"""Official Google Gen AI SDK adapter. No tools, files, search or fallback."""
import asyncio
from contextlib import asynccontextmanager
import json
from decimal import Decimal, InvalidOperation
import re

from .cloud import CloudBudget, CloudError, CloudSettings
from .enums import FinishReason, MessageRole
from .models import (Capabilities, LLMRequest, LLMResponse, ModelIdentity,
                     ProviderInfo, TextContent, TokenUsage)


def _bounded_output(text, *, secret):
    """Resource preflight before Pydantic/strategy Decimal construction or digesting."""
    try:
        def reject_float(_):
            # Pydantic JSON numbers pass through binary floating-point conversion.
            # Monetary/threshold values must use the existing exact string encoding.
            raise CloudError("invalid_model_response")
        value = json.loads(text, parse_float=reject_float)
    except json.JSONDecodeError:
        return  # The existing strict structured validator owns syntax diagnostics.
    except (ValueError, RecursionError):
        raise CloudError("invalid_model_response") from None
    def visit(item, depth=0):
        if depth > 64:
            raise CloudError("invalid_model_response")
        if isinstance(item, dict):
            for child in item.values():
                visit(child, depth+1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth+1)
        elif isinstance(item, str):
            if secret in item:
                raise CloudError("invalid_model_response")
            try:
                number = Decimal(item)
            except InvalidOperation:
                if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", item.strip().replace("_", "")):
                    raise CloudError("invalid_model_response") from None
                return
            if not number.is_finite():
                raise CloudError("invalid_model_response")
            _, digits, exponent = number.as_tuple()
            if len(item) > 8192 or max(len(digits)+exponent, -exponent) > 4096:
                raise CloudError("invalid_model_response")
    visit(value)


@asynccontextmanager
async def sdk_client(settings):
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise CloudError("provider_unavailable") from None
    import httpx
    # Explicit transport prevents an installed aiohttp extra from selecting the
    # SDK's connection-error retry/logging path independently of retry_options.
    async with httpx.AsyncClient(timeout=settings.timeout_ms / 1000,
            transport=httpx.AsyncHTTPTransport(retries=0)) as transport_client:
        client = genai.Client(api_key=settings.api_key.get_secret_value(), vertexai=False,
            http_options=types.HttpOptions(timeout=settings.timeout_ms,
                httpx_async_client=transport_client,
                retry_options=types.HttpRetryOptions(attempts=1)))
        try:
            async with client.aio as asynchronous:
                yield asynchronous
        finally:
            client.close()


def normalize_error(error):
    import httpx
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "provider_timeout"
    code = getattr(error, "code", None)
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        envelope = details.get("error", details)
        details = envelope.get("details", []) if isinstance(envelope, dict) else []
    details = details if isinstance(details, list) else []
    if code == 400 and any(isinstance(d, dict)
            and str(d.get("@type", "")).endswith("ErrorInfo")
            and d.get("reason") in ("API_KEY_INVALID", "API_KEY_EXPIRED") for d in details):
        return "credentials_invalid"
    if code in (401, 403):
        return "credentials_invalid"
    if code == 429:
        if any(isinstance(d, dict) and str(d.get("@type", "")).endswith("QuotaFailure") for d in details):
            return "quota_exhausted"
        return "rate_limited"
    return "provider_unavailable"


class GeminiProvider:
    def __init__(self, settings: CloudSettings, *, budget=None, client_factory=sdk_client):
        self.settings = CloudSettings.model_validate(settings)
        self.budget = budget or CloudBudget(self.settings)
        self._factory = client_factory
        self._info = ProviderInfo(identity=ModelIdentity(provider_id=settings.provider_id,
            model_id=settings.model_id or "unconfigured"), capabilities=Capabilities(structured_output=True))

    @property
    def info(self):
        return self._info

    async def generate(self, request: LLMRequest) -> LLMResponse:
        request = LLMRequest.model_validate(request)
        settings = self.settings
        if request.identity != self.info.identity or request.structured_output is None:
            raise CloudError("cloud_input_rejected")
        if any(not isinstance(c, TextContent) for m in request.messages for c in m.content):
            raise CloudError("cloud_input_rejected")
        if request.max_output_tokens > settings.max_output_tokens:
            raise CloudError("cloud_input_rejected")
        system = "\n".join(c.text for m in request.messages if m.role is MessageRole.SYSTEM for c in m.content)
        contents = [{"role": "model" if m.role is MessageRole.ASSISTANT else "user",
            "parts": [{"text": c.text} for c in m.content]} for m in request.messages if m.role is not MessageRole.SYSTEM]
        wire = json.dumps(contents, ensure_ascii=False) + system + request.structured_output.json_schema
        if (len(wire.encode("utf-8")) > settings.max_prompt_bytes
                or settings.api_key and settings.api_key.get_secret_value() in wire):
            raise CloudError("cloud_input_rejected")
        self.budget.acquire()
        try:
            async with asyncio.timeout(settings.timeout_ms / 1000):
                async with self._factory(settings) as client:
                    # Developer API countTokens does not accept system_instruction.
                    # Count the same text as an additional message; reserve framing.
                    counted = await client.models.count_tokens(model=settings.model_id,
                        contents=([{"role": "user", "parts": [{"text": system}]}] if system else []) + contents)
                    count = counted.total_tokens
                    schema_allowance = len(request.structured_output.json_schema.encode("utf-8"))
                    if type(count) is not int or count < 0 or count + schema_allowance + 64 > settings.max_input_tokens:
                        raise CloudError("cloud_input_rejected")
                    config = {"candidate_count": 1,
                        "max_output_tokens": request.max_output_tokens,
                        "response_mime_type": "application/json",
                        "response_json_schema": json.loads(request.structured_output.json_schema),
                        "automatic_function_calling": {"disable": True}}
                    if system:
                        config["system_instruction"] = system
                    for name in ("temperature", "top_p", "seed"):
                        value = getattr(request.parameters, name)
                        if value is not None:
                            config[name] = int(value) if name == "seed" else float(value)
                    response = await client.models.generate_content(model=settings.model_id, contents=contents, config=config)
            candidates = response.candidates
            if not candidates or len(candidates) != 1 or candidates[0].finish_reason.value != "STOP":
                raise CloudError("invalid_model_response")
            content = candidates[0].content
            if content is None or not content.parts or any(p.function_call is not None for p in content.parts):
                raise CloudError("invalid_model_response")
            text = "".join(p.text for p in content.parts if p.text and not p.thought)
            if (not text or len(text.encode("utf-8")) > settings.max_response_bytes
                    or settings.api_key.get_secret_value() in text):
                raise CloudError("invalid_model_response")
            _bounded_output(text, secret=settings.api_key.get_secret_value())
            usage = response.usage_metadata
            if usage is None or type(usage.prompt_token_count) is not int or type(usage.total_token_count) is not int:
                raise CloudError("invalid_model_response")
            output = usage.total_token_count - usage.prompt_token_count
            if output < 0 or output > request.max_output_tokens or usage.prompt_token_count > settings.max_input_tokens:
                raise CloudError("invalid_model_response")
            return LLMResponse(identity=self.info.identity, text=text, finish_reason=FinishReason.STOP,
                usage=TokenUsage(input_tokens=usage.prompt_token_count, output_tokens=output,
                    total_tokens=usage.total_token_count))
        except CloudError:
            raise
        except Exception as error:
            raise CloudError(normalize_error(error)) from None
        finally:
            self.budget.release()
