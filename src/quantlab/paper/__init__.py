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
