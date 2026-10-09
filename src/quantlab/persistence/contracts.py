"""Strict versioned disk contracts, using the existing financial canonical codec."""
from datetime import timedelta
import json
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_serializer
from quantlab.data.models import UtcTimestamp
from quantlab.strategies.schema import Digest
from quantlab.paper.models import Identity, KernelSnapshot, KernelProgress, PaperContract
from quantlab.paper.oco_models import OCOProgress
from quantlab.paper.account_models import AccountCommand, AccountSnapshot
from quantlab.paper.session_models import ClockState, FeedState, SessionState
from quantlab.paper.strategy_models import EntryIntent, AdvancedEntryIntent


class PersistenceError(ValueError):
    """Stable diagnostics; arbitrary SQLite/validation messages are not reasons."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class RecoveryRequired(PersistenceError):
    pass


def decode(cls, wire):
    """JSON mode retains strict Decimal/string and enum/tuple conversions.

    Refuse duplicate keys, numbers expressed as floats, nonfinite values and
    noncanonical spelling. Only the core canonical timedelta object is adapted.
    No pickle, dynamic imports, arbitrary object hooks or type names from disk.
    """
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def forbidden(value):
        raise ValueError("noncanonical JSON number")

    adapted = False

    def durations(value):
        nonlocal adapted
        if isinstance(value, dict):
            if set(value) == {"days", "seconds", "microseconds"}:
                if any(type(v) is not int for v in value.values()):
                    raise ValueError("invalid duration")
                adapted = True
                return json.loads(TypeAdapter(timedelta).dump_json(timedelta(**value)))
            return {k: durations(v) for k, v in value.items()}
        if isinstance(value, list):
            return [durations(v) for v in value]
        return value

    try:
        if type(wire) is not str or len(wire.encode("utf-8")) > 4_194_304:
            raise ValueError("invalid encoded record")
        data = json.loads(wire, object_pairs_hook=pairs, parse_float=forbidden,
                          parse_constant=forbidden)
        converted = durations(data)
        # Keep the original validated wire when no duration adaptation is needed.
        result = cls.model_validate_json(json.dumps(converted) if adapted else wire)
        if result.canonical_json() != wire:
            raise ValueError("noncanonical stored record")
        return result
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise PersistenceError("invalid_record") from exc


class StoragePolicy(PaperContract):
    schema_version: Literal[1] = 1
    checkpoint_interval: Annotated[int, Field(ge=0, le=100_000)] = 1000
    retained_checkpoints: Annotated[int, Field(ge=1, le=8)] = 2
    durability: Literal["sqlite-delete-extra-exclusive-v1"] = "sqlite-delete-extra-exclusive-v1"


class ExitState(PaperContract):
    exit_kernel: KernelProgress | KernelSnapshot | None = None
    oco: OCOProgress | None = None

    @model_serializer(mode="wrap")
    def legacy_wire(self, handler):
        body = handler(self)
        if self.exit_kernel is None:
            body.pop("exit_kernel", None)
        if self.oco is None:
            body.pop("oco", None)
        return body


class Effects(ExitState):
    """Bounded generated state, not another accounting authority."""
    schema_version: Literal[1] = 1
    kernel: KernelProgress | KernelSnapshot
    intent: AdvancedEntryIntent | EntryIntent | None
    financial_inputs: tuple[AccountCommand, ...] = ()


class JournalEntry(PaperContract):
    schema_version: Literal[1] = 1
    session_id: Identity
    config_digest: Digest
    ordinal: Annotated[int, Field(gt=0)]
    input_id: Identity
    input_type: Literal["command", "event"]
    logical_sequence: Annotated[int, Field(gt=0)]
    timestamp: UtcTimestamp
    transaction_id: Digest
    payload: str
    output: str
    effects: str
    output_digest: Digest
    previous_digest: Digest
    digest: Digest


class Checkpoint(ExitState):
    schema_version: Literal[1] = 1
    session_id: Identity
    binding_digest: Digest
    ordinal: Annotated[int, Field(gt=0)]
    head_digest: Digest
    record_id: Digest
    state: SessionState
    clock: ClockState
    feed: FeedState
    account: AccountSnapshot
    kernel: KernelProgress | KernelSnapshot
    intent: AdvancedEntryIntent | EntryIntent | None
    runtime_sequence: Annotated[int, Field(ge=0)]
    runtime_timestamp: UtcTimestamp
    # History and idempotency are references to the validated retained prefix.
    runtime_count: Annotated[int, Field(ge=0)]
    financial_count: Annotated[int, Field(ge=0)]
    digest: Digest
