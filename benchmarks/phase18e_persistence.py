"""Real durable-file benchmark; observations only, no latency assertions.

Run: python benchmarks/phase18e_persistence.py --counts 100 1000 5000
Evidence, event generation and consumer snapshots are outside processing timings.
SQLite DELETE/EXTRA durability is never relaxed for benchmarking.
"""
import argparse
from contextlib import ExitStack
from datetime import timedelta
import json
from pathlib import Path
import platform
import sqlite3
import sys
from statistics import median
from unittest.mock import patch
from tempfile import TemporaryDirectory
from time import perf_counter

from quantlab.paper import MarketDelivery
from quantlab.persistence import DurablePaperSession, SQLitePaperStore, StoragePolicy
from tests.paper.test_sessions import T, command, event, inputs, session
from tests.paper.strategy_helpers import delivery, close_quote
from tests.backtesting.helpers import MINUTE


def recovery_orders(repeats):
    """Both modes run on each trial; alternate which mode receives warm caches."""
    return [(False, True) if trial % 2 == 0 else (True, False) for trial in range(repeats)]


def recover_once(path, reference, args, policy, expected, use_checkpoint):
    timings = dict(open_seconds=0.0, integrity_seconds=0.0,
        checkpoint_validation_seconds=0.0, prefix_restore_seconds=0.0,
        replay_processing_seconds=0.0)

    def timed(name, original):
        def call(*a, **kw):
            began = perf_counter()
            try:
                return original(*a, **kw)
            finally:
                timings[name] += perf_counter() - began
        return call

    # Instrument only the benchmark. Production recovery has no timing callbacks.
    with ExitStack() as stack:
        for method, phase in (("_verify_checkpoint", "checkpoint_validation_seconds"),
                              ("_restore_checkpoint", "prefix_restore_seconds"),
                              ("process", "replay_processing_seconds")):
            stack.enter_context(patch.object(DurablePaperSession, method,
                timed(phase, getattr(DurablePaperSession, method))))
        began = perf_counter()
        store = SQLitePaperStore(path)
        timings["open_seconds"] = perf_counter() - began
        try:
            store.verify = timed("integrity_seconds", store.verify)
            recovered = DurablePaperSession.recover(store, reference.config, storage_policy=policy,
                                                    use_checkpoint=use_checkpoint, **args)
            elapsed = perf_counter() - began
            # Consumer snapshot materialization is outside measured recovery.
            assert recovered.snapshot == expected
            timings["other_recovery_seconds"] = elapsed - sum(timings.values())
            return dict(use_checkpoint=use_checkpoint, seconds=elapsed, phases=timings,
                checkpoint_ordinal=recovered.recovery_checkpoint_ordinal,
                prefix_verified_inputs=recovered.recovery_checkpoint_ordinal,
                replayed_inputs=recovered.recovery_replayed_inputs)
        finally:
            store.close()


