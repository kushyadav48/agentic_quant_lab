# Natural-language strategy interpretation

Phase 14 implements `quantlab.interpretation`. It translates human strategy text
into a reviewable, **unapproved** Phase 5 `StrategySpecification`, or returns
specific clarification questions without a proposal. It does not establish
profitability, execution eligibility, or approval.

## Workflow and API

1. The application supplies an immutable `InterpretationInput`: `strategy_text`,
   `input_reference`, `strategy_id`, and positive integer `version`.
2. `build_request` creates deterministic system/user messages, a Phase 13 JSON
   Schema request, and `PromptProvenance`.
3. `interpret_strategy` calls Phase 13 `generate_structured` with an `LLMClient`.
4. Strict `StructuredInterpretation` validation accepts a READY draft or questions.
5. READY conversion constructs `StrategyContent`, which runs existing Phase 5
   semantic validation, then checks the bounded interpretation feature vocabulary.
6. Conversion creates an existing `StrategySpecification` in DRAFT state, with
   `approval=None` and `created_at=None`. No lifecycle promotion is invoked.
7. A separate human review workflow may later use the existing exact-version
   validation/approval APIs. The backtester rejects the interpreted draft.

With an application-supplied Phase 13 client and model identity:

```python
from quantlab.interpretation import InterpretationInput, interpret_strategy

idea = InterpretationInput(
    strategy_text=("Trade research:TEST on 1m bars, long only. Enter when close "
                   "crosses above the SMA of 20 closes. Exit when close crosses "
                   "below the same SMA. No other restrictions."),
    input_reference="idea:example-14", strategy_id="sma_idea", version=1,
)
result = await interpret_strategy(client, idea, identity=model_identity)
# Display result.interpretation and result.proposal for review.
# result.proposal is None when clarification is required.
```

The caller owns identity/version allocation and reference retention. References
are opaque and never fetched. There is no strategy registry or persistence here.

## Contracts and ambiguity

All Phase 14 models are strict, frozen, forbid extras and revalidate instances.
Collections are tuples. Text is nonblank and limited to 16,000 characters; source
references to 512; display names to 200; questions/descriptions to 2,000. Drafts
allow at most 32 instruments, 64 features and 64 parameters; result envelopes
allow at most 32 clarification pairs and 32 source-evidence items. Nested rule,
identifier, argument and scalar bounds remain the existing Phase 5 contracts.
No credential, executable payload, timestamp or random identity field is added.
Text itself can contain arbitrary instructions or code-like strings, but stays
inert data; nothing evaluates it.

`StrategyDraft` is a small untrusted boundary model. It reuses Phase 5 side rules,
features, parameters, distances, sessions and timing types. It excludes strategy
identity, provenance and approval, which the application controls. Nullable
intent fields are required in structured output: silently omitting a field is
not accepted. Timing alone defaults to documented Phase 5 BAR_CLOSE/NEXT_BAR_OPEN.
Nested Phase 5 defaults such as current-bar offset zero retain their established
meaning. There is no second executable strategy schema or digest algorithm.

`READY` requires a draft and source evidence, with zero clarification questions.
`NEEDS_CLARIFICATION` requires one or more ambiguity/question pairs and forbids
both a draft and evidence. It produces no usable strategy specification.
Missing SMA periods, RSI thresholds, instrument/timeframe/direction, ambiguous
entry combinations, absent exit intent and unsupported concepts must be clarified.
Explicitly requested no-exit intent is valid Phase 5 content; unspecified exit
behavior must not be silently treated as that choice. Optional restrictions not
requested can remain null. There are no invented material assumptions or numeric
confidence scores. Descriptive names and safe local aliases do not add trading
semantics. Clarification answers must come from the caller in a new input; the
application never answers or merges them automatically.

For every populated material field, READY includes an exact source quote. The
converter checks quote presence in the original text and complete field coverage.
These checks catch missing/fabricated citations, **not semantic mistranslation**.
A model can attach a real but irrelevant quote or misread a number. Human review
must compare the complete draft with the input. No deterministic general-purpose
natural-language truth verifier or semantic-equivalence guarantee is claimed.

## Supported vocabulary

The exact Phase 5 wire vocabulary is reused:

| Construct | Values / constraints |
| --- | --- |
| Direction | `long`, `short`, `both`; exactly corresponding side rules |
| Timeframe | `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`, `1w` |
| Rule group | `all`, `any`; flat groups of 1 to 64 rules |
| Comparison | `gt`, `ge`, `lt`, `le`, `eq`, `crosses_above`, `crosses_below` |
| Operand | `market`, `feature`, `parameter`, `constant` |
| Market field | `open`, `high`, `low`, `close`, `bid`, `ask` |
| Offset | Completed bars, integer 0 to 10,000, never a future offset |
| Parameter | `integer`, `decimal`, `boolean`; exact matching defaults/bounds |
| Boolean rule | Equality with another boolean only |
| Stops/targets | Fixed positive `price`/`percent`, feature price distance; take-profit also `risk_reward` requiring a stop |
| Session | Explicit UTC wall times, Monday=0 to Sunday=6, explicit overnight flag |
| Sizing | Opaque external `sizing_reference`; no sizing calculation |
| Timing | `bar_close`, `next_bar_open` |

Strategy-visible feature aliases must resolve to explicit implementations; when
`implementation_id` is null, existing lookup uses `feature_id` literally. An alias
such as `sma_20` never implicitly determines a period or algorithm.

