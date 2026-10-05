"""The narrow MCP 2.3 registration seam required by our JSON boundary.

The public decorator has no option to disable stringified-object parsing or
replace argument validation. Tool is an exported constructor input, but its
FuncMetadata hook is a lower-level SDK dependency. Keep that dependency here:
we override only argument validation, retaining SDK execution, output validation,
serialization and unexpected-error handling. Behavioral SDK tests guard it.
"""

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver.tools import Tool
from mcp.server.mcpserver.utilities.func_metadata import FuncMetadata
from pydantic import ValidationError, create_model

from .models import MCPBoundaryModel


class _RawArgumentsMetadata(FuncMetadata):
    """Preserve raw values for finite JSON checking and strict domain decoding."""

    def validate_arguments(self, arguments_to_validate: dict[str, Any]) -> dict[str, Any]:
        if (type(arguments_to_validate) is not dict
                or set(arguments_to_validate) != set(self.arg_model.model_fields)):
            # Tool.run recognizes ValidationError as an anticipated failure.
            # Fixed metadata avoids echoing values, unknown keys or tracebacks.
            raise ValidationError.from_exception_data("ToolArguments", [{
                "type": "value_error", "loc": (), "input": None,
                "ctx": {"error": ValueError("Invalid tool arguments.")},
            }], hide_input=True)
        return arguments_to_validate.copy()


def _portable_schema(schema: Any) -> None:
    """Translate Rust's strict end anchor to an equivalent JSON Schema regex.

    A plain dollar anchor accepts a final newline. This negative lookahead
    forbids any remaining character; canonical domain validators are unchanged.
    """
    if isinstance(schema, dict):
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and pattern.endswith(r"\z"):
            schema["pattern"] = pattern[:-2] + r"(?![\s\S])"
        for value in schema.values():
            _portable_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            _portable_schema(value)


def create_tool(
    fn: Callable[..., MCPBoundaryModel],
    *,
    request_contract: type[MCPBoundaryModel] | None = None,
) -> Tool:
    """Publish canonical schemas while letting the adapter report input issues.

    Standard Pydantic model generation handles definition hoisting and references.
    The schema describes accepted input; it does not replace semantic validation.
    """
    tool = Tool.from_function(fn, structured_output=True)
    if request_contract is not None:
        arguments = create_model(
            f"{request_contract.__name__}Arguments",
            __base__=MCPBoundaryModel,
            request=(request_contract, ...),
        )
        tool.parameters = arguments.model_json_schema()
    tool.parameters["additionalProperties"] = False
    metadata = tool.fn_metadata
    _portable_schema(tool.parameters)
    _portable_schema(metadata.output_schema)
    tool.fn_metadata = _RawArgumentsMetadata(
        arg_model=metadata.arg_model,
        output_schema=metadata.output_schema,
        output_model=metadata.output_model,
        wrap_output=metadata.wrap_output,
    )
    return tool
