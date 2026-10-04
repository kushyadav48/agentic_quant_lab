# Chart + text multimodal strategy interpretation

Phase 15 extends `quantlab.interpretation` with ordered chart references and
optional human text. It produces either a reviewable, **unapproved** Phase 5
`StrategySpecification` or explicit clarification questions with no proposal.
It implements contracts and an offline pipeline, not a real vision adapter.

## Architecture and workflow

1. The caller constructs `MultimodalInput`, assigning strategy identity/version,
   a source reference, ordered images and optional strategy text.
2. `build_multimodal_request` creates a deterministic SYSTEM prompt and USER
   message containing optional JSON-wrapped text followed by the ordered Phase 13
   `ImageReference` items. Strategy ID/version stay outside model instructions.
3. `interpret_multimodal_strategy` uses the existing Phase 13 `LLMClient` and
   `generate_structured`. Capabilities, strict parsing, retries, budgets, provider
   identity and sanitized failures remain entirely Phase 13 responsibilities.
4. `MultimodalInterpretation` extends `StructuredInterpretation`, using the same
   `InterpretationStatus`, `StrategyDraft`, `SourceEvidence` and `Clarification`.
   Its additions concern visual evidence and cross-source conflicts only.
5. Deterministic checks bind evidence to input asset IDs/text and check coverage.
   The shared Phase 14 converter constructs Phase 5 `StrategyContent`, applies the
   same feature allowlist and constructs `StrategySpecification` in DRAFT state.
6. `MultimodalResult` retains the complete input, interpretation, Phase 13 invocation
   and proposal. A separate human review/approval workflow is still required.

The three new modules are `multimodal_models.py`, `multimodal_prompts.py` and
`multimodal.py`. The small Phase 14 refactor extracts its existing conversion and
material-field coverage helpers, shares its status validator and executable
vocabulary prompt section, and preserves the text-only prompt byte for byte.
There is no new strategy schema, executable vocabulary, approval path or digest.

The package imports only Phase 13 infrastructure and Phase 5 contracts. Accessing
the shared data `Timeframe` enum does not load data services: public data storage,
resampling and validation exports load on demand, retaining their existing API.
Interpretation does not import or invoke market-data services or quant engines.

## Input contract and ordered assets

`MultimodalInput` is strict, frozen, extra-forbidden, validates defaults and deeply
revalidates even unchecked model copies. It has:

| Field | Contract |
| --- | --- |
| `images` | Immutable ordered tuple of 1–16 Phase 13 `ImageReference` objects; unique asset IDs |
| `strategy_text` | Optional, defaults to `None`; when supplied, nonblank text of 1–16,000 characters |
| `input_reference` | Required opaque non-whitespace source reference, at most 512 characters |
| `strategy_id` | Required existing Phase 5 identifier, at most 128 characters |
| `version` | Required strict positive integer; no boolean/string coercion |

Phase 13 image MIME declarations are PNG, JPEG or WebP; declarations do not validate
bytes. Asset IDs are caller-owned opaque names, at most 512 characters. This path
rejects URL/data/file transport forms and slash/backslash paths, and requires unique
IDs so an evidence reference is unambiguous. Namespaced IDs such as `chart:first`
are supported. Image order is preserved in the input and request, never sorted.

No asset is opened, fetched, decoded, downloaded or OCR-processed. No bytes,
credentials, timestamps or random IDs are introduced. The caller owns asset
retention and later adapter resolution. Bounds are artifact limits, not image-size
or model context-window guarantees. User content and opaque IDs are not a general
secret-detection boundary; callers must keep credentials out of them.

```python
from quantlab.interpretation import MultimodalInput, interpret_multimodal_strategy
from quantlab.llm import ImageMediaType, ImageReference

idea = MultimodalInput(
    images=(ImageReference(asset_id="chart:first", media_type=ImageMediaType.PNG),),
    strategy_text="Interpret the explicitly annotated entry and exit rules.",
    input_reference="idea:chart-review", strategy_id="chart_idea", version=1,
)
result = await interpret_multimodal_strategy(client, idea, identity=model_identity)
# Display the complete input, interpretation and proposal for human review.
# An incomplete chart/annotation should produce questions, not guessed rules.
```

