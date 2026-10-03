"""Strict JSON transport; no repair, evaluation, or remote schema resolution."""
import json
import math


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON number")
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def load_json(text: str) -> object:
    return json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float,
                      object_pairs_hook=_unique_object)


def canonical_schema(text: str) -> str:
    value = load_json(text)
    if not isinstance(value, dict):
        raise ValueError("JSON Schema must be an object")
    # Same sorted, compact ASCII JSON convention as existing content identities.
    # Schema transport contains JSON values only, never Python model classes.
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)
