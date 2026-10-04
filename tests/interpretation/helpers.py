import asyncio
import json

from quantlab.interpretation import InterpretationInput, interpret_strategy
from quantlab.llm import FakeProvider, LLMClient, LLMResponse
from tests.llm.helpers import IDENTITY, INFO

TEXT = ("Trade research:TEST on 1m bars, long only. Enter when close crosses above "
        "the SMA of 2 closes AND close is greater than threshold. Exit when close "
        "is below threshold. Integer parameter threshold defaults to 100 with "
        "minimum 1 and maximum 200. No other restrictions.")


def source(**changes):
    return InterpretationInput(**(dict(strategy_text=TEXT, input_reference="idea:14",
        strategy_id="interpreted", version=1) | changes))


def ready():
    return dict(status="READY", draft=dict(
        name="SMA threshold idea", description=None,
        instruments=["research:TEST"], timeframe="1m", direction="long",
        long=dict(entry=dict(mode="all", rules=[
            dict(left=dict(kind="market", field="close"), comparison="crosses_above",
                 right=dict(kind="feature", feature_id="fast")),
            dict(left=dict(kind="market", field="close"), comparison="gt",
                 right=dict(kind="parameter", name="threshold")),
        ]), exit=dict(mode="all", rules=[
            dict(left=dict(kind="market", field="close"), comparison="lt",
                 right=dict(kind="parameter", name="threshold")),
        ])), short=None, session=None,
        features=[dict(feature_id="fast", implementation_id="sma", feature_type="indicator",
                       parameters=[dict(name="period", value=2)])],
        parameters=[dict(name="threshold", type="integer", default=100, minimum=1, maximum=200)],
        stop_loss=None, take_profit=None, sizing_reference=None),
        clarifications=[], evidence=[dict(field=field, quote=TEXT) for field in
            ("instruments", "timeframe", "direction", "long", "features", "parameters")])


def clarification(ambiguity="The SMA period is missing.", question="Which SMA period should be used?"):
    return dict(status="NEEDS_CLARIFICATION", draft=None, evidence=[],
                clarifications=[dict(ambiguity=ambiguity, question=question)])


def raw(value=None, **changes):
    return LLMResponse(**(dict(identity=IDENTITY,
        text=json.dumps(ready() if value is None else value)) | changes))


def run(value=None, *, input=None, policy=None):
    fake = FakeProvider(INFO, (raw(value),))
    client = LLMClient(fake, **({} if policy is None else {"policy": policy}))
    return asyncio.run(interpret_strategy(client, source() if input is None else input,
                                         identity=IDENTITY)), fake
