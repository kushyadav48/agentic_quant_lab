"""One structured invocation, deterministic domain conversion, no approval authority."""
from typing import get_args

from pydantic import ValidationError

from quantlab.llm import (
    LLMClient, ModelIdentity, generate_structured, validate_structured,
)
from quantlab.strategies import Origin, Provenance, StrategyContent, StrategySpecification

from .errors import InterpretedStrategyError, InterpretationContractError
from .models import (
    EvidenceField, InterpretationInput, InterpretationResult, InterpretationStatus,
    StrategyDraft, StructuredInterpretation,
)
from .prompts import PROMPT_ID, PROMPT_VERSION, build_request, validate_input
from .vocabulary import validate_vocabulary


def _required_evidence(draft: StrategyDraft) -> set[str]:
    return {field for field in get_args(EvidenceField) if getattr(draft, field) not in (None, ())}


def _proposal(value: InterpretationInput, output: StructuredInterpretation) -> StrategySpecification | None:
    if output.status is InterpretationStatus.NEEDS_CLARIFICATION:
        return None
    draft = output.draft
    required = _required_evidence(draft)
    if (set(e.field for e in output.evidence) != required
            or any(e.quote not in value.strategy_text for e in output.evidence)):
        raise InterpretationContractError()
    return _convert_draft(draft, strategy_id=value.strategy_id, version=value.version,
        provenance=Provenance(origin=Origin.NATURAL_LANGUAGE, source_reference=value.input_reference,
            notes=f"Interpretation prompt {PROMPT_ID} version {PROMPT_VERSION}"))


def _convert_draft(draft: StrategyDraft, *, strategy_id: str, version: int,
                   provenance: Provenance) -> StrategySpecification:
    """Shared text/vision conversion; Phase 5 and the Phase 14 allowlist own semantics."""
    try:
        # Constructors recursively revalidate all nested Phase 5 objects; no copy
        # update, bypass construction, coercion, expression evaluation or repair.
        content = StrategyContent(**draft.model_dump(mode="python"), provenance=provenance)
        validate_vocabulary(content)
        return StrategySpecification(strategy_id=strategy_id, version=version,
                                     content=content)
    except (ValueError, TypeError, OverflowError):
        raise InterpretedStrategyError() from None


def _check_result(result: InterpretationResult) -> None:
    """Reject forged/copied audit records, including unrelated invocation artifacts."""
    invocation = result.invocation
    expected = build_request(result.input, identity=invocation.request.identity,
                             max_output_tokens=invocation.request.max_output_tokens)
    if invocation.request != expected:
        raise InterpretationContractError()
    parsed = validate_structured(invocation.response, StructuredInterpretation)
    if parsed != result.interpretation or result.proposal != _proposal(result.input, parsed):
        raise InterpretationContractError()


async def interpret_strategy(client: LLMClient, value: InterpretationInput, *,
                             identity: ModelIdentity, max_output_tokens: int = 4096) -> InterpretationResult:
    """Provider retries/limits/errors belong exclusively to the Phase 13 client."""
    value = validate_input(value)
    request = build_request(value, identity=identity, max_output_tokens=max_output_tokens)
    output, invocation = await generate_structured(client, request, StructuredInterpretation)
    # Explicit deep revalidation also protects this boundary from unchecked
    # instances supplied by integrations replacing the structured helper.
    try:
        output = StructuredInterpretation.model_validate(output)
    except ValidationError:
        raise InterpretationContractError() from None
    return InterpretationResult(input=value, interpretation=output,
        proposal=_proposal(value, output), invocation=invocation)
