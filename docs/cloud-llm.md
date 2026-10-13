# Phase 23 cloud research setup and contracts

Cloud inference is disabled by default. The optional `google-genai>=2.30,<3`
adapter uses the official Gemini Developer API, with async `count_tokens` and
`generate_content`, a JSON Schema response, and SDK retries explicitly set to one
attempt. An explicit HTTPX async transport with zero connection retries prevents
optional aiohttp availability from changing the SDK retry path. SDK 2.30.0 was verified with real SDK serialization over an offline
`httpx.MockTransport`. No local model is needed. No model ID is shipped as an
automatically free or enabled default. Other adapters can implement the existing
`LLMProvider` protocol; other cloud providers and cloud image inputs are unsupported.

## Verify the account before enabling requests

In [AI Studio API keys](https://aistudio.google.com/api-keys), identify the exact
key's project. Check its tier and linked billing status in Projects and Billing.
Use a Free Tier project with billing disabled. Do not enable billing, prepay,
auto-reload, paid fallback, Search grounding or other billable tools for this phase.
Consult Google's [billing guide](https://ai.google.dev/gemini-api/docs/billing),
[current pricing](https://ai.google.dev/gemini-api/docs/pricing),
[model capabilities](https://ai.google.dev/gemini-api/docs/models), and
[current rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).
Confirm that the selected concrete model supports text generation, structured
JSON output and free input/output inference for this account and region. Avoid
`latest` aliases. A model's name, its presence in an allowlist, an API key, or a
successful model listing does not prove it is free.

The operator records a short-lived verification containing the exact provider and
model, UTC check/expiry timestamps, and explicit assertions of disabled billing,
free inference eligibility and structured text support. Evidence expires after at
most seven days and is checked again before every inference. Reverify immediately
after account/model changes. This is a trusted operator attestation, not an automated
provider billing check. A false or stale assertion can expose the account to costs.
**Local limits cannot guarantee zero external charges if the provider account has
billing enabled.** The application never changes provider billing settings.

## Exact Windows PowerShell setup

Use Python 3.11+ and Node 22.16+ from the repository root. If the existing virtual
environment references a removed Python, recreate a development environment using
an installed interpreter before following these commands.

```powershell
cd C:\Users\KUSH\OneDrive\Desktop\agentic_quant_lab
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,cloud]'

# Only after completing the account/model verification above:
$env:QUANTLAB_CLOUD_PROVIDER = 'gemini'
$env:QUANTLAB_CLOUD_MODEL = Read-Host 'Exact currently verified free-tier structured-text model ID'
$env:QUANTLAB_CLOUD_MODEL_ALLOWLIST = $env:QUANTLAB_CLOUD_MODEL
$checked = [DateTimeOffset]::UtcNow
$env:QUANTLAB_CLOUD_FREE_VERIFICATION = @{
    provider_id = 'gemini'
    model_id = $env:QUANTLAB_CLOUD_MODEL
    checked_at = $checked.ToString('o')
    expires_at = $checked.AddHours(24).ToString('o')
    billing_disabled = $true
    free_inference_eligible = $true
    structured_text_supported = $true
} | ConvertTo-Json -Compress
$secureKey = Read-Host 'Gemini API key (server terminal only)' -AsSecureString
$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
$env:QUANTLAB_CLOUD_MAX_REQUESTS = '20'
$env:QUANTLAB_CLOUD_TIMEOUT_MS = '20000'
$env:QUANTLAB_CLOUD_ENABLED = 'true'

# Separate explicit smoke: one synthetic inference, no financial mutations.
.\.venv\Scripts\python.exe -m quantlab.llm.smoke --run

# Start the existing authenticated local API.
$env:QUANTLAB_API_OPERATOR_TOKEN = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))")
.\.venv\Scripts\python.exe -m quantlab.api
```

Copy the operator bearer token securely to the dashboard using the existing local
credential workflow; it is distinct from the Gemini key. In a second terminal:

```powershell
cd C:\Users\KUSH\OneDrive\Desktop\agentic_quant_lab\frontend
npm.cmd ci
npm.cmd run dev
```

Open `http://127.0.0.1:5173`, connect with the operator bearer credential, and open
Research. Provider credentials never belong in Vite variables, browser controls,
tracked files or chat. `.env.example` contains empty secrets and safe defaults;
the launcher does not automatically load `.env`. Clear the terminal environment
after use with `Remove-Item Env:GEMINI_API_KEY` and disable cloud inference.

The smoke command without `--run`, or with disabled/missing/unverified configuration,
prints a sanitized `PENDING` reason and exits 2 without provider I/O. A successful
run prints provider/model, schema version, reported usage, reserved requests and
elapsed time; it never prints credentials or raw model text. Failure exits 1.
Each smoke process has its own local budget. Save the successful sanitized output
as acceptance evidence only after a real verified free-tier request succeeds.

## Workflow and authority

1. Check server cloud configuration in Research. Select only the non-sensitive text
   to send, assign strategy ID/version and a stable idempotency key, and explicitly
   consent to that external transmission.
2. Submit AI interpretation. This records a queued process-local job; no cloud call
   occurs until **Execute AI interpretation**. Refresh/lookup are read operations.
3. Inspect the verified result artifact. `READY` means an unapproved draft; a
   clarification result has questions and no strategy. Download the artifact for
   trusted review. The frontend displays the canonical JSON whose digest it verified.
4. Review semantic fidelity and use the existing trusted Python Phase 5
   `mark_validated()` / `approve(ApprovalRecord(...))` process for the exact draft
   identity/version/content digest. There is no HTTP approval endpoint. See
   [strategy-spec.md](strategy-spec.md); do not approve merely because the model
   said READY. Revisions invalidate approvals and require a new interpretation/binding.
5. Compute features using the existing feature engine and select actual local bars,
   instrument, initial capital, quantity, costs and risk policy. Supply those explicit
   inputs with the approved strategy through the existing backtest request editor.
   Use **Bind reviewed research to this AI job** to choose the provenance binding.
   No bars, datasets, archives or journal records are sent to Gemini.
6. Submit research, then explicitly Execute and inspect the existing status/result
   lifecycle. The Phase 7–11 backtester, costs, analytics and risk gates retain
   financial authority. All nine ordinary research kinds remain available; the
   new AI-bound handoff supports one backtest kind only.
7. A trusted application may explicitly call the existing
   `SQLiteJournal.ingest_research(namespace, completed_snapshot)` on its service
   thread. The journal retains exact strategy bindings, result digests and the AI
   job digest in `workflow.thread_digest`. HTTP journal writes remain unsupported.
   Journal ingestion is not automatic or atomic with execution. AI artifacts are
   process-local, so export them separately if durable interpretation evidence is needed.

Example of the separate trusted review step, after inspecting a downloaded artifact:

```python
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
from quantlab.interpretation import InterpretationResult
from quantlab.strategies import ApprovalRecord

record = json.loads(Path("reviewed-ai-artifact.json").read_text())
wire = record["result_json"]  # Original canonical bytes; preserve large provenance integers.
if hashlib.sha256(wire.encode("utf-8")).hexdigest() != record["result_digest"]:
    raise ValueError("Artifact digest mismatch")
artifact = InterpretationResult.model_validate_json(wire, strict=True)
draft = artifact.proposal
if draft is None:
    raise ValueError("Resolve clarification before review")
validated = draft.mark_validated()
# Execute only after an actual human review; this is the existing trusted authority.
approved = validated.approve(ApprovalRecord(
    strategy_id=validated.strategy_id, strategy_version=validated.version,
    content_digest=validated.content_digest, reviewer="operator:local",
    reviewed_at=datetime.now(timezone.utc)))

# Explicitly selected existing local JSON, with instrument/bars/config fields.
# This file is never sent to Gemini. Do not substitute invented market observations.
from pydantic import TypeAdapter
from quantlab.data import Instrument, MarketBar
from quantlab.backtesting import BacktestConfig
from quantlab.features import compute_features, validate_strategy_features
from quantlab.mcp.operation_models import BacktestOperation, OperationSubmission

local = json.loads(Path("selected-local-backtest-input.json").read_text())
instrument = Instrument.model_validate_json(json.dumps(local["instrument"]), strict=True)
bars = TypeAdapter(tuple[MarketBar, ...]).validate_json(json.dumps(local["bars"]), strict=True)
config = BacktestConfig.model_validate_json(json.dumps(local["config"]), strict=True)
features = compute_features(bars, validate_strategy_features(approved), instrument=instrument)
request = OperationSubmission(idempotency_key="reviewed-backtest-1", operation=BacktestOperation(
    kind="backtest", strategy=approved, instrument=instrument, bars=bars,
    features=features, config=config))
# Research editor supplies kind separately; copy this operation JSON into it.
Path("reviewed-backtest-operation.json").write_text(
    request.operation.model_dump_json(exclude={"kind"}), encoding="utf-8")
```

In the dashboard choose `backtest`, use the same `reviewed-backtest-1` research
idempotency key, paste `reviewed-backtest-operation.json`, and set the original AI
job binding. Submission queues an existing deterministic research operation; execute
it separately. The selected local input must already satisfy the existing dataset,
instrument, causality, feature and backtest compatibility contracts.

No AI job can approve, place orders, alter capital, bypass eligibility/recovery,
mutate paper/portfolio owners or modify journal history. Prompt injection stays
untrusted text. No SDK tools, automatic function calling, file upload, grounding,
retrieval, arbitrary URL, filesystem access or autonomous actions are enabled.

## HTTP routes and contracts

All routes use existing bearer authentication. Readers may inspect configuration,
metadata and results. Submission, execution, cancellation and research handoff
require the operator credential regardless of the browser's role selector.

| Method | Route under `/api/v1` | Contract |
|---|---|---|
| GET | `/ai/configuration` | `AIConfiguration`: safe identity, policy, budget, readiness; no key/evidence secret |
| POST | `/ai/interpretations` | `AIInput` → `AIStatus`; explicit transmission consent required |
| GET | `/ai/interpretations/{job_id}` | Metadata-only status |
| POST | `/ai/interpretations/{job_id}/execute` | Explicit shielded async invocation |
| POST | `/ai/interpretations/{job_id}/cancel` | Queued jobs only |
| GET | `/ai/interpretations/{job_id}/result` | `AIResult`, validated Phase 14 artifact and canonical JSON/digest |
| POST | `/ai/research/operations` | `AIResearchSubmission` → existing `OperationStatus`; approved exact bound backtest |

Strict Pydantic input contracts reject missing/unknown fields. Output uses the
existing Phase 14 schema and prompt v1, identified as `interpretation-v1` in job
metadata. JSON parsing rejects duplicates, invalid syntax, unknown fields, missing
required fields and nonfinite numbers. Floating JSON number tokens are rejected
before Pydantic conversion; exact Decimal values must be encoded as strings.
Integers retain their original exact representation. Complete STOP output is mandatory. Cloud
resource preflight additionally caps nesting and sparse Decimal expansion before
domain construction. Deterministic evidence/vocabulary/strategy checks reject
incompatible proposals. No repair, plausible-text acceptance or schema-failure retry.

Job identity hashes request content, provider/model, schema and idempotency key.
An exact duplicate returns its retained snapshot without a cloud call; key reuse
with different content returns 409. Capacity is 32 jobs with no eviction. Execution
returns a terminal failed snapshot with a fixed failure code on cloud/validation
failure; it never produces a fake successful result. Terminal execute retries do
not reissue network calls. A new key intentionally requests a new invocation.

The existing quant/SQLite thread lane remains separate from asynchronous cloud
I/O. One active cloud execution is allowed; a busy slot returns 503 without a queue.
Client disconnect does not cancel inference. Running AI jobs cannot be cancelled;
normal shutdown drains active jobs, bounded by their configured request deadline.
No background worker, durable job store, multi-user tenancy or production deployment
is introduced. All existing Decimal/UTC, bounded pagination, auth and reporting
contracts remain in force. Synthetic Demo does not expose these AI controls and
its adapter rejects inherited cloud methods before fetch.

## Cost, failure and offline policy

Default bounds: 20 reserved wire requests per process session, one concurrent
inference, 32,768 input tokens including conservative schema/framing allowance,
4,096 output tokens including thinking, 49,152 prompt bytes, 65,536 response bytes,
and a 20-second whole invocation deadline (configurable up to 30 seconds).
Generation settings use existing bounded neutral parameters; the dashboard cannot
change them. Each interpretation reserves **two requests**, countTokens plus
generateContent, atomically before I/O. Failed/abandoned requests are not refunded.
Thus the default permits at most ten interpretations, fewer when calls fail. This
ledger is a conservative reservation count, not a claim that both HTTP requests
always reached the provider. It resets on process restart and cannot track other
applications using the key. Reported per-invocation token usage stays in provenance.

No application or SDK retry occurs. DNS/connection errors, timeouts, authentication,
429 rate limits, structured quota violations, server errors, malformed JSON,
schema/domain rejection, truncation, missing usage and unsupported/unverified
configurations produce fixed safe errors. QuotaFailure detail objects distinguish
quota exhaustion when the provider supplies them; an undifferentiated 429 is
reported as rate limiting. There is no paid inference mode or fallback provider.

Cloud failure leaves deterministic research, backtests, reports, journal reads and
Synthetic Demo available. Error logs contain fixed route/correlation/status
metadata, never SDK exception text, prompts or provider keys. The provider key is
server-side `SecretStr`, excluded from configuration serialization and repr. Input
or output containing that configured key is rejected before retained artifacts,
including key occurrences in JSON-decoded strings after Unicode escape decoding.

## Privacy and limits

Cloud calls are external data transmission. Under Google's
[unpaid-service terms](https://ai.google.dev/gemini-api/terms), inputs/outputs can
be used to improve products and machine learning, and human reviewers can process
them. Do not submit sensitive, confidential or personal material. Provider terms
include regional distinctions; verify applicable terms for the account. This phase
does not promise zero retention or a blanket deletion period. Paid services have
different data-use rules but are unsupported by this operating configuration.

The software cannot classify proprietary text reliably, verify the truth of a
human billing attestation, guarantee model semantic fidelity, or guarantee external
charges remain zero after provider/account changes. Cloud image uploads, provider
failover, private archive ingestion and live trading are unsupported. Real-provider
smoke acceptance is recorded as PASS from user-supplied evidence on
`gemini-3.1-flash-lite`: input/output 23/25 tokens, two reserved requests,
4,543.863 ms total, zero financial mutations, approval=false. This is point-in-time
connectivity/structured-output evidence, not a permanent free-model declaration
or full browser strategy-research workflow verification. See
[the final audit](phase23-acceptance-audit.md); no live inference was rerun by it.
