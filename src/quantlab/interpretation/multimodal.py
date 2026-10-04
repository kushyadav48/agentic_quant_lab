"""Chart/text interpretation: binding checks followed by the shared Phase 14 converter."""
from pydantic import ValidationError

from quantlab.llm import LLMClient, ModelIdentity, generate_structured, validate_structured
from quantlab.strategies import Origin, Provenance, StrategySpecification

from .errors import InterpretationContractError
from .models import InterpretationStatus
from .multimodal_models import MultimodalInput, MultimodalInterpretation, MultimodalResult
from .multimodal_prompts import PROMPT_ID, PROMPT_VERSION, build_multimodal_request, validate_input
from .service import _convert_draft, _required_evidence


def _proposal(value: MultimodalInput, output: MultimodalInterpretation) -> StrategySpecification | None:
    asset_ids = {image.asset_id for image in value.images}
    text_evidence = (*output.evidence, *(e for c in output.conflicts for e in c.text_evidence))
    visual_evidence = (*output.visual_evidence, *(e for c in output.conflicts for e in c.visual_evidence))
    if (any(e.asset_id not in asset_ids for e in visual_evidence)
            or any(value.strategy_text is None or e.quote not in value.strategy_text for e in text_evidence)):
        raise InterpretationContractError()
    if output.status is InterpretationStatus.NEEDS_CLARIFICATION:
        return None
    if {e.field for e in (*output.evidence, *output.visual_evidence)} != _required_evidence(output.draft):
        raise InterpretationContractError()
    return _convert_draft(output.draft, strategy_id=value.strategy_id, version=value.version,
        provenance=Provenance(origin=Origin.CHART_MULTIMODAL, source_reference=value.input_reference,
            notes=f"Interpretation prompt {PROMPT_ID} version {PROMPT_VERSION}"))


def _check_result(result: MultimodalResult) -> None:
    invocation = result.invocation
    expected = build_multimodal_request(result.input, identity=invocation.request.identity,
                                        max_output_tokens=invocation.request.max_output_tokens)
    if invocation.request != expected:
        raise InterpretationContractError()
    parsed = validate_structured(invocation.response, MultimodalInterpretation)
    if parsed != result.interpretation or result.proposal != _proposal(result.input, parsed):
        raise InterpretationContractError()


async def interpret_multimodal_strategy(client: LLMClient, value: MultimodalInput, *,
        identity: ModelIdentity, max_output_tokens: int = 4096) -> MultimodalResult:
    value = validate_input(value)
    request = build_multimodal_request(value, identity=identity, max_output_tokens=max_output_tokens)
    output, invocation = await generate_structured(client, request, MultimodalInterpretation)
    try:
        output = MultimodalInterpretation.model_validate(output)
    except ValidationError:
        raise InterpretationContractError() from None
    return MultimodalResult(input=value, interpretation=output,
        proposal=_proposal(value, output), invocation=invocation)
