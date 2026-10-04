import asyncio
import json

from pydantic import ValidationError
import pytest

from quantlab.interpretation import (
    InterpretationContractError, InterpretationInputError, MultimodalInput,
    MultimodalInterpretation, MultimodalResult, VisualEvidence,
    build_multimodal_request, interpret_multimodal_strategy,
)
from quantlab.llm import FakeProvider, ImageReference, LLMClient
from tests.llm.helpers import IDENTITY
from .multimodal_helpers import IMAGES, INFO, clarification, conflict, raw, ready, run, source, visual


@pytest.mark.parametrize("changes", [
    {"images": ()}, {"images": IMAGES * 9}, {"images": list(IMAGES)}, {"images": ("chart",)},
    {"images": (b"png",)}, {"images": (IMAGES[0].model_copy(update={"media_type": "image/gif"}),)},
    {"images": (IMAGES[0].model_copy(update={"asset_id": 2}),)},
    {"images": (ImageReference.model_construct(asset_id="x", media_type=None),)},
    {"images": (IMAGES[0], IMAGES[0])},
    {"strategy_text": ""}, {"strategy_text": " \n\t"}, {"strategy_text": "a" * 16001},
    {"strategy_text": 1}, {"strategy_text": b"text"}, {"strategy_text": True},
    {"input_reference": ""}, {"input_reference": "two words"}, {"input_reference": "a" * 513},
    {"strategy_id": "a.b"}, {"strategy_id": 1}, {"strategy_id": "a" * 129},
    {"version": 0}, {"version": True}, {"version": "1"}, {"version": 1.0},
    {"api_key": "secret"}, {"image_bytes": b"png"}, {"created_at": "today"}, {"approval": None},
])
def test_strict_bounded_input(changes):
    with pytest.raises(ValidationError):
        source(**changes)


@pytest.mark.parametrize("asset", ["https://example.test/chart.png", "http:chart", "file:chart",
    "data:image/png;base64,abc", "C:\\images\\chart.png", "/tmp/chart.png", "folder/chart.png"])
def test_transport_locations_are_not_asset_ids(asset):
    image = ImageReference(asset_id=asset, media_type=IMAGES[0].media_type)
    with pytest.raises(ValidationError):
        source(images=(image,))
    with pytest.raises(ValidationError):
        VisualEvidence(**visual(asset_id=asset))


def test_input_roundtrip_order_bounds_optional_text_and_frozen():
    images = tuple(ImageReference(asset_id=f"chart:{i}", media_type=IMAGES[0].media_type) for i in range(16))
    value = source(images=images, strategy_text="x" * 16000,
                   strategy_id="a" * 128, input_reference="a" * 512)
    assert value.images == images
    assert MultimodalInput.model_validate_json(value.model_dump_json()) == value
    assert source(images=(IMAGES[0],), strategy_text=None).strategy_text is None
    assert MultimodalInput(images=IMAGES, input_reference="x", strategy_id="x", version=1).strategy_text is None
    for model, field, replacement in ((value, "images", ()), (value.images[0], "asset_id", "other")):
        with pytest.raises(ValidationError):
            setattr(model, field, replacement)


@pytest.mark.parametrize("value", [None, {}, "text", source().model_copy(update={"images": ()}),
    source().model_copy(update={"images": (IMAGES[0].model_copy(update={"asset_id": "https://host"}),)}),
    MultimodalInput.model_construct(images=IMAGES, input_reference="x", strategy_id="x", version=True)])
def test_unchecked_input_is_rejected_before_call(value):
    fake = FakeProvider(INFO, (raw(),))
    with pytest.raises(InterpretationInputError):
        asyncio.run(interpret_multimodal_strategy(LLMClient(fake), value, identity=IDENTITY))
    assert not fake.history
    with pytest.raises(InterpretationInputError):
        build_multimodal_request(value, identity=IDENTITY)


@pytest.mark.parametrize("changes", [
    {"draft": None}, {"visual_evidence": []}, {"clarifications": clarification()["clarifications"]},
    {"conflicts": conflict()["conflicts"]}, {"status": "ready"}, {"status": 1},
    {"confidence": 0.99}, {"visual_evidence": [visual()] * 65}, {"conflicts": None},
])
def test_ready_status_invariants(changes):
    with pytest.raises(ValidationError):
        MultimodalInterpretation.model_validate_json(json.dumps(ready() | changes))


