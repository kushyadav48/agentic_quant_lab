"""Bounded explicit AI jobs and exact proposal-to-research bindings. No approval owner."""
import asyncio
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from quantlab.interpretation import InterpretationInput, InterpretationResult, interpret_strategy
from quantlab.interpretation.errors import InterpretationError
from quantlab.llm import InvocationPolicy, LLMClient, LLMError
from quantlab.llm.cloud import CloudError, CloudSettings
from quantlab.llm.gemini import GeminiProvider
from quantlab.mcp.canonical import canonical_json, digest_json
from quantlab.mcp.operation_models import Digest, Key, OperationSubmission, WorkflowBinding

from .errors import APIError
from .schemas import HTTPModel


class AIInput(HTTPModel):
    idempotency_key: Key = Field(repr=False)
    input: InterpretationInput = Field(repr=False)
    external_transmission_approved: Literal[True]


class AIStatus(HTTPModel):
    job_id: Digest
    request_digest: Digest
    provider_id: str
    model_id: str
    schema_version: Literal["interpretation-v1"] = "interpretation-v1"
    state: Literal["queued", "running", "completed", "failed", "cancelled"]
    created_at: datetime
    updated_at: datetime
    result_digest: Digest | None = None
    failure: str | None = None


class AIResult(HTTPModel):
    job_id: Digest
    result_digest: Digest
    result_json: str = Field(repr=False)
    result: InterpretationResult = Field(repr=False)


class AIConfiguration(HTTPModel):
    enabled: bool
    provider_id: str
    model_id: str
    state: str
    free_only: Literal[True] = True
    requests_reserved: int
    maximum_requests: int
    maximum_input_tokens: int
    maximum_output_tokens: int
    maximum_concurrency: Literal[1] = 1
    active: bool
    timeout_ms: int
    maximum_jobs: Literal[32] = 32


class AIResearchSubmission(HTTPModel):
    interpretation_job_id: Digest
    submission: OperationSubmission


class AIJobs:
    """Owned by one ASGI event loop; metadata access never waits for provider I/O.

    Execution is explicitly requested, shielded from client disconnect and drained
    on shutdown. No queues of network work and no retry of terminal jobs.
    """
    def __init__(self, settings: CloudSettings, *, provider=None):
        self.settings = CloudSettings.model_validate(settings)
        self.provider = provider or GeminiProvider(self.settings)
        self.jobs = {}
        self.inputs = {}
        self.results = {}
        self.keys = {}
        self.tasks = set()
        self.closing = False

    def configuration(self):
        used, active = self.provider.budget.snapshot()
        return AIConfiguration(enabled=self.settings.enabled, provider_id=self.settings.provider_id,
            model_id=self.settings.model_id, state=self.settings.rejection() or
            ("budget_exhausted" if used + 2 > self.settings.max_session_requests else "ready"),
            requests_reserved=used, maximum_requests=self.settings.max_session_requests,
            maximum_input_tokens=self.settings.max_input_tokens, maximum_output_tokens=self.settings.max_output_tokens,
            active=active, timeout_ms=self.settings.timeout_ms)

    def submit(self, item: AIInput):
        item = AIInput.model_validate(item)
        identity = self.provider.info.identity
        wire = canonical_json({"input": item.input.model_dump(mode="python"),
            "identity": identity.model_dump(mode="python"), "schema_version": "interpretation-v1"})
        request_digest = digest_json(wire)
        key = digest_json(item.idempotency_key)
        if key in self.keys:
            old = self.get(self.keys[key])
            if old.request_digest != request_digest:
                raise APIError(409, "idempotency_conflict")
            return old
        if self.closing:
            raise APIError(503, "not_ready")
        code = self.settings.rejection()
        if code:
            raise APIError(503, code)
        if self.settings.api_key.get_secret_value() in wire:
            raise APIError(422, "cloud_input_rejected")
        if len(self.jobs) >= 32:
            raise APIError(409, "operation_capacity")
        job_id = digest_json(canonical_json((request_digest, key)))
        now = datetime.now(timezone.utc)
        job = AIStatus(job_id=job_id, request_digest=request_digest,
            provider_id=identity.provider_id, model_id=identity.model_id,
            state="queued", created_at=now, updated_at=now)
        self.jobs[job_id], self.inputs[job_id], self.keys[key] = job, item.input, job_id
        return job

    def get(self, job_id):
        if job_id not in self.jobs:
            raise APIError(404, "not_found")
        return self.jobs[job_id]

    def transition(self, job_id, state, **changes):
        value = self.get(job_id).model_dump(mode="python")
        value.update(state=state, updated_at=datetime.now(timezone.utc), **changes)
        self.jobs[job_id] = AIStatus(**value)
        return self.jobs[job_id]

    async def execute(self, job_id):
        job = self.get(job_id)
        if job.state in ("completed", "failed"):
            return job
        if job.state != "queued":
            raise APIError(409, "operation_not_executable")
        if self.closing:
            raise APIError(503, "not_ready")
        if self.tasks:
            raise APIError(503, "cloud_busy")
        self.transition(job_id, "running")
        task = asyncio.create_task(self._run(job_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return await asyncio.shield(task)

    async def _run(self, job_id):
        try:
            result = await interpret_strategy(LLMClient(self.provider, policy=InvocationPolicy(
                max_attempts=1, max_output_tokens=self.settings.max_output_tokens)), self.inputs[job_id],
                identity=self.provider.info.identity, max_output_tokens=self.settings.max_output_tokens)
            wire = canonical_json(result.model_dump(mode="python"), max_bytes=262144)
            self.results[job_id] = wire
            return self.transition(job_id, "completed", result_digest=digest_json(wire))
        except CloudError as error:
            return self.transition(job_id, "failed", failure=error.code)
        except InterpretationError:
            return self.transition(job_id, "failed", failure="research_validation_rejected")
        except LLMError:
            return self.transition(job_id, "failed", failure="invalid_model_response")
        except Exception:
            return self.transition(job_id, "failed", failure="provider_unavailable")
        finally:
            self.inputs.pop(job_id, None)
            if self.get(job_id).state == "running":
                self.transition(job_id, "failed", failure="provider_unavailable")

    def cancel(self, job_id):
        job = self.get(job_id)
        if job.state == "cancelled":
            return job
        if job.state != "queued":
            raise APIError(409, "operation_not_cancellable")
        self.inputs.pop(job_id, None)
        return self.transition(job_id, "cancelled")

    def result(self, job_id):
        job = self.get(job_id)
        if job.state != "completed":
            raise APIError(409, "result_unavailable")
        wire = self.results[job_id]
        return AIResult(job_id=job_id, result_digest=job.result_digest, result_json=wire,
            result=InterpretationResult.model_validate_json(wire, strict=True))

    def bind_research(self, item: AIResearchSubmission):
        proposal = self.result(item.interpretation_job_id).result.proposal
        operation = item.submission.operation
        # This bounded path supports one backtest with existing explicit approval.
        strategy = getattr(operation, "strategy", None)
        if (operation.kind != "backtest" or proposal is None or strategy is None
                or (strategy.strategy_id, strategy.version, strategy.content_digest) !=
                   (proposal.strategy_id, proposal.version, proposal.content_digest)):
            raise APIError(422, "research_validation_rejected")
        from quantlab.strategies import ApprovalState
        if strategy.state is not ApprovalState.APPROVED:
            raise APIError(422, "research_validation_rejected")
        return WorkflowBinding(strategy_id=proposal.strategy_id, version=proposal.version,
            content_digest=proposal.content_digest, thread_digest=item.interpretation_job_id)

    async def close(self):
        self.closing = True
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
