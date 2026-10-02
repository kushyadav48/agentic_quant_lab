"""Validated, content-addressed datasets in a standard-library SQLite adapter."""

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, Self

from pydantic import Field, model_validator

from .enums import PriceType, Timeframe
from .models import Identifier, Instrument, MarketBar, MarketQuote, UtcTimestamp, _DomainModel
from .validation import Observation, QualityReport, ValidationOptions, validate_dataset

SCHEMA_VERSION = 2


class ObservationType(StrEnum):
    QUOTE = "quote"
    BAR = "bar"


class DatasetMetadata(_DomainModel):
    """Save fills derived fields; creation time is excluded from content identity.

    Quote bounds describe first/last events (inclusive); bar bounds describe
    first start/last end (exclusive end). Empty datasets need explicit source
    and observation type; their bounds may be absent or supplied by the caller.
    """

    instrument: Instrument
    description: str = Field(min_length=1)
    licensing: str = Field(min_length=1)
    transformation: str = Field(min_length=1)
    transformation_version: Identifier = "1"
    parent_ids: tuple[Identifier, ...] = ()
    validation_options: ValidationOptions = ValidationOptions()
    expected_starts: tuple[UtcTimestamp, ...] | None = None
    dataset_id: Identifier | None = None
    source_id: Identifier | None = None
    instrument_id: Identifier | None = None
    observation_type: ObservationType | None = None
    timeframe: Timeframe | None = None
    price_type: PriceType | None = None
    start_time: UtcTimestamp | None = None
    end_time: UtcTimestamp | None = None
    record_count: int | None = Field(default=None, ge=0)
    created_at: UtcTimestamp | None = None

    @model_validator(mode="after")
    def check_bounds(self) -> Self:
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time and end_time must be supplied together")
        if self.start_time is not None and self.end_time < self.start_time:
            raise ValueError("end_time must not precede start_time")
        return self


@dataclass(frozen=True)
class StoredDataset:
    dataset_id: str
    metadata: DatasetMetadata
    observations: tuple[Observation, ...]
    quality: QualityReport


class DatasetStore(Protocol):
    def save(self, observations: Iterable[Observation], metadata: DatasetMetadata) -> str: ...
    def load(self, dataset_id: str) -> StoredDataset: ...


class StorageIntegrityError(ValueError):
    """Stored content fails schema, row-count, report, or digest checks."""


def _encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _prepare(
    observations: Iterable[Observation], metadata: DatasetMetadata,
) -> tuple[str, DatasetMetadata, tuple[Observation, ...], QualityReport]:
    metadata = DatasetMetadata.model_validate(metadata)
    records = tuple(observations)
    if any(type(r) not in (MarketQuote, MarketBar) for r in records):
        raise ValueError("canonical observations required")
    records = tuple(type(r).model_validate(r) for r in records)
    quality = validate_dataset(
        records, instrument=metadata.instrument, options=metadata.validation_options,
        expected_starts=metadata.expected_starts,
    )
    if not quality.valid:
        raise ValueError(f"invalid research dataset: {quality.errors}")
    if len({r.source_id for r in records}) > 1:
        raise ValueError("storage requires one source per research dataset")

    derived: dict[str, Any] = {
        "instrument_id": metadata.instrument.instrument_id,
        "record_count": len(records),
    }
    if records:
        first = records[0]
        quotes = isinstance(first, MarketQuote)
        derived.update(
            source_id=first.source_id,
            observation_type=ObservationType.QUOTE if quotes else ObservationType.BAR,
            timeframe=None if quotes else first.timeframe,
            price_type=None if quotes else first.price_type,
            start_time=first.timestamp if quotes else first.start_time,
            end_time=records[-1].timestamp if quotes else records[-1].end_time,
        )
    elif metadata.observation_type is None or metadata.source_id is None:
        raise ValueError("empty datasets require explicit observation_type and source_id")
    if not records and metadata.observation_type is ObservationType.QUOTE:
        derived.update(timeframe=None, price_type=None)
    if not records and metadata.observation_type is ObservationType.BAR:
        if metadata.timeframe is None or metadata.price_type is None:
            raise ValueError("empty bar datasets require timeframe and price_type")
    for field, value in derived.items():
        declared = getattr(metadata, field)
        if declared is not None and declared != value:
            raise ValueError(f"metadata {field} disagrees with observations")
    fields = metadata.model_dump()
    fields.update(derived)
    fields["created_at"] = metadata.created_at or datetime.now(timezone.utc)
    resolved = DatasetMetadata(**fields)
    body = {
        "schema_version": SCHEMA_VERSION,
        "metadata": resolved.model_dump(mode="json", exclude={"dataset_id", "created_at"}),
        "records": [r.model_dump(mode="json") for r in records],
        "quality": quality.model_dump(mode="json"),
    }
    identity = "sha256:" + hashlib.sha256(_encode(body).encode()).hexdigest()
    if metadata.dataset_id is not None and metadata.dataset_id != identity:
        raise ValueError("metadata dataset_id disagrees with content identity")
    fields["dataset_id"] = identity
    return identity, DatasetMetadata(**fields), records, quality


