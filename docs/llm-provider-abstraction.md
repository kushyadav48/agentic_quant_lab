# LLM provider abstraction — Phase 13

`quantlab.llm` is an offline, provider-neutral invocation boundary. It defines
contracts for later interpretation and orchestration without implementing them.
There are no vendor SDK dependencies, real provider adapters or network calls.

**LLM output is untrusted.** Provider responses never directly create fills, risk
decisions, authoritative financial metrics or account state. Existing deterministic
Python engines remain authoritative. This package imports no quantitative domains.

## Architecture and protocol

| Module | Responsibility |
| --- | --- |
| `models.py` | Frozen request, response, capability, provenance, usage and policy contracts |
| `enums.py` | Roles, declared image MIME types and neutral finish reasons |
| `provider.py` | Async `LLMProvider` protocol |
| `client.py` | Preflight validation, capability checks, limits, retries and response validation |
| `structured.py` | Pydantic schema construction and strict typed output validation |
| `_json.py` | Strict JSON parsing and canonical schema transport |
| `fake.py` | Deterministic scripted adapter and attempt history |
| `errors.py` | Neutral, sanitized error taxonomy |
| `__init__.py` | Explicit public exports |

An adapter exposes read-only `info: ProviderInfo` and
`async generate(request: LLMRequest) -> LLMResponse`. One configured adapter
represents one explicit provider/model identity and its capabilities. Provider and
model identifiers are opaque, case-sensitive, non-whitespace strings of up to 512
characters; no family aliases or vendor name normalization occur. A deployment
that resolves aliases should configure the resolved identity consistently.

Future adapters translate neutral contracts into SDK requests, map finish reasons,
and return neutral response models. They must translate vendor exceptions to
sanitized errors, honor output constraints and cancellation, and reject unsupported
parameters or schemas explicitly. Capability flags do not promise support for
every possible vendor parameter or JSON Schema feature.

## Contracts and provenance

All public domain models use the existing strict/frozen/extra-forbidden Pydantic
conventions, validate defaults and revalidate instances. Ordered collections are
tuples. The client revalidates even constructed/copied model instances. Core
configuration is defined locally so importing this package cannot load a quant
engine merely to inherit its base class.

`LLMRequest` contains an explicit `ModelIdentity`, ordered `Message` tuple,
`PromptProvenance`, positive `max_output_tokens`, `GenerationParameters` and an
optional `StructuredOutput`. Provenance requires `prompt_id` and `prompt_version`;
`input_reference` is optional. The caller owns prompt versioning. An
`InvocationResult` records the complete validated request, raw neutral response,
policy and number of attempts, binding output to its exact inputs. There are no
random IDs or wall-clock fields in content identity.

Messages use SYSTEM, USER or ASSISTANT roles with explicitly tagged content.
Contracts bound messages to 256, content items per message to 128, each nonblank
text item to 1,000,000 characters, and response text to 4,000,000 characters.
Response text may be empty, for example for a refusal. These are storage bounds,
not tokenizer estimates or vendor context-window guarantees.

`ImageReference` is a forward-compatible declaration of an opaque caller-owned
asset ID and PNG/JPEG/WebP MIME type. It contains no image bytes or URL transport.
Nothing fetches or interprets it; media declarations are not byte validation.
Phase 15 must implement upload validation, storage, privacy and adapter translation.

Generation temperature is optional Decimal in [0, 2], top-p is optional Decimal
in (0, 1], and seed is an optional bounded nonnegative integer. Python float,
string and boolean substitutes are rejected. Pydantic JSON round trips preserve
typed meaning, including its standard JSON encodings for Decimal, enums and tuples.
Provider defaults remain unspecified when parameters are absent. A seed is not a
promise of reproducible model output.

`LLMResponse` carries provider/model identity, raw text, optional finish reason,
optional `TokenUsage` and optional provider request ID. Unknown metadata remains
`None`. Usage counts are strict nonnegative integers; booleans are invalid. When
all three counts exist, total must equal input plus output. A reported component
cannot exceed a reported total. Adapters must normalize provider accounting to
this convention or reject incompatible accounting; counts are not financial metrics.

