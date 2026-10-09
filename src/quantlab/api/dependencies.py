"""Strict JSON-mode decoding and dependency-injected service access."""
import json
from decimal import Decimal, InvalidOperation, localcontext
import re

from fastapi import Request
from pydantic import ValidationError
from starlette.responses import Response

from quantlab.mcp.canonical import canonical_json
from .errors import APIError
from .schemas import ValidationIssue

INPUT_SCHEMAS = {}
_NUMERIC_SYNTAX = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def get_lane(request: Request):
    lane = getattr(request.app.state, "lane", None)
    if not getattr(request.app.state, "ready", False) or lane is None:
        raise APIError(503, "not_ready")
    return lane


def body_contract(model):
    """JSON mode accepts wire Decimal strings, UTC timestamps, enums and arrays.

    FastAPI's default Python-mode strict validation would reject those valid
    existing wire contracts. Decode original bytes, never round through float.
    The identical model's validation schema is registered for OpenAPI.
    """
    async def decode(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise APIError(415, "unsupported_media_type")
        wire = await request.body()
        return await get_lane(request).call(lambda: validate_body(model, wire))
    return decode


def validate_body(model, wire):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value
    def forbidden(_):
        raise ValueError("Decimal values require strings")
    def bounded(value, depth=0):
        if depth > 64:
            raise ValueError("JSON nesting exceeds bound")
        if isinstance(value, dict):
            for item in value.values():
                bounded(item, depth+1)
        elif isinstance(value, list):
            for item in value:
                bounded(item, depth+1)
        elif isinstance(value, str):
            # Strategy approval/digest validators can expand Decimal strings
            # before ResearchRequest's canonical admission bound executes.
            # Guard sparse exponents and long coefficients before ANY domain
            # construction, including stateless reader validation requests.
            try:
                # Match Decimal's accepted spelling (including whitespace,
                # underscores and Unicode digits), rather than a narrower
                # regex that an untrusted numeric string could bypass.
                with localcontext() as context:
                    context.traps[InvalidOperation] = True
                    decimal = Decimal(value)
            except InvalidOperation:
                if _NUMERIC_SYNTAX.fullmatch(value.strip().replace("_", "")):
                    raise ValueError("numeric string cannot be represented") from None
                return  # Opaque text is left to its original domain schema.
            if not decimal.is_finite():
                return  # Domain scalar schemas already reject nonfinite values.
            _, digits, exponent = decimal.as_tuple()
            if len(value) > 8192 or max(len(digits)+exponent, -exponent) > 4096:
                raise ValueError("numeric string expansion exceeds bound")
    try:
        value = json.loads(wire, object_pairs_hook=pairs, parse_float=forbidden, parse_constant=forbidden)
        bounded(value)
        return model.model_validate_json(wire, strict=True)
    except ValidationError as exc:
        schema = model.model_json_schema()
        names = set(schema.get("properties", {}))
        for definition in schema.get("$defs", {}).values():
            names.update(definition.get("properties", {}))
        issues = tuple(ValidationIssue(location=tuple(str(part) if type(part) is int
            or part in names else "<field>" for part in error["loc"]))
            for error in exc.errors(include_input=False, include_context=False, include_url=False)[:16])
        raise APIError(422, "invalid_request", issues) from None
    except (ValueError, TypeError, RecursionError):
        raise APIError(422, "invalid_request") from None


def request_schema(model):
    schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    def integer_wire_numbers(value):
        if isinstance(value, dict):
            if value.get("type") == "number":
                value["type"] = "integer"
            for child in value.values():
                integer_wire_numbers(child)
        elif isinstance(value, list):
            for child in value:
                integer_wire_numbers(child)
    integer_wire_numbers(schema)
    INPUT_SCHEMAS.update(schema.pop("$defs", {}))
    INPUT_SCHEMAS[model.__name__] = schema
    return {"requestBody": {"required": True, "content": {"application/json": {
        "schema": {"$ref": f"#/components/schemas/{model.__name__}"}}}}}


def wire_response(value):
    """Serialize on the service lane; preserve Decimals with bounded expansion."""
    try:
        wire = canonical_json(value.model_dump(mode="python"), max_bytes=12_582_912)
    except ValueError:
        raise APIError(503, "response_too_large") from None
    return Response(wire, media_type="application/json")