| Feature type | Implementation | Required arguments |
| --- | --- | --- |
| `indicator` | `open`, `high`, `low`, `close` | None |
| `indicator` | `simple_return`, `log_return` | None |
| `indicator` | `sma`, `ema`, `rsi` | Strict integer `period >= 1` |
| `indicator` | `rolling_volatility` | Strict integer `window >= 2` |
| `ml_signal` | `ml_forward_return_v1` | Exactly one integer `model_digest` in `[0, 2**256)` |

SMA, EMA and RSI use close; EMA uses its SMA seed, RSI Wilder smoothing, and
rolling volatility the unannualized population standard deviation of simple
returns. Exact formulas remain in [the feature documentation](feature-engine.md).
ML is an existing externally fitted model declaration only: no model training,
prediction, artifact resolution or authenticity verification occurs here.
No parameter defaults, extra arguments, boolean/Decimal substitutes for integers,
unknown implementations, LEVEL features or mismatched timeframe overrides are
accepted. ATR, trailing stops, dynamic expressions, generated code, cross-timeframe
features and autonomous optimization remain unsupported.

The versioned allowlist in `vocabulary.py` is interpretation policy, not a new
feature engine. Phase 14 imports only Phase 13 infrastructure and minimum Phase 5
contracts, keeping computational layers out of its import graph. Offline tests
compare the indicator catalogue with the existing registry and check accepted
declarations through `validate_strategy_features`, including bounded ML_SIGNAL.
Changes to supported vocabulary require a deliberate policy/prompt version update.

READY means valid **proposal intent**. Phase 5 represents more than the present
backtester can execute: stop/target, session, external sizing and BID/ASK operands
are preserved when explicit, but remain unsupported by that engine. Multi-instrument
intent likewise is not permission to bypass its single-instrument constraints.
Feature-distance units, external references and data availability require later
deterministic eligibility checks. Interpretation never calls an engine to decide
what is profitable or executable.

## Prompt and provenance

- Prompt ID: `quantlab.natural-language-strategy`
- Prompt version: `1`

The prompt is deterministic application code, with no timestamps or generated IDs.
System instructions state that user content is untrusted data. The user message
contains a JSON-escaped `strategy_text` field only. Source reference flows through
Phase 13 `PromptProvenance`; application strategy ID/version never come from the
model. No vendor-specific prompting syntax or credentials are used.

`InterpretationResult` retains the input, parsed interpretation, optional proposal,
and complete Phase 13 `InvocationResult` with exact request, raw response,
provider/model identity, prompt provenance, policy and attempt count. Its validator
rebuilds the expected request, reparses the raw response and recomputes the proposal
to reject inconsistent or forged model copies. This is consistency validation,
not provider authentication or tamper-proof storage.

The content provenance uses `Origin.NATURAL_LANGUAGE`, the supplied source
reference, and application-generated prompt ID/version notes. Provider/model and
raw response live in the accompanying invocation artifact. Retain the complete
result to preserve that linkage; a standalone specification is not the full audit
record. Content identity uses only existing `StrategyContent.content_digest()`;
all normal provenance fields participate as before. Different provider responses
may have the same canonical strategy content. No second strategy identity exists.

## Fail-closed boundaries

Phase 13 retains responsibility for provider capabilities, retries, budgets,
timeouts, sanitization, duplicate-key rejection and strict structured parsing.
Malformed JSON, Markdown wrappers, extras, missing fields, wrong types, unsupported
finish reasons and invalid nested Phase 5 models raise `StructuredOutputError`.
There is no repair or retry loop for invalid generated content.

After parsing, fresh Phase 5 constructors validate direction/side consistency,
unique declarations, all references, boolean compatibility and stop/target
relationships. Invalid strategy semantics or unsupported feature declarations
raise sanitized `InterpretedStrategyError`. Source-evidence or audit inconsistency
raises `InterpretationContractError`. Invalid input at the public service/prompt
boundary raises `InterpretationInputError`; direct Pydantic construction uses
`ValidationError`. Provider failures retain their Phase 13 types and retry policy.
Errors never interpolate provider exception text or source content. Raw diagnostic
artifacts are available by deliberate access and should not be blindly logged.

No package code imports or calls backtesting, execution, analytics, validation,
risk, ML training, paper trading, portfolio, API or UI code. No network access,
eval/exec, generated Python/SQL or arbitrary expression language is implemented.
Prompt separation helps resist injection but cannot guarantee model compliance;
schema/domain checks and the separate approval boundary enforce the actual limits.

## Determinism, testing and deferred work

Identical application inputs/model identity/resource limit yield identical
requests. Identical accepted structured outputs and application metadata yield
identical proposals and existing canonical digests. Live LLM output itself is
not deterministic, including when a provider accepts a seed or temperature zero.
Calls are invocation-scoped; A -> B -> A does not retain clarification or proposal
state. Cancellation and transient retries remain Phase 13 behavior.

Offline tests use only `FakeProvider`. Coverage includes strict/frozen/bounded
inputs, unchecked nested copies, prompt provenance and injection-as-data, both
statuses, exact domain conversion, structured failures, unsupported vocabulary,
registry parity, approval separation, backtester rejection, import/network
isolation, retry propagation, state isolation and replay consistency.

Future Ollama/Qwen-class or cloud adapters can implement the same Phase 13
`LLMProvider` without changing this package; they must support its structured
output capability. No real adapter or local-model quality evaluation is included.
Complex schemas may exceed a particular model's capacity; failures remain closed.

Chart/image interpretation (15), agents (16), MCP (17), paper trading (18), portfolio
(19), journal (20), FastAPI (21), frontend (22), E2E (23), deployment (24) and final
portfolio polish (25) remain deferred. So do automatic approval, profitability
ranking, strategy optimization, alpha search, self-modification, real-money
execution, persistence, reviewer authentication and automatic clarification loops.
