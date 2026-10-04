"""Image/evidence extensions to Phase 14; no second strategy schema or lifecycle."""
from typing import Annotated, Self

from pydantic import AfterValidator, Field, model_validator

from quantlab.llm import ImageReference, InvocationResult
from quantlab.llm.models import OpaqueID
from quantlab.strategies import StrategySpecification
from quantlab.strategies.schema import Identifier, Reference

from .models import (
    Clarification, EvidenceField, InterpretationStatus, Note, SourceEvidence,
    StrategyText, StructuredInterpretation, _Contract,
)


def _opaque_asset(value: str) -> str:
    # Opaque IDs may contain namespace colons, but are never transport locations.
    if ("/" in value or "\\" in value or value.lower().startswith(("data:", "file:",
            "http:", "https:", "ftp:"))):
        raise ValueError("image asset must be an opaque ID, not a URL or path")
    return value


AssetID = Annotated[OpaqueID, AfterValidator(_opaque_asset)]


class MultimodalInput(_Contract):
    images: tuple[ImageReference, ...] = Field(min_length=1, max_length=16, repr=False)
    strategy_text: StrategyText | None = Field(default=None, repr=False)
    input_reference: Reference
    strategy_id: Identifier
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def check_images(self) -> Self:
        ids = tuple(_opaque_asset(image.asset_id) for image in self.images)
        if len(set(ids)) != len(ids):
            raise ValueError("image asset IDs must be unique")
        return self


class VisualEvidence(_Contract):
    """An unverified visual claim supporting a material field, solely for review."""
    field: EvidenceField
    asset_id: AssetID = Field(repr=False)
    observation: Note = Field(repr=False)


class MultimodalConflict(Clarification):
    """Reported disagreement; never resolved by choosing a source automatically."""
    field: EvidenceField
    text_evidence: tuple[SourceEvidence, ...] = Field(max_length=32)
    visual_evidence: tuple[VisualEvidence, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def check_sources(self) -> Self:
        if any(e.field != self.field for e in (*self.text_evidence, *self.visual_evidence)):
            raise ValueError("conflict evidence must support its field")
        if not self.text_evidence and len({e.asset_id for e in self.visual_evidence}) < 2:
            raise ValueError("conflict requires text/image or multiple image sources")
        return self


class MultimodalInterpretation(StructuredInterpretation):
    """Same draft/status semantics, with visual review evidence and explicit conflicts."""
    visual_evidence: tuple[VisualEvidence, ...] = Field(max_length=64)
    conflicts: tuple[MultimodalConflict, ...] = Field(max_length=32)

    def _has_evidence(self) -> bool:
        return bool(self.evidence or self.visual_evidence)

    @model_validator(mode="after")
    def check_visual_status(self) -> Self:
        if self.status is InterpretationStatus.READY:
            if not self.visual_evidence or self.conflicts:
                raise ValueError("READY requires visual evidence and forbids unresolved conflicts")
        elif any(Clarification(ambiguity=c.ambiguity, question=c.question) not in self.clarifications
                 for c in self.conflicts):
            raise ValueError("each conflict must appear in clarification questions")
        return self


class MultimodalResult(_Contract):
    """Replay-checkable consistency record; not provider authentication or market data."""
    input: MultimodalInput = Field(repr=False)
    interpretation: MultimodalInterpretation = Field(repr=False)
    proposal: StrategySpecification | None = Field(repr=False)
    invocation: InvocationResult = Field(repr=False)

    @model_validator(mode="after")
    def check_binding(self) -> Self:
        from .multimodal import _check_result
        _check_result(self)
        return self

    @property
    def status(self) -> InterpretationStatus:
        return self.interpretation.status
