"""Offline historical adapter. Dataset storage remains outside the paper core."""
from dataclasses import dataclass
from itertools import islice
from typing import Annotated

from pydantic import Field
from quantlab.strategies.schema import Digest

from quantlab.data import MarketBar, MarketQuote
from quantlab.data.storage import StoredDataset, _prepare
from quantlab.paper.errors import PaperInputError
from quantlab.paper.models import MarketDelivery, PaperContract, stable_id
from quantlab.paper.session_models import FeedProvenance, ReplayEvent
from quantlab.paper.sessions import PaperSession
from quantlab.paper.strategy_models import BarCloseDelivery


@dataclass(frozen=True)
class HistoricalReplay:
    """Validated immutable local dataset. Never classifies quotes as openings.

    Storage does not record delivery: v1 explicitly models delivery at availability.
    Overlapping recorded availability cannot be reordered; invalid chronology fails.
    Producer order is retained, including equal-time distinct observation positions.
    """
    dataset: StoredDataset

    def __post_init__(self):
        d = self.dataset
        if type(d) is not StoredDataset or d.metadata.created_at is None:
            raise PaperInputError("validated retained dataset required")
        identity, metadata, observations, quality = _prepare(d.observations, d.metadata)
        if (identity, metadata, observations, quality) != (d.dataset_id, d.metadata, d.observations, d.quality):
            raise PaperInputError("dataset identity or integrity mismatch")

    @property
    def provenance(self):
        return FeedProvenance(dataset_id=self.dataset.dataset_id,
            version_digest=self.dataset.dataset_id.removeprefix("sha256:"),
            source_id=self.dataset.metadata.source_id,
            source_reference="stored:" + self.dataset.dataset_id)

    def events(self, *, first_sequence, maximum_events):
        if (type(first_sequence) is not int or first_sequence < 1
                or type(maximum_events) is not int or not 0 <= maximum_events <= 100_000):
            raise PaperInputError("invalid historical replay bound/sequence")
        provenance = self.provenance
        for index, market in enumerate(islice(self.dataset.observations, maximum_events)):
            # The envelope references the stored version; retain original row provenance
            # by requiring the caller to bind that version alongside its original dataset ID.
            row_provenance = provenance.model_copy(update={"dataset_id": market.dataset_id})
            identity = stable_id("paper-historical-observation-v1", (provenance, index, market))
            common = dict(event_id=identity, sequence=first_sequence + index,
                timestamp=market.available_at, delivered_at=market.available_at)
            if type(market) is MarketBar:
                obs = BarCloseDelivery(**common, bar=market)
                kind, occurrence = "bar_close", market.end_time
            elif type(market) is MarketQuote:
                obs = MarketDelivery(**common, quote=market)
                kind, occurrence = "quote", market.timestamp
            else:
                raise PaperInputError("unsupported historical observation")
            yield ReplayEvent(**common, kind=kind, provenance=row_provenance,
                occurred_at=occurrence, available_at=market.available_at, observation=obs)

    @property
    def sources(self):
        # Sources bind the immutable stored digest and original provider row IDs.
        base = self.provenance
        return tuple(dict.fromkeys(base.model_copy(update={"dataset_id": row.dataset_id})
            for row in self.dataset.observations)) or (base,)


class ReplayVerification(PaperContract):
    input_count: Annotated[int, Field(ge=0)]
    outcome_digest: Digest
    verified: bool


def verify_replay(config, inputs, *, strategy, policy, eligibility, evidence):
    """Run bounded identical recorded inputs in two fresh isolated owners.

    The final content-addressed audit chain commits every preceding outcome.
    Verification serializes this small final projection, not the entire history.
    """
    if type(inputs) is not tuple or len(inputs) > config.maximum_inputs + 1:
        raise PaperInputError("bounded immutable recorded input tuple required")
    digests = []
    for _ in range(2):
        session = PaperSession(config, strategy=strategy, policy=policy,
            eligibility=eligibility, evidence=evidence)
        for item in inputs:
            session.process(item)
        p = session._publication
        digests.append(stable_id("paper-replay-result-v1", (session._config_digest,
            p.record_id, p.count, p.state, p.clock, p.feed, p.account.snapshot,
            p.runtime.admission, p.runtime._publication.intent,
            p.adapter.snapshot, p.adapter.openings)))
    return ReplayVerification(input_count=len(inputs), outcome_digest=digests[0],
        verified=digests[0] == digests[1])