# Decimal values use TEXT, timestamps use UTC ISO strings, and position preserves
# source order. A stored version never rewrites provider provenance on records.
_COLUMNS = (
    "instrument_id", "source_id", "dataset_id", "available_at", "timestamp",
    "timeframe", "price_type", "start_time", "end_time", "open", "high", "low",
    "close", "volume", "volume_type", "bid", "ask",
)
_COLUMN_SQL = ", ".join('"' + c + '"' for c in _COLUMNS)


class SQLiteDatasetStore:
    """Caller-configured local file; no construction-time I/O.

    Save rejects quality errors and never cleans inputs. Empty input warnings
    may be stored with explicit metadata. Versions are transactional and
    idempotent; the first save's created_at is retained on repeated saves.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise StorageIntegrityError("unsupported storage schema version")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, "
                "metadata TEXT NOT NULL, quality TEXT NOT NULL, record_count INTEGER NOT NULL)"
            )
            columns = ", ".join('"' + c + '" TEXT' for c in _COLUMNS)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS observations ("
                "owner TEXT NOT NULL REFERENCES datasets(id), position INTEGER NOT NULL, "
                "kind TEXT NOT NULL CHECK(kind IN ('quote','bar')), " + columns +
                ", PRIMARY KEY(owner, position))"
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
            return connection
        except Exception:
            connection.close()
            raise

    def save(self, observations: Iterable[Observation], metadata: DatasetMetadata) -> str:
        identity, metadata, records, quality = _prepare(observations, metadata)
        connection = self._connect()
        try:
            with connection:
                # Serialize competing saves before checking for an existing version.
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT id FROM datasets WHERE id = ?", (identity,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO datasets VALUES (?, ?, ?, ?)",
                        (identity, metadata.model_dump_json(),
                         _encode(quality.model_dump(mode="json")), len(records)),
                    )
                    placeholders = ",".join("?" for _ in range(len(_COLUMNS) + 3))
                    for position, record in enumerate(records):
                        fields = record.model_dump(mode="json")
                        connection.execute(
                            "INSERT INTO observations (owner, position, kind, " + _COLUMN_SQL +
                            ") VALUES (" + placeholders + ")",
                            (identity, position,
                             "quote" if isinstance(record, MarketQuote) else "bar",
                             *(fields.get(c) for c in _COLUMNS)),
                        )
        finally:
            connection.close()
        self.load(identity)  # verify existing versions, never overwrite corruption
        return identity

    def load(self, dataset_id: str) -> StoredDataset:
        if (not isinstance(dataset_id, str) or len(dataset_id) != 71
                or not dataset_id.startswith("sha256:")
                or any(c not in "0123456789abcdef" for c in dataset_id[7:])):
            raise ValueError("invalid dataset identifier")
        if not self.path.exists():
            raise KeyError(dataset_id)
        try:
            connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.DatabaseError as exc:
            raise StorageIntegrityError("cannot open stored dataset") from exc
        try:
            with connection:
                connection.execute("BEGIN")  # one consistent read snapshot
                if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    raise StorageIntegrityError("unsupported storage schema version")
                header = connection.execute(
                    "SELECT metadata, quality, record_count FROM datasets WHERE id = ?",
                    (dataset_id,),
                ).fetchone()
                if header is None:
                    raise KeyError(dataset_id)
                rows = connection.execute(
                    "SELECT position, kind, " + _COLUMN_SQL +
                    " FROM observations WHERE owner = ? ORDER BY position", (dataset_id,),
                ).fetchall()
                stored_metadata = DatasetMetadata.model_validate_json(header[0])
                stored_quality = QualityReport.model_validate_json(header[1])
                records: list[Observation] = []
                for index, row in enumerate(rows):
                    if row[0] != index or row[1] not in ("quote", "bar"):
                        raise StorageIntegrityError("invalid observation position or kind")
                    model = MarketQuote if row[1] == "quote" else MarketBar
                    fields = {
                        c: value for c, value in zip(_COLUMNS, row[2:])
                        if c in model.model_fields
                    }
                    if any(value is not None for c, value in zip(_COLUMNS, row[2:])
                           if c not in model.model_fields):
                        raise StorageIntegrityError("unexpected observation fields")
                    records.append(model.model_validate_json(_encode(fields)))
                identity, metadata, normalized, quality = _prepare(records, stored_metadata)
                if (identity != dataset_id or len(normalized) != header[2]
                        or quality != stored_quality or metadata != stored_metadata):
                    raise StorageIntegrityError("dataset content, metadata, count or quality digest mismatch")
                return StoredDataset(identity, metadata, normalized, quality)
        except StorageIntegrityError:
            raise
        except (ValueError, TypeError, sqlite3.DatabaseError) as exc:
            raise StorageIntegrityError("invalid stored dataset") from exc
        finally:
            connection.close()
