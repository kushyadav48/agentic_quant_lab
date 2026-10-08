"""Reproducible offline Phase 18D benchmark; timings are observations, not gates.

Run from repository root: python benchmarks/phase18d_replay.py --counts 1000 5000
Fixtures generate formally approved strategy and verified research evidence once
per workload before timing. No network, background service or persistent session.
"""
import argparse
import json
import platform
import struct
import sys
from datetime import timedelta
from time import perf_counter

from quantlab.paper import MarketDelivery, ReplayEvent, SessionCommand
from tests.paper.test_sessions import SOURCE, T, event, inputs, session
from tests.paper.strategy_helpers import delivery, close_quote
from tests.backtesting.helpers import MINUTE, group, rule, strategy
from quantlab.strategies import FeatureOperand, FeatureReference, FeatureType


def workload(kind, count):
    args = {}
    if kind == "raw-offset-bars":
        f = FeatureReference(feature_id="close", implementation_id="close", feature_type=FeatureType.INDICATOR)
        args["spec"] = strategy(no_exit=True, features=(f,),
            entry=group(rule(left=FeatureOperand(feature_id="close", offset=100))))
    owner, _ = session(maximum_inputs=count + 5, maximum_events=min(10000, count), **args)
    bar = delivery(close=99, sequence=2)
    quote = close_quote(bar)
    generated = []
    if kind == "entry-and-bars":
        generated.extend(inputs()[1:])
    for i in range(count - len(generated)):
        sequence = i + (6 if kind == "entry-and-bars" else 2)
        delta = (i + (1 if kind == "entry-and-bars" else 0)) * MINUTE
        if kind == "quotes":
            t = quote.timestamp + i * timedelta(microseconds=1)
            obs = MarketDelivery(event_id=f"quote-{i}", sequence=sequence, timestamp=t,
                delivered_at=t, quote=quote.quote.model_copy(update={"timestamp": t, "available_at": t}))
        else:
            b = bar.bar.model_copy(update={"start_time": bar.bar.start_time + delta,
                "end_time": bar.bar.end_time + delta, "available_at": bar.bar.available_at + delta})
            obs = bar.model_copy(update={"event_id": f"bar-{i}", "sequence": sequence,
                "timestamp": b.end_time, "delivered_at": b.end_time, "bar": b})
        generated.append(event(obs))
    owner.start(SessionCommand(command_id="start", action="start", sequence=1,
        timestamp=T, reason_reference="benchmark:offline"))
    started = perf_counter()
    owner.replay(generated, maximum_events=count)
    elapsed = perf_counter() - started
    p = owner._publication
    return dict(workload=kind, market_events=count, total_inputs=p.count,
        seconds=elapsed, events_per_second=count / elapsed,
        replay_config_version=owner.config.version, stale_policy_version=owner.config.stale.version,
        stale_threshold_microseconds=0, maximum_runtime_events=owner.config.strategy.maximum_events,
        strategy_digest=owner.config.strategy.strategy_digest,
        final_state_version=p.account.snapshot.state_version,
        final_fees=str(p.account.snapshot.fees_paid), final_record_id=p.record_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", type=int, nargs="+", default=[1000, 5000])
    args = parser.parse_args()
    if any(not 3 <= n <= 10000 for n in args.counts):
        parser.error("counts must be between 3 and 10000")
    results = [workload(kind, n) for kind in ("quotes", "bars", "raw-offset-bars", "entry-and-bars")
        for n in args.counts]
    print(json.dumps(dict(environment=dict(python=sys.version, platform=platform.platform(),
        machine=platform.machine() or "unreported", processor=platform.processor() or "unreported",
        pointer_bits=struct.calcsize("P") * 8, execution="serialized embedded Windows CPython"),
        configuration=dict(account="prefunded EQUITY USD", opening="declared synthetic locked on-time",
            pricing="Decimal; existing Phase 18A–18C costs and risk", event_creation="excluded from processing time",
            snapshot_materialization="excluded; explicit consumer operation"), results=results), indent=2))


if __name__ == "__main__":
    main()
