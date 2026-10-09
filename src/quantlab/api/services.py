"""One application-owned lane for bounded quant work and single-thread SQLite."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Mapping

from quantlab.journal import SQLiteJournal, HistoryQuery, JournalError, JournalRecoveryRequired
from quantlab.mcp.operations import MAX_OPERATIONS, ResearchOperations
from quantlab.mcp.operation_models import OperationKind
from quantlab.paper import PaperSession
from quantlab.portfolio import Portfolio

from .errors import APIError
from .schemas import Capabilities


@dataclass
class ApplicationServices:
    """Created, accessed and closed on the service thread.

    Registered owners are exclusively handed to this application. External
    writers must not share them. The factory must clean partial construction.
    The close callback owns all registered paper stores and other resources;
    this bundle closes the journal itself, without assuming paper ownership APIs.
    """
    operations: ResearchOperations = field(default_factory=ResearchOperations)
    paper_sessions: Mapping[str, PaperSession] = field(default_factory=dict)
    portfolios: Mapping[str, Portfolio] = field(default_factory=dict)
    journal: SQLiteJournal | None = None
    close_owners: Callable[[], None] | None = None

    def __post_init__(self):
        if len(self.paper_sessions) > 32 or len(self.portfolios) > 32:
            raise ValueError("reporting catalogue exceeds bound")
        for identity, owner in self.paper_sessions.items():
            if identity != owner.config.strategy.session_id or owner.config.maximum_inputs > 2000:
                raise ValueError("paper snapshots require matching identities and at most 2000 inputs")
        for identity, owner in self.portfolios.items():
            if identity != owner.snapshot.config.portfolio_id:
                raise ValueError("portfolio catalogue identity mismatch")
        self.paper_sessions = dict(self.paper_sessions)
        self.portfolios = dict(self.portfolios)

    def capabilities(self, settings):
        return Capabilities(research_kinds=tuple(OperationKind), maximum_operations=MAX_OPERATIONS,
            maximum_request_bytes=settings.max_request_bytes, paper_sessions=tuple(sorted(self.paper_sessions)),
            portfolios=tuple(sorted(self.portfolios)), journal=self.journal is not None)

    def check_ready(self):
        # Exercise a bounded public read, preserving uncertain-commit gates.
        if self.journal is not None:
            try:
                self.journal.trade_history(HistoryQuery(limit=1))
            except JournalRecoveryRequired:
                raise APIError(503, "recovery_required") from None
            except JournalError:
                raise APIError(503, "not_ready") from None
        for owner in self.paper_sessions.values():
            if getattr(owner, "recovery_required", False):
                raise APIError(503, "recovery_required")

    def close(self):
        try:
            if self.close_owners is not None:
                self.close_owners()
        finally:
            if self.journal is not None:
                self.journal.close()


def default_services(settings):
    journal = None if settings.journal_path is None else SQLiteJournal(settings.journal_path)
    return ApplicationServices(journal=journal)


class ServiceLane:
    """No execution queue: a busy lane fails admission with HTTP 503.

    A disconnected request does not cancel an executing quant operation or
    free its lane. Shutdown drains the real work before closing its owners.
    Status reads use ResearchOperations' existing metadata lock separately.
    """
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quantlab-api")
        self._pending = None
        self._closing = False
        self._close_task = None
        self.services = None

    async def call(self, function):
        if self._closing or self._pending is not None:
            raise APIError(503, "service_busy")
        future = asyncio.get_running_loop().run_in_executor(self._pool, function)
        self._pending = future
        def finished(done):
            # Retrieve orphan failures after disconnect, without logging details.
            if not done.cancelled():
                done.exception()
            self._pending = None
        future.add_done_callback(finished)
        return await asyncio.shield(future)

    async def start(self, factory):
        def construct():
            services = factory()
            if not isinstance(services, ApplicationServices):
                raise TypeError("ApplicationServices factory required")
            # Acquire ownership before a cancelled startup can discard the result.
            self.services = services
        await self.call(construct)
        await self.call(self.services.check_ready)

    async def close(self):
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._finish_close())
        cancelled = False
        while True:
            try:
                await asyncio.shield(self._close_task)
                break
            except asyncio.CancelledError:
                if self._close_task.cancelled():
                    raise
                # Even repeated lifespan cancellation must wait for owner cleanup.
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _finish_close(self):
        try:
            if self._pending is not None:
                try:
                    await asyncio.shield(self._pending)
                except Exception:
                    pass  # An already reported request failure cannot prevent cleanup.
            if self.services is not None:
                await asyncio.get_running_loop().run_in_executor(self._pool, self.services.close)
        finally:
            await asyncio.to_thread(self._pool.shutdown, wait=True)
