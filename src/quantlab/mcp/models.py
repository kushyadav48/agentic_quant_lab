"""Phase 17 MCP-specific boundary models.

Domain models continue to live in their existing Quant Lab packages.
Only models genuinely specific to the MCP boundary belong here.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class MCPBoundaryModel(BaseModel):
    """Strict base model for MCP-only contracts."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
        allow_inf_nan=False,
    )


class StrategyValidationRequest(MCPBoundaryModel):
    """Raw JSON-compatible strategy content submitted for validation."""

    content: dict[str, JsonValue]


class StrategyValidationIssue(MCPBoundaryModel):
    """Sanitized validation issue exposed at the MCP boundary."""

    location: tuple[str, ...] = ()
    code: str
    message: str


class StrategyValidationResult(MCPBoundaryModel):
    """Result of deterministic StrategyContent validation."""

    valid: bool
    content_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        min_length=64,
        max_length=64,
    )
    issues: tuple[StrategyValidationIssue, ...] = ()

    @model_validator(mode="after")
    def check_result(self) -> Self:
        if self.valid:
            if self.content_digest is None or self.issues:
                raise ValueError("valid content requires a digest and no issues")
        elif self.content_digest is not None or not self.issues:
            raise ValueError("invalid content requires issues and no digest")
        return self