def measure(kind, count, interval, repeats=3):
    reference, args = session(maximum_inputs=count + 10, maximum_events=min(count, 10000))
    bar = delivery(close=99, sequence=2)
    quote = close_quote(bar)
    generated = list(inputs()[1:]) if kind == "entry-and-bars" else []
    for i in range(count - len(generated)):
        seq = i + (6 if kind == "entry-and-bars" else 2)
        if kind == "quotes":
            t = quote.timestamp + i * timedelta(microseconds=1)
            obs = MarketDelivery(event_id=f"quote-{i}", sequence=seq, timestamp=t,
                delivered_at=t, quote=quote.quote.model_copy(update={"timestamp": t, "available_at": t}))
        else:
            delta = (i + 1) * MINUTE
            b = bar.bar.model_copy(update={"start_time": bar.bar.start_time + delta,
                "end_time": bar.bar.end_time + delta, "available_at": bar.bar.available_at + delta})
            obs = bar.model_copy(update={"event_id": f"bar-{i}", "sequence": seq,
                "timestamp": b.end_time, "delivered_at": b.end_time, "bar": b})
        generated.append(event(obs))
    start = command("start")
    reference.process(start)
    began = perf_counter()
    reference.replay(generated, maximum_events=count)
    baseline = perf_counter() - began
    policy = StoragePolicy(checkpoint_interval=interval)
    with TemporaryDirectory(prefix="paper-18e-", dir="tmp") as directory:
        path = Path(directory) / "paper.db"
        began = perf_counter()
        store = SQLitePaperStore(path)
        owner = DurablePaperSession(store, reference.config, storage_policy=policy, **args)
        owner.process(start)
        creation = perf_counter() - began
        halfway = count // 2
        began = perf_counter()
        owner.replay(generated[:halfway], maximum_events=halfway)
        first = perf_counter() - began
        began = perf_counter()
        checkpoint = owner.checkpoint()
        checkpoint_seconds = perf_counter() - began
        began = perf_counter()
        owner.replay(generated[halfway:], maximum_events=count - halfway)
        second = perf_counter() - began
        expected = owner.snapshot
        assert expected == reference.snapshot
        checkpoints = len(store.checkpoints())
        checkpoint_bytes = len(checkpoint.canonical_json().encode())
        store.close()
        observations = {False: [], True: []}
        for trial, order in enumerate(recovery_orders(repeats), 1):
            print(f"{kind}: recovery trial {trial}/{repeats}, order={order}", file=sys.stderr, flush=True)
            for position, use_checkpoint in enumerate(order, 1):
                observation = recover_once(path, reference, args, policy, expected, use_checkpoint)
                observations[use_checkpoint].append(dict(trial=trial, position=position, **observation))
        recoveries = []
        for use_checkpoint, samples in observations.items():
            values = [s["seconds"] for s in samples]
            recoveries.append(dict(use_checkpoint=use_checkpoint, seconds=median(values),
                minimum_seconds=min(values), maximum_seconds=max(values), samples=samples,
                phases_median={key: median(s["phases"][key] for s in samples) for key in samples[0]["phases"]},
                checkpoint_ordinal=samples[0]["checkpoint_ordinal"],
                prefix_verified_inputs=samples[0]["prefix_verified_inputs"],
                replayed_inputs=samples[0]["replayed_inputs"]))
        size = path.stat().st_size
    persisted = first + second
    return dict(workload=kind, market_events=count, committed_inputs=count + 1,
        initial_journal_seconds=creation, in_memory_seconds=baseline,
        persisted_seconds=persisted, first_half_seconds=first, second_half_seconds=second,
        persisted_events_per_second=count / persisted,
        persistence_overhead_seconds=persisted - baseline,
        checkpoint_seconds=checkpoint_seconds, checkpoint_bytes=checkpoint_bytes,
        retained_checkpoints=checkpoints, database_bytes=size, recovery=recoveries,
        final_record_id=expected.records[-1].record_id, final_fees=str(expected.account.fees_paid))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", type=int, nargs="+", default=[100, 1000, 5000])
    parser.add_argument("--checkpoint-interval", type=int, default=1000)
    parser.add_argument("--recovery-repeats", type=int, default=3)
    args = parser.parse_args()
    if any(not 4 <= n <= 10000 for n in args.counts):
        parser.error("counts must be between 4 and 10000")
    if not 2 <= args.recovery_repeats <= 10:
        parser.error("recovery repeats must be between 2 and 10")
    results = []
    for kind in ("quotes", "entry-and-bars"):
        for n in args.counts:
            print(f"Benchmark {kind}, {n} events", file=sys.stderr, flush=True)
            results.append(measure(kind, n, args.checkpoint_interval, args.recovery_repeats))
    print(json.dumps(dict(environment=dict(python=sys.version, platform=platform.platform(),
        sqlite=sqlite3.sqlite_version, machine=platform.machine() or "unreported",
        processor=platform.processor() or "unreported"), configuration=dict(journal="DELETE",
        synchronous="EXTRA (3)", locking="EXCLUSIVE", checkpoint_interval=args.checkpoint_interval,
        storage="temporary local file under workspace tmp", writer="one serialized process",
        processing_excludes="evidence/event construction, explicit checkpoint, snapshots",
        processing_includes="engine preparation, strict codec validation, transaction sync and scheduled checkpoints",
        recovery_includes="file open, admission checks, all-record verification, index restoration and replay",
        checkpoint_retention=2, recovery_repeats=args.recovery_repeats,
        recovery_statistic="median; raw samples and range retained",
        recovery_order="alternating full/checkpoint and checkpoint/full",
        persistence_repeats=1,
        phase_timing_note="replay processing excludes effect comparison; other includes admission, checkpoint decoding and comparison; marginal phase medians need not sum to total median",
        limitations="shared Windows host; warm filesystem cache; no physical power-loss certification; timing wrappers add small overhead"), results=results), indent=2))


if __name__ == "__main__":
    main()
