"""Fresh arithmetic contexts independent of caller and decimal.DefaultContext."""
from decimal import Context, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN


def deterministic_context(*, prec: int = 34) -> Context:
    """Keep standard exponent bounds/traps and the caller's context untouched.

    Invalid arithmetic, division by zero and overflow raise; ordinary rounding
    and inexact results remain permitted, as in the project's original contexts.
    A fresh context also starts with clear flags and cannot leak mutable settings.
    """
    return Context(prec=prec, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                   capitals=1, clamp=0, flags=[],
                   traps=[InvalidOperation, DivisionByZero, Overflow])
