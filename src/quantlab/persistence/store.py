"""One explicit local SQLite connection and serialized transaction owner."""
from enum import Enum, auto
from pathlib import Path
import sqlite3

from quantlab.paper.models import stable_id
from quantlab.paper.session_models import ReplayConfig, ReplayEvent, SessionCommand, SessionRecord
from quantlab.strategies.schema import Digest
from .contracts import (Checkpoint, Effects, JournalEntry, PersistenceError,
                        RecoveryRequired, StoragePolicy, decode)
from quantlab.paper.models import PaperContract
from typing import Literal

SCHEMA_VERSION = 1
APPLICATION_ID = 0x514C5045


class Manifest(PaperContract):
    schema_version: Literal[1] = 1
    engine_version: Literal["paper-18e-v1"] = "paper-18e-v1"
    config: ReplayConfig
    policy: StoragePolicy
    owner_digest: Digest
    admission_digest: Digest
    binding_digest: Digest


_SCHEMA = {
    "metadata": "CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), manifest TEXT NOT NULL, binding TEXT NOT NULL, count INTEGER NOT NULL CHECK(count>=0), head TEXT NOT NULL)",
    "operations": "CREATE TABLE operations (ordinal INTEGER PRIMARY KEY CHECK(ordinal>0), input_id TEXT NOT NULL UNIQUE, logical_sequence INTEGER NOT NULL UNIQUE, transaction_id TEXT NOT NULL UNIQUE, digest TEXT NOT NULL UNIQUE, entry TEXT NOT NULL)",
    "checkpoints": "CREATE TABLE checkpoints (ordinal INTEGER PRIMARY KEY REFERENCES operations(ordinal), digest TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)",
}


def entry_digest(entry):
    return stable_id("paper-durable-entry-v1", entry.model_dump(exclude={"digest"}))


def checkpoint_digest(checkpoint):
    return stable_id("paper-durable-checkpoint-v1", checkpoint.model_dump(exclude={"digest"}))


class _TransactionState(Enum):
    IDLE = auto()
    PREPARING = auto()
    COMMITTING = auto()
    COMMITTED = auto()


