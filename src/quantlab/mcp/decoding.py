"""Finite JSON wire checking and strict canonical decoding for query adapters."""

import json
from typing import TypeVar

from pydantic import JsonValue, TypeAdapter, ValidationError

from .models import AdapterIssue, MCPBoundaryModel


Request = TypeVar("Request", bound=MCPBoundaryModel)
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue], config=MCPBoundaryModel.model_config)


def encode_json_object(raw: object) -> str:
    """Check finite JSON wire types without using a strategy-specific contract.

    Validate before encoding: json.dumps alone silently converts tuples and
    non-string dictionary keys. No custom serializers or domain repair belong here.
    """
    checked = _JSON_OBJECT.validate_python(raw, strict=True)
    return json.dumps(checked, allow_nan=False, sort_keys=True)


def decode_request(
    raw: object, contract: type[Request],
) -> tuple[Request | None, tuple[AdapterIssue, ...]]:
    """No Python coercion or domain repair; external data must already be JSON."""

    try:
        wire = encode_json_object(raw)
    except (ValidationError, TypeError, ValueError, RecursionError):
        return None, (AdapterIssue(
            code="invalid_json_request",
            message="Request must be a JSON object containing only finite JSON values.",
        ),)

    try:
        return contract.model_validate_json(wire, strict=True), ()
    except ValidationError as exc:
        # Union branch labels and unknown keys can contain submitted values.
        # Retain only locations declared by the schema and numeric array indices.
        schema = contract.model_json_schema()
        safe_names = set(schema.get("properties", {})) | set(schema.get("$defs", {}))
        for definition in schema.get("$defs", {}).values():
            safe_names.update(definition.get("properties", {}))
        issues = []
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            location = tuple(
                str(part) if type(part) is int or part in safe_names else "<field>"
                for part in error["loc"]
            )
            if error["type"] == "extra_forbidden":
                location = (*location[:-1], "<extra>")
            issues.append(AdapterIssue(
                location=location, code=error["type"],
                message="Input violates the deterministic query contract.",
            ))
        return None, tuple(sorted(issues, key=lambda issue: (issue.location, issue.code)))


def service_issue() -> tuple[AdapterIssue, ...]:
    """Expected public-service input rejection, with no exception text."""

    return (AdapterIssue(
        code="service_input_error",
        message="Input rejected by the deterministic service.",
    ),)