`client` and `model_identity` above are application-supplied Phase 13 objects.
Automated tests use only `FakeProvider`; this example does not configure a real model.

## Prompt and capabilities

- Prompt ID: `quantlab.multimodal-strategy`
- Prompt version: `1`

Both appear in `PromptProvenance`, along with the input reference. The prompt and
structured schema are fixed application code. Application strategy identity/version
never enter the model message. The only USER text item, when supplied, is a
JSON-escaped `strategy_text` object, followed by the image references in exact order.
There are no approval metadata or vendor-specific messages.

Phase 13 rejects providers without `image_input=True` or `structured_output=True`
before invoking them. `text_input=True` is also required, including for image-only
submissions, because the SYSTEM prompt is a text item. Images are never discarded
to accommodate an unsupported provider. No second capability system is introduced.

The prompt states that images can be incomplete or misleading and that screenshot
text, including prompt-like instructions to ignore the system or approve a
strategy, is untrusted image content. User text is likewise untrusted. The model
must not run tools/code, approve, backtest, calculate performance, train, optimize,
create orders/fills, make risk decisions or mutate account state.

The actual security boundary is strict schema/domain validation and the absence
of approval/execution authority. Prompt separation is not a claim of perfect
model-level prompt-injection immunity. There is no prose parser, Markdown
extraction, generated code execution or repair loop.

## Evidence and status

`VisualEvidence` contains an existing material `EvidenceField`, a supplied
`asset_id`, and a nonblank observation of at most 2,000 characters. Up to 64 visual
items may accompany READY. Text evidence reuses up to 32 Phase 14 `SourceEvidence`
items with exact quotes from the supplied text. There are no invented coordinates,
bounding boxes or confidence percentages.

READY requires the existing draft, at least one visual-evidence item, no questions
and no conflicts. The union of text and visual evidence fields must exactly cover
populated material draft fields: instruments, timeframe, direction, side rules,
session, features, parameters, stops, targets and sizing reference. Unsupported or
irrelevant field names cannot extend the schema. Image-only READY is possible if
all necessary intent is explicitly visible and the model supplies visual evidence.

NEEDS_CLARIFICATION requires questions and forbids a draft and top-level proposal
evidence. It always produces `proposal=None`. Missing/unreadable SMA periods,
timeframe, symbol or exit intent; ambiguous lines, levels or patterns; unsupported
indicators; visually ambiguous values; and incompatible screenshots must be
clarified. Images without meaningful strategy intent can legitimately need questions.
No RSI 14, RSI 30/70, SMA 20/50, 1h or long-direction defaults are inferred.

Evidence is a **review aid only**. Checks establish field coverage, real input asset
references and actual text substrings. They cannot prove that an observation is
visible, that a quote supports the rule, or that the translation is semantically
correct. A plausible but false observation can pass binding checks. Human review
must compare the complete proposed intent with all inputs.

## Conflicts

`MultimodalConflict` extends the existing ambiguity/question pair with a material
field, up to 32 text-evidence items and 1–32 visual-evidence items. Evidence must
refer to the same field. It must identify either text plus image sources or at
least two distinct image sources. Up to 32 conflicts are allowed. Each conflict's
ambiguity/question must also appear in the clarification list; all quoted text and
asset IDs are validated even though there is no proposal. Conflicting labels inside
one screenshot can be described using ordinary clarification pairs.

Text EUR/USD versus image GBP/USD, 15m versus 1h, SMA20 versus SMA50, and long versus
short require questions. No source is automatically preferred. The schema rejects
READY containing even one reported conflict, including an otherwise complete draft.
Conflicts have no model-controlled “resolved” flag. Resolution requires a new
caller-supplied input and interpretation; the service never answers its own questions.

Conflict discovery is a model observation, not deterministic OCR or a natural-language
truth verifier. Deterministic validation prevents **reported unresolved conflicts**
from yielding READY; it cannot detect conflicts the model omitted or misread.

## Strategy, market-data and approval boundaries