class SQLitePaperStore:
    """One file, one session, one exclusive local writer; context-managed lifecycle.

    Never opens a service, thread, URI, memory database or network connection.
    Raw SQL and direct mutation of internals are outside the supported API.
    """
    def __init__(self, path):
        self._connection = None
        self._owner = None
        self._closed = False
        self._transaction_state = _TransactionState.IDLE
        if not isinstance(path, (str, Path)) or not str(path) or str(path) == ":memory:" or str(path).startswith("file:"):
            raise PersistenceError("invalid_path")
        if "\x00" in str(path) or str(path).startswith(("\\\\", "//")):
            raise PersistenceError("invalid_path")
        try:
            self.path = Path(path).resolve()
            if not self.path.parent.is_dir() or self.path.is_dir():
                raise PersistenceError("invalid_path")
        except (OSError, ValueError, RuntimeError) as exc:
            raise PersistenceError("invalid_path") from exc
        # Remote/network filesystems are not a supported durability boundary.
        if str(self.path).startswith(("\\", "//")):
            raise PersistenceError("invalid_path")
        try:
            c = sqlite3.connect(self.path, isolation_level=None, timeout=0)
            self._connection = c
            version = c.execute("PRAGMA user_version").fetchone()[0]
            app = c.execute("PRAGMA application_id").fetchone()[0]
            objects = dict(c.execute("SELECT name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"))
            if version == 0:
                if app != 0 or objects:
                    raise PersistenceError("schema_mismatch")
            elif version != SCHEMA_VERSION or app != APPLICATION_ID or objects != _SCHEMA:
                raise PersistenceError("schema_mismatch")
            if c.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise PersistenceError("durability_unavailable")
            c.execute("PRAGMA synchronous=EXTRA")
            c.execute("PRAGMA locking_mode=EXCLUSIVE")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA trusted_schema=OFF")
            if (c.execute("PRAGMA synchronous").fetchone()[0] != 3
                    or c.execute("PRAGMA locking_mode").fetchone()[0] != "exclusive"
                    or c.execute("PRAGMA foreign_keys").fetchone()[0] != 1):
                raise PersistenceError("durability_unavailable")
            c.execute("BEGIN EXCLUSIVE")
            if version == 0:
                for sql in _SCHEMA.values():
                    c.execute(sql)
                c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                c.execute(f"PRAGMA application_id={APPLICATION_ID}")
            c.commit()
            if c.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise PersistenceError("database_integrity")
        except BaseException as exc:
            if self._connection is not None:
                self._connection.close()
            self._closed = True
            if isinstance(exc, sqlite3.Error):
                raise PersistenceError("storage_unavailable") from exc
            raise

    def _check(self):
        if self._closed:
            raise PersistenceError("store_closed")

    def _fault(self, stage):
        """Internal test seam; production has no callbacks or external work."""

    def _bind(self, owner, manifest, *, recover):
        self._check()
        if self._owner is not None:
            raise PersistenceError("store_already_owned")
        manifest = Manifest.model_validate(manifest)
        wire = manifest.canonical_json()
        decode(Manifest, wire)
        c = self._connection
        row = c.execute("SELECT manifest, binding, count, head FROM metadata").fetchone()
        if recover:
            if row is None:
                raise PersistenceError("missing_metadata")
            stored = decode(Manifest, row[0])
            if stored != manifest or row[1] != manifest.binding_digest:
                raise PersistenceError("configuration_mismatch")
        else:
            if row is not None:
                raise PersistenceError("session_already_exists")
            if c.execute("SELECT count(*) FROM operations").fetchone()[0] or c.execute("SELECT count(*) FROM checkpoints").fetchone()[0]:
                raise PersistenceError("missing_metadata")
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute("INSERT INTO metadata VALUES (1,?,?,0,?)", (wire, manifest.binding_digest, manifest.binding_digest))
                c.commit()
            except sqlite3.Error as exc:
                c.rollback()
                raise PersistenceError("storage_unavailable") from exc
        self._owner = owner
        self.manifest = manifest
        self._config_digest = stable_id("paper-replay-config-v1", manifest.config)

    def lookup(self, identity):
        self._check()
        row = self._connection.execute("SELECT entry FROM operations WHERE input_id=?", (identity,)).fetchone()
        return None if row is None else decode(JournalEntry, row[0])

    def _append(self, item, outcome, effects, *, expected_count, checkpoint=None):
        """Prepare all wire records before BEGIN; commit complete operation once."""
        self._check()
        self._fault("before_prepare")
        m = self.manifest
        identity = item.command_id if type(item) is SessionCommand else item.event_id
        kind = "command" if type(item) is SessionCommand else "event"
        prior = self.lookup(identity)
        if prior is not None:
            from quantlab.paper.errors import PaperIdentityConflict
            if prior.payload != item.canonical_json():
                raise PaperIdentityConflict("durable input identity conflict")
            raise RecoveryRequired("memory_requires_recovery")
        row = self._connection.execute("SELECT count, head FROM metadata WHERE singleton=1").fetchone()
        if row is None or row[0] != expected_count:
            raise RecoveryRequired("durable_head_mismatch")
        ordinal = expected_count + 1
        body = dict(session_id=m.config.strategy.session_id, config_digest=self._config_digest,
            ordinal=ordinal, input_id=identity, input_type=kind, logical_sequence=item.sequence,
            timestamp=item.timestamp, transaction_id=stable_id("paper-durable-transaction-v1", (m.binding_digest, identity)),
            payload=item.canonical_json(), output=outcome.canonical_json(), effects=effects.canonical_json(),
            output_digest=stable_id("paper-durable-output-v1", (outcome, effects)), previous_digest=row[1])
        self._fault("serialization")
        entry = JournalEntry(**body, digest=stable_id("paper-durable-entry-v1", {"schema_version": 1, **body}))
        wire = entry.canonical_json()
        decode(JournalEntry, wire)
        decode(type(item), entry.payload)
        decode(SessionRecord, entry.output)
        decode(Effects, entry.effects)
        cp = None if checkpoint is None else checkpoint(entry)
        cp_wire = None if cp is None else cp.canonical_json()
        if cp is not None:
            decode(Checkpoint, cp_wire)
        c = self._connection
        try:
            self._transaction_state = _TransactionState.PREPARING
            c.execute("BEGIN IMMEDIATE")
            # Detect stale ownership again inside the writer transaction.
            if c.execute("SELECT count,head FROM metadata").fetchone() != row:
                raise RecoveryRequired("durable_head_mismatch")
            c.execute("INSERT INTO operations VALUES (?,?,?,?,?,?)", (ordinal, identity, item.sequence, entry.transaction_id, entry.digest, wire))
            if cp is not None:
                c.execute("INSERT INTO checkpoints VALUES (?,?,?)", (ordinal, cp.digest, cp_wire))
                c.execute("DELETE FROM checkpoints WHERE ordinal NOT IN (SELECT ordinal FROM checkpoints ORDER BY ordinal DESC LIMIT ?)", (m.policy.retained_checkpoints,))
            c.execute("UPDATE metadata SET count=?, head=? WHERE singleton=1", (ordinal, entry.digest))
            self._fault("before_commit")
            # Shared with the session BEFORE commit: interruptions anywhere after
            # this assignment remain fail-closed, including return/handoff.
            self._transaction_state = _TransactionState.COMMITTING
            self._fault("commit_armed")
            c.commit()
            self._transaction_state = _TransactionState.COMMITTED
            self._fault("after_commit")
            return entry
        except BaseException as exc:
            self._rollback_transaction(exc)
            if isinstance(exc, sqlite3.Error):
                raise PersistenceError("transaction_failed") from exc
            raise

    def _rollback_transaction(self, exc):
        state = self._transaction_state
        try:
            self._connection.rollback()
        except BaseException as rollback_error:
            # A failed or interrupted rollback cannot authorize another attempt.
            self._transaction_state = _TransactionState.COMMITTING
            raise RecoveryRequired("rollback_outcome_unknown") from rollback_error
        if state in (_TransactionState.COMMITTING, _TransactionState.COMMITTED):
            reason = "committed_unpublished" if state is _TransactionState.COMMITTED else "commit_outcome_unknown"
            raise RecoveryRequired(reason) from exc
        # Only a confirmed rollback before the commit boundary permits retry.
        self._transaction_state = _TransactionState.IDLE

    def _publication_complete(self, owner):
        if self._owner is not owner or self._transaction_state is not _TransactionState.COMMITTED:
            raise RecoveryRequired("publication_state_mismatch")
        # The owner calls this only AFTER the complete memory root is published.
        self._transaction_state = _TransactionState.IDLE

    def verify(self):
        """Validate all retained input/output records, indexes, chain and tail anchor."""
        self._check()
        try:
            c = self._connection
            if c.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise PersistenceError("database_integrity")
            rows = c.execute("SELECT ordinal,input_id,logical_sequence,transaction_id,digest,entry FROM operations ORDER BY ordinal")
            retained = []
            previous = self.manifest.binding_digest
            sequence = 0
            timestamp = self.manifest.config.strategy.timestamp
            record_id = None
            for ordinal, identity, logical, transaction, digest, wire in rows:
                e = decode(JournalEntry, wire)
                if (ordinal, identity, logical, transaction, digest) != (e.ordinal, e.input_id, e.logical_sequence, e.transaction_id, e.digest):
                    raise PersistenceError("index_mismatch")
                if e.ordinal != len(retained) + 1 or e.previous_digest != previous:
                    raise PersistenceError("journal_chain")
                if e.digest != entry_digest(e):
                    raise PersistenceError("journal_digest")
                if e.session_id != self.manifest.config.strategy.session_id or e.config_digest != self._config_digest:
                    raise PersistenceError("configuration_mismatch")
                item = decode(SessionCommand if e.input_type == "command" else ReplayEvent, e.payload)
                outcome, effects = decode(SessionRecord, e.output), decode(Effects, e.effects)
                item_id = item.command_id if type(item) is SessionCommand else item.event_id
                if ((e.input_id, e.logical_sequence, e.timestamp) != (item_id, item.sequence, item.timestamp)
                        or item.sequence <= sequence or item.timestamp < timestamp
                        or e.transaction_id != stable_id("paper-durable-transaction-v1", (self.manifest.binding_digest, item_id))):
                    raise PersistenceError("input_order")
                if (outcome.previous_record_id != record_id or outcome.input_id != item_id
                        or outcome.session_id != e.session_id or outcome.config_digest != e.config_digest
                        or outcome.input_digest != stable_id("paper-session-input-v1", item)
                        or (outcome.sequence, outcome.timestamp) != (item.sequence, item.timestamp)
                        or outcome.command != (item if type(item) is SessionCommand else None)
                        or outcome.market_event != (item if type(item) is ReplayEvent else None)):
                    raise PersistenceError("output_provenance")
                if e.output_digest != stable_id("paper-durable-output-v1", (outcome, effects)):
                    raise PersistenceError("output_digest")
                retained.append((e, item, outcome, effects))
                previous, sequence, timestamp, record_id = e.digest, item.sequence, item.timestamp, outcome.record_id
            meta = c.execute("SELECT count,head FROM metadata WHERE singleton=1").fetchone()
            if meta != (len(retained), previous):
                raise PersistenceError("journal_tail")
            if c.execute("PRAGMA foreign_key_check").fetchall():
                raise PersistenceError("checkpoint_reference")
            return retained
        except sqlite3.Error as exc:
            raise PersistenceError("database_integrity") from exc

    def _save_checkpoint(self, checkpoint):
        """Optional accelerator transaction; never changes authoritative inputs."""
        self._check()
        cp = Checkpoint.model_validate(checkpoint)
        wire = cp.canonical_json()
        decode(Checkpoint, wire)
        if cp.digest != checkpoint_digest(cp) or cp.binding_digest != self.manifest.binding_digest:
            raise PersistenceError("checkpoint_digest")
        c = self._connection
        try:
            self._transaction_state = _TransactionState.PREPARING
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT count,head FROM metadata").fetchone() != (cp.ordinal, cp.head_digest):
                raise RecoveryRequired("durable_head_mismatch")
            prior = c.execute("SELECT payload FROM checkpoints WHERE ordinal=?", (cp.ordinal,)).fetchone()
            if prior is not None:
                if prior != (wire,):
                    raise PersistenceError("checkpoint_conflict")
                c.rollback()
                self._transaction_state = _TransactionState.IDLE
                return cp
            c.execute("INSERT INTO checkpoints VALUES (?,?,?)", (cp.ordinal, cp.digest, wire))
            c.execute("DELETE FROM checkpoints WHERE ordinal NOT IN (SELECT ordinal FROM checkpoints ORDER BY ordinal DESC LIMIT ?)", (self.manifest.policy.retained_checkpoints,))
            self._fault("before_checkpoint_commit")
            self._transaction_state = _TransactionState.COMMITTING
            c.commit()
            self._transaction_state = _TransactionState.COMMITTED
            return cp
        except BaseException as exc:
            self._rollback_transaction(exc)
            if isinstance(exc, sqlite3.Error):
                raise PersistenceError("transaction_failed") from exc
            raise

    def checkpoints(self):
        self._check()
        result = []
        for ordinal, digest, wire in self._connection.execute("SELECT ordinal,digest,payload FROM checkpoints ORDER BY ordinal"):
            cp = decode(Checkpoint, wire)
            if cp.ordinal != ordinal or cp.digest != digest or cp.digest != checkpoint_digest(cp):
                raise PersistenceError("checkpoint_digest")
            result.append(cp)
        return result

    def close(self):
        if not self._closed:
            self._connection.close()
            self._closed = True
            self._owner = None

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *args):
        self.close()
