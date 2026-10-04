"""Text and chart ideas to reviewable, unapproved Phase 5 strategy proposals."""
from .errors import (
    InterpretedStrategyError, InterpretationContractError, InterpretationError,
    InterpretationInputError,
)
from .models import (
    Clarification, InterpretationInput, InterpretationResult, InterpretationStatus,
    SourceEvidence, StrategyDraft, StructuredInterpretation,
)
from .prompts import PROMPT_ID, PROMPT_VERSION, build_request
from .service import interpret_strategy
from .multimodal import interpret_multimodal_strategy
from .multimodal_models import (
    MultimodalConflict, MultimodalInput, MultimodalInterpretation, MultimodalResult, VisualEvidence,
)
from .multimodal_prompts import (
    PROMPT_ID as MULTIMODAL_PROMPT_ID, PROMPT_VERSION as MULTIMODAL_PROMPT_VERSION,
    build_multimodal_request,
)

__all__ = [
    "Clarification", "InterpretedStrategyError", "InterpretationContractError",
    "InterpretationError", "InterpretationInput", "InterpretationInputError",
    "InterpretationResult", "InterpretationStatus", "PROMPT_ID", "PROMPT_VERSION",
    "SourceEvidence", "StrategyDraft", "StructuredInterpretation", "build_request",
    "interpret_strategy",
    "MultimodalConflict", "MultimodalInput", "MultimodalInterpretation", "MultimodalResult",
    "VisualEvidence", "MULTIMODAL_PROMPT_ID", "MULTIMODAL_PROMPT_VERSION",
    "build_multimodal_request", "interpret_multimodal_strategy",
]
