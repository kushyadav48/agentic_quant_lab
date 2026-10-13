"""Explicit one-inference non-sensitive smoke. Never invoked by API startup or pytest."""
import argparse
import asyncio
import json
from time import perf_counter
from typing import Literal

from .cloud import CloudError, CloudSettings
from .gemini import GeminiProvider
from .models import (_Contract, InvocationPolicy, LLMRequest, Message, PromptProvenance, TextContent)
from .enums import MessageRole
from .client import LLMClient
from .structured import generate_structured, structured_output
from .errors import LLMError


class SmokeOutput(_Contract):
    schema_version: Literal[1]
    acknowledgement: Literal["non-sensitive research smoke"]


async def run(settings):
    provider = GeminiProvider(settings)
    request = LLMRequest(identity=provider.info.identity, max_output_tokens=128,
        provenance=PromptProvenance(prompt_id="quantlab.cloud-smoke", prompt_version="1"),
        messages=(Message(role=MessageRole.USER, content=(TextContent(text=
            'Return schema_version 1 and acknowledgement "non-sensitive research smoke". This is a synthetic connectivity test.'),)),),
        structured_output=structured_output(SmokeOutput))
    start = perf_counter()
    _, invocation = await generate_structured(LLMClient(provider, policy=InvocationPolicy(
        max_attempts=1, max_output_tokens=128)), request, SmokeOutput)
    return {"status": "PASS", "provider": invocation.response.identity.provider_id,
        "model": invocation.response.identity.model_id, "schema_version": 1,
        "usage": invocation.response.usage.model_dump(), "requests_reserved": provider.budget.used,
        "elapsed_ms": round((perf_counter() - start) * 1000, 3),
        "financial_mutations": 0, "approval": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly permit one cloud inference after configuration checks")
    args = parser.parse_args()
    try:
        settings = CloudSettings.from_env()
        code = settings.rejection()
        if not args.run or code:
            print(json.dumps({"status": "PENDING", "reason": code or "explicit_run_required"}))
            return 2
        print(json.dumps(asyncio.run(run(settings))))
        return 0
    except CloudError as error:
        print(json.dumps({"status": "FAIL", "reason": error.code}))
    except (LLMError, RuntimeError):
        print(json.dumps({"status": "FAIL", "reason": "configuration_or_response_invalid"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
