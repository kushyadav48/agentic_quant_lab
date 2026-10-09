"""Offline deterministic entry execution and prefunded research accounting."""
from .errors import PaperError, PaperFundingError, PaperIdentityConflict, PaperInputError, PaperPricingError
from .models import (
    CancellationOutcome, CancellationRequest, FillRecord, KernelConfig, KernelSnapshot,
    MarketDelivery, OrderSide, OrderState, OrderSubmission, OrderTransition, RiskOutcome,
    stable_id,
)
from .orders import PaperOrderKernel

__all__ = [
    "PaperOrderKernel", "KernelConfig", "KernelSnapshot", "MarketDelivery",
    "OrderSide", "OrderState", "OrderSubmission", "OrderTransition",
    "CancellationRequest", "CancellationOutcome", "FillRecord", "RiskOutcome",
    "PaperError", "PaperFundingError", "PaperInputError", "PaperIdentityConflict", "PaperPricingError", "stable_id",
]

from .account_models import (
    AccountConfig, AccountEvent, AccountPosition, AccountSnapshot, ApplyFill,
    FundReservation, MarkAccount, ReleaseFunds, ReserveFunds,
)
from .accounting import initialize_account, transition_account
from .accounts import PaperAccount

__all__ += [
    "AccountConfig", "AccountEvent", "AccountPosition", "AccountSnapshot", "ApplyFill",
    "FundReservation", "MarkAccount", "ReleaseFunds", "ReserveFunds",
    "initialize_account", "transition_account", "PaperAccount",
]

from .admission import AdmissionError, ResearchEvidenceStore, admit_strategy
from .runtime import StrategyRuntime
from .strategy_orders import StrategyOrderAdapter
from .strategy_models import (
    AdmissionRecord, BarCloseDelivery, DatasetVersion, EligibilityDecision,
    EligibilityPolicy, EntryIntent, EvidenceReference, OpeningDelivery,
    ResearchEvidence, RuntimeSnapshot, StrategyDecision, StrategySessionConfig, StrategyOrderSnapshot,
)

__all__ += [
    "AdmissionError", "ResearchEvidenceStore", "admit_strategy", "StrategyRuntime",
    "StrategyOrderAdapter", "AdmissionRecord", "BarCloseDelivery", "DatasetVersion",
    "EligibilityDecision", "EligibilityPolicy", "EntryIntent", "EvidenceReference",
    "OpeningDelivery", "ResearchEvidence", "RuntimeSnapshot", "StrategyDecision",
    "StrategySessionConfig", "StrategyOrderSnapshot",
]

from .session_models import (ClockState, FeedProvenance, FeedState, ReplayConfig, ReplayEvent,
    SessionCommand, SessionRecord, SessionSnapshot, SessionState, StaleFeedPolicy)
from .sessions import PaperSession

__all__ += ["ClockState", "FeedProvenance", "FeedState", "ReplayConfig", "ReplayEvent",
    "SessionCommand", "SessionRecord", "SessionSnapshot", "SessionState", "StaleFeedPolicy",
    "PaperSession"]

from .models import (AdvancedKernelConfig, AdvancedOrderSubmission, AdvancedFillRecord,
    OrderActivation, StopTrigger, PendingCancellation, CancellationAcknowledgement)
from .account_models import AdvancedApplyFill
from .session_models import AdvancedReplayConfig, AdvancedSessionCommand

__all__ += ["AdvancedKernelConfig", "AdvancedOrderSubmission", "AdvancedFillRecord",
    "OrderActivation", "StopTrigger", "PendingCancellation", "CancellationAcknowledgement",
    "AdvancedApplyFill", "AdvancedReplayConfig", "AdvancedSessionCommand"]

from .oco_models import OCOCommand, OCOProgress, OCOEvent, OCOSnapshot
from .models import OCOKernelConfig, OCOOrderSubmission, OCOFillRecord, OCOQuantityAdjustment, OCOKernelProgress
from .account_models import OCOApplyFill

__all__ += ["OCOCommand", "OCOProgress", "OCOEvent", "OCOSnapshot", "OCOKernelConfig",
    "OCOOrderSubmission", "OCOFillRecord", "OCOQuantityAdjustment", "OCOKernelProgress", "OCOApplyFill"]

from .strategy_models import (AdvancedEntryPolicy, AdvancedEntryApproval, AdvancedEligibilityPolicy,
    AdvancedEligibilityDecision, AdvancedStrategySessionConfig, AdvancedEntryIntent)
from .session_models import AdvancedEntryReplayConfig, EntryCancellationCommand

__all__ += ["AdvancedEntryPolicy", "AdvancedEntryApproval", "AdvancedEligibilityPolicy",
    "AdvancedEligibilityDecision", "AdvancedStrategySessionConfig", "AdvancedEntryIntent",
    "AdvancedEntryReplayConfig", "EntryCancellationCommand"]