@pytest.mark.parametrize("changes", [
    {"draft": ready()["draft"]}, {"evidence": ready()["evidence"]},
    {"visual_evidence": [visual()]}, {"clarifications": []},
    {"clarifications": clarification()["clarifications"] * 33},
    {"conflicts": conflict()["conflicts"]},
])
def test_clarification_invariants(changes):
    with pytest.raises(ValidationError):
        MultimodalInterpretation.model_validate_json(json.dumps(clarification() | changes))


@pytest.mark.parametrize("changes", [{"field": "ohlc"}, {"observation": " "},
    {"observation": "x" * 2001}, {"observation": 1}, {"asset_id": ""},
    {"asset_id": "a" * 513}, {"confidence": 99}, {"bbox": [0, 1, 2, 3]}])
def test_visual_evidence_is_strict(changes):
    with pytest.raises(ValidationError):
        VisualEvidence(**(visual() | changes))


@pytest.mark.parametrize("case", ["field", "no_image", "one_source", "too_many"])
def test_conflict_evidence_contract(case):
    wire = conflict()
    item = wire["conflicts"][0]
    if case == "field":
        item["visual_evidence"][0]["field"] = "timeframe"
    elif case == "no_image":
        item["visual_evidence"] = []
    elif case == "one_source":
        item["text_evidence"] = []
    else:
        wire["conflicts"] *= 33
    with pytest.raises(ValidationError):
        MultimodalInterpretation.model_validate_json(json.dumps(wire))


def test_result_roundtrip_and_deep_frozen_revalidation():
    result, _ = run()
    assert MultimodalResult.model_validate_json(result.model_dump_json()) == result
    for model, field, value in ((result, "proposal", None), (result.interpretation, "conflicts", ()),
        (result.interpretation.visual_evidence[0], "observation", "changed"),
        (result.interpretation.draft.features[0].parameters[0], "value", 50)):
        with pytest.raises(ValidationError):
            setattr(model, field, value)
    forged = result.interpretation.model_copy(update={"visual_evidence": (
        result.interpretation.visual_evidence[0].model_copy(update={"observation": 1}),)})
    with pytest.raises(ValidationError):
        MultimodalInterpretation.model_validate(forged)


@pytest.mark.parametrize("case", ["images_order", "images_id", "media_type", "text", "reference",
    "strategy_id", "version", "proposal", "prompt", "response", "interpretation", "provider_identity"])
def test_replay_rejects_mutated_artifacts(case):
    result, _ = run()
    if case in ("images_order", "images_id", "media_type", "text", "reference", "strategy_id", "version"):
        changes = {"images_order": dict(images=tuple(reversed(IMAGES))),
            "images_id": dict(images=(IMAGES[1],)),
            "media_type": dict(images=(ImageReference(asset_id=IMAGES[0].asset_id,
                media_type=IMAGES[1].media_type), IMAGES[1])),
            "text": dict(strategy_text="unrelated"), "reference": dict(input_reference="other"),
            "strategy_id": dict(strategy_id="different"), "version": dict(version=2)}[case]
        forged = result.model_copy(update={"input": source(**changes)})
    elif case == "proposal":
        forged = result.model_copy(update={"proposal": None})
    elif case == "interpretation":
        forged = result.model_copy(update={"interpretation": MultimodalInterpretation.model_validate_json(json.dumps(clarification()))})
    else:
        invocation = result.invocation
        if case == "response":
            invocation = invocation.model_copy(update={"response": raw(clarification())})
        elif case == "provider_identity":
            invocation = invocation.model_copy(update={"response": raw(identity=IDENTITY.model_copy(update={"model_id": "different"}))})
        else:
            req = invocation.request.model_copy(update={"messages": invocation.request.messages[1:]})
            invocation = invocation.model_copy(update={"request": req})
        forged = result.model_copy(update={"invocation": invocation})
    with pytest.raises((InterpretationContractError, ValidationError)):
        MultimodalResult.model_validate(forged)
