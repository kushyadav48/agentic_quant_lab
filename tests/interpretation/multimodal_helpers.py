import asyncio
import json

from quantlab.interpretation import MultimodalInput, interpret_multimodal_strategy
from quantlab.llm import Capabilities, FakeProvider, ImageMediaType, ImageReference, LLMClient, LLMResponse, ProviderInfo
from tests.llm.helpers import IDENTITY
from .helpers import TEXT, ready as text_ready

IMAGES = (ImageReference(asset_id="chart:first", media_type=ImageMediaType.PNG),
          ImageReference(asset_id="chart:second", media_type=ImageMediaType.JPEG))
INFO = ProviderInfo(identity=IDENTITY, capabilities=Capabilities(image_input=True, structured_output=True))
FIELDS = ("instruments", "timeframe", "direction", "long", "features", "parameters")


def source(**changes):
    return MultimodalInput(**(dict(images=IMAGES, strategy_text=TEXT,
        input_reference="idea:15", strategy_id="chart_idea", version=1) | changes))


def visual(field="instruments", asset_id="chart:first", observation="The label appears to show research:TEST."):
    return dict(field=field, asset_id=asset_id, observation=observation)


def ready():
    return text_ready() | dict(visual_evidence=[visual()], conflicts=[])


def visual_ready():
    observations = ("The symbol label appears to show research:TEST.",
        "The timeframe label appears to show 1m.", "The annotation says long only.",
        "The annotation says enter on close crossing above SMA AND above threshold; exit below threshold.",
        "The overlay label says SMA of 2 closes.",
        "The annotation specifies integer threshold default 100, minimum 1, maximum 200.")
    return ready() | dict(evidence=[], visual_evidence=[
        visual(field, observation=observation) for field, observation in zip(FIELDS, observations)])


def clarification(ambiguity="SMA period is unreadable", question="Which SMA period?"):
    return dict(status="NEEDS_CLARIFICATION", draft=None, evidence=[], visual_evidence=[],
                conflicts=[], clarifications=[dict(ambiguity=ambiguity, question=question)])


def conflict(field="instruments", quote="EUR/USD", observation="The label appears to show GBP/USD.", *, images_only=False):
    pair = dict(ambiguity="The sources disagree about " + field, question="Which " + field + " is intended?")
    evidence = [visual(field, observation=observation)]
    if images_only:
        evidence.append(visual(field, "chart:second", "The second image shows a different setup."))
    return clarification(**pair) | dict(conflicts=[dict(**pair, field=field,
        text_evidence=[] if images_only else [dict(field=field, quote=quote)], visual_evidence=evidence)])


def raw(value=None, **changes):
    return LLMResponse(**(dict(identity=IDENTITY, text=json.dumps(ready() if value is None else value)) | changes))


def run(value=None, *, input=None, policy=None):
    fake = FakeProvider(INFO, (raw(value),))
    client = LLMClient(fake, **({} if policy is None else {"policy": policy}))
    return asyncio.run(interpret_multimodal_strategy(client, source() if input is None else input,
                                                    identity=IDENTITY)), fake