## Capabilities and fail-closed validation

`Capabilities` declares text input, image input and structured output independently.
The client checks every content item and structured request before generation.
Unsupported capabilities raise an error with zero attempts; images are never
discarded and structured requests never become unconstrained requests.

The requested identity must match adapter information. Returned identity must
match the request exactly. Untyped responses, forged invalid models, incoherent
usage or reported output beyond the requested output limit fail explicitly.

## Structured output

`structured_output(ExpectedModel)` creates an immutable `StructuredOutput` with a
name and canonical `json_schema` string. Schemas use sorted keys, compact
separators and ASCII escaping, matching the repository's JSON convention. No
mutable schema dictionaries or Python classes escape into artifacts. No new
digest convention is introduced; the full schema and request are recorded.
The transport checks JSON object structure, not full JSON Schema semantics;
Pydantic generates the supported schema in the typed path. No remote references
are resolved. A future adapter must explicitly reject schemas it cannot support.

Use `generate_structured(client, request, ExpectedModel)` for typed results. The
request must explicitly carry the exact schema generated for that model; a
mismatch fails before invocation. The helper returns `(typed_value, invocation)`.
The model must forbid extra fields; nested models retain their own declared
constraints and should also forbid extras where appropriate. Strict Pydantic JSON
validation is used, including its normal JSON-specific date/enum/tuple semantics.
Application-supplied model classes and validators are trusted application code.

Malformed JSON, duplicate keys, non-finite numbers, schema violations and wrong
scalar types fail without repair. LENGTH, REFUSAL, CONTENT_FILTER and OTHER
termination reasons cannot produce a trusted structured result, even with valid
JSON. A missing finish reason is allowed. `StructuredOutputError.response`
preserves raw output for explicit diagnostics; exception text does not include it.
`validate_structured` is available for an already validated neutral response.
Plain `client.generate` returns raw text and does not claim it is a typed result.
There is no eval, exec, executable message content or model-generated Python.

## Retry, timeout and budget policy

`InvocationPolicy` is immutable and scoped to each client invocation:

- `max_attempts`: 1–10, including the initial attempt; default 1.
- `max_output_tokens`: positive requested-output cap, up to 1,000,000; default 4096.
- `max_total_tokens`: optional positive total-token check, described below.
- `timeout_ms`: optional per-attempt timeout, up to one hour.
- `retry_delay_ms`: fixed delay between failed retryable attempts, 0–60,000; default 0.

Only `TransientProviderError`, its rate-limit/timeout subclasses, and a built-in
`TimeoutError` raised by the provider/timeout context are retryable. Permanent
provider failures, response incompatibility, capability/local-input failures,
usage/policy failures and structured-output failures are not retried. No delay
occurs after success or the final failed attempt. The delay function is injectable
as `async delay(milliseconds)` for deterministic tests. The default zero delay does
not sleep. There are no internal threads, event-loop creation or global counters.
External task cancellation propagates. Timeouts use cooperative async cancellation;
adapters must not block the event loop or swallow cancellation.

The requested output limit is checked against both the output cap and, if set,
the total-token cap before any attempt. After a successful provider return, the
optional total-token policy checks the reported total, or the sum of reported
input and output when both are present. This calculation does not populate a
missing total field. Insufficient metadata raises `UsageUnavailableError`;
over-budget usage raises `BudgetExceededError`.

**This is a per-response usage check, not a cumulative spending guarantee.**
Input tokens are not estimated. Failed attempts may consume unknown tokens, and
post-response checks cannot undo consumption. No cost, billing, payment, shared
token pool or aggregate orchestration budget is implemented. Each invocation
starts its own attempt count and usage checks. Phase 16 must define any cumulative
reservations and unknown-usage policy explicitly.

## Errors and secrets