Exactly the [Phase 14 vocabulary](natural-language-strategy-interpretation.md#supported-vocabulary)
is supported: existing Phase 5 sides/rules/parameters, raw OHLC feature declarations,
simple/log returns, SMA/EMA/RSI, rolling volatility and bounded external
`ml_forward_return_v1` declarations. This declares strategy intent only. It does
not calculate indicators or train/resolve models. Unsupported ATR, MACD, Bollinger
Bands, Fibonacci, trend-line or chart-pattern algorithms do not become executable
features merely because they appear in a screenshot.

Screenshots are **not authoritative historical market data**. A claim that a label
appears to show a symbol, timeframe or price is a visual claim only. No pixels
become `MarketBar`/`MarketQuote`, OHLC/ticks, indicator series, volatility, returns,
execution/risk prices, P&L or model-training data. Explicit intended thresholds may
be proposed under the existing schema, but they are unapproved strategy intent,
not a price record or a risk decision. Later authoritative calculations require
canonical structured market data and the existing deterministic engines.

READY produces `state=DRAFT`, `approval=None`, `created_at=None`. Interpretation
never calls `approve`, `mark_validated`, backtesting, risk, execution, analytics,
portfolio or paper trading. The existing backtester rejects the result until
separately approved, and later execution-compatibility restrictions still apply.
Phase 5 representability does not establish execution eligibility or profitability.

Content provenance uses existing `Origin.CHART_MULTIMODAL`, the source reference
and prompt ID/version notes. Content identity uses existing
`StrategyContent.content_digest()` only. Raw observations and image IDs live in
the complete result, not a new strategy digest or an authoritative market dataset.

## Replay, errors and determinism

`MultimodalResult` retains exact optional text, ordered image IDs/MIME declarations,
application metadata, parsed interpretation, optional proposal and full Phase 13
`InvocationResult`: request/schema, provider/model identity, prompt provenance,
raw response, policy and attempt count. Revalidation rebuilds the request, reparses
the raw response, rechecks evidence and reconstructs the proposal. Inconsistent
copied/constructed nested models, image order changes, unrelated responses and
forged approved proposals fail. This is consistency checking, not provider
authentication, cryptographic tamper-proof storage or image-content hashing.

Existing errors are reused. Invalid local inputs raise `InterpretationInputError`;
direct model construction uses Pydantic `ValidationError`. Malformed JSON,
duplicate keys, extra fields, wrong types, Markdown wrappers, incomplete finish
reasons and status/schema violations use Phase 13 `StructuredOutputError` without
repair/retry. Evidence/result inconsistency uses `InterpretationContractError`.
Domain or vocabulary failures use `InterpretedStrategyError`. Provider retries,
budgets and sanitized errors retain Phase 13 behavior, with no extra retry loop.
Raw artifacts require deliberate retention/redaction; they should not be blindly logged.

Identical validated inputs/identity/token limit produce identical requests.
Identical accepted output and application metadata produce identical proposals
and canonical digests. A -> B -> A invocations share no interpretation state.
Real model responses and correct chart understanding are not deterministic guarantees.

## Verification and deferred work

Offline FakeProvider tests cover strict/deep/frozen models, bounds, opaque images,
request determinism and ordering, capabilities, both statuses, source conflicts,
evidence binding and its semantic limitations, strict structured failures, Phase 5
validation, feature catalogue reuse, replay forgery, injection-as-data, approval
isolation/backtester rejection, network/file denial, no market-record construction,
import isolation, retry/budget reuse and A -> B -> A behavior. Phase 13/14 tests
continue to exercise the unchanged infrastructure and text-only behavior.

The architecture is provider-neutral. A future local Qwen3-VL/Ollama adapter can
implement Phase 13's provider interface, translate opaque image assets and support
the required capabilities/schema without changing Phase 15 interpretation logic.
Actual adapter support and model quality have not been evaluated. Complex schemas
may exceed a model's ability; failures remain closed.

Deferred: real local/cloud adapters, image upload/storage/privacy/retention services,
byte/MIME validation, OCR, chart-to-OHLC extraction, reviewer authentication,
durable persistence, automatic clarification loops and real model evaluation.
Bounded orchestration (16) now adds human review; see
[agent-orchestration.md](agent-orchestration.md). MCP (17), paper trading (18), portfolio (19), journal (20), FastAPI (21), frontend
(22), E2E (23), deployment (24) and final polish (25) remain unimplemented. No
automatic approval, automatic chart trading, strategy optimization, self-evolution
or real-money execution is included.