| Error | Meaning |
| --- | --- |
| `LLMInputError` | Invalid local input, identity, policy or expected schema |
| `UnsupportedCapabilityError` | Required capability unavailable |
| `ProviderError` | Permanent or unclassified provider/network failure |
| `TransientProviderError` | Adapter-declared retryable failure |
| `RateLimitError` | Retryable provider throttling |
| `ProviderTimeoutError` | Retryable attempt timeout |
| `ResponseCompatibilityError` | Invalid response type, identity or metadata |
| `StructuredOutputError` | Invalid/incomplete structured output |
| `BudgetExceededError` | Requested or reported resource limit exceeded |
| `UsageUnavailableError` | Cannot enforce usage policy from available counts |
| `FakeScriptError` | Direct fake invocation exhausted its script or mismatched an expectation |

These derive from `LLMError`; transient failures derive from `ProviderError`.
Direct model construction uses normal Pydantic `ValidationError`. Public service
boundaries translate invalid model instances into neutral input/response errors.
Unclassified adapter exceptions, including fake script mistakes through the client,
become sanitized `ProviderError` without their original message or displayed chain.

There is deliberately no credential object or credential field in these contracts.
Future adapters receive credentials at runtime construction from an application
composition boundary, outside request, response and provenance artifacts. Adapters
must exclude credentials from reprs, logs and exceptions. Importing this package
does not read credentials, create SDK clients or contact a network. It logs nothing.
Core errors have fixed messages, model error displays hide input, and raw text is
excluded from model reprs. Explicit diagnostic output and serialization still
contain application content: callers must never place credentials in prompts,
IDs, schema descriptions or provenance, and must apply retention/redaction policy
before logging raw artifacts. This boundary is not a general secret detector.

## Offline example

```python
from pydantic import BaseModel, ConfigDict
from quantlab.llm import (
    Capabilities, FakeProvider, LLMClient, LLMRequest, LLMResponse, Message,
    MessageRole, ModelIdentity, PromptProvenance, ProviderInfo, TextContent,
    generate_structured, structured_output,
)

class Summary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    summary: str

async def example():
    identity = ModelIdentity(provider_id="fake", model_id="script-v1")
    request = LLMRequest(
        identity=identity,
        messages=(Message(role=MessageRole.USER,
                          content=(TextContent(text="Summarize the supplied text."),)),),
        provenance=PromptProvenance(prompt_id="summary", prompt_version="1"),
        max_output_tokens=64,
        structured_output=structured_output(Summary),
    )
    provider = FakeProvider(
        ProviderInfo(identity=identity, capabilities=Capabilities(structured_output=True)),
        (LLMResponse(identity=identity, text='{"summary":"An offline example."}'),),
        expected_requests=(request,),
    )
    value, invocation = await generate_structured(LLMClient(provider), request, Summary)
    assert value.summary == "An offline example."
    assert provider.history == (request,)
    return value, invocation
```

The fake consumes one explicit response/error per generation attempt, including
failed attempts. Its history is an immutable snapshot of ordered requests, scoped
to the instance. Expected requests can assert complete equality. No script reset,
implicit reuse, global state or output synthesis occurs. Forged response models
are allowed in scripts to exercise client rejection behavior.

## Determinism, testing and deferred work

Contract validation, canonical schema representation, script playback and retry
accounting are deterministic for identical inputs. Real LLM output is not claimed
to be deterministic. Provenance records what was sent and received; it does not
make generated facts authoritative.

`tests/llm` covers strict/frozen contracts, JSON round trips, capability failures,
structured validation, retry limits, cancellation, usage boundaries, secret-safe
displays, A–B–A isolation and an offline structured invocation pipeline. Fresh
subprocess import tests deny network and credential lookups. AST/import tests keep
quantitative engines and vendor SDKs outside this boundary. Tests require no
provider account and perform no real retry sleeps.

Phase 14 strategy interpretation/prompts, Phase 15 chart/image interpretation and
uploads, and Phase 16 agents/LangGraph/orchestration remain deferred. MCP, trading
tool calls, backtest/risk invocation, paper trading, portfolio management, APIs,
autonomous research and real provider adapters are outside Phase 13.
