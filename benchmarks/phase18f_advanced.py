"""Offline advanced-order latency, persistence and recovery; no assertions on time."""
import argparse
from decimal import Decimal as D
import cProfile
import json
from pathlib import Path
import platform
import pstats
import sqlite3
import statistics
import sys
import tempfile
from time import perf_counter

from tests.paper.test_advanced import kernel, quote, owned_entry
from tests.persistence.test_advanced import setup, execute, exit_command, q, reopened
from tests.paper.test_sessions import command, control, T
from tests.backtesting.helpers import MINUTE
from quantlab.paper import OrderState, StopTrigger, AdvancedFillRecord


def summarize(samples):
    ordered = sorted(samples)
    return dict(events=len(samples), median_ms=statistics.median(samples)*1000,
        p95_ms=ordered[min(len(ordered)-1, int(len(ordered)*0.95))]*1000,
        total_seconds=sum(samples), events_per_second=len(samples)/sum(samples))


def work(repeats, observations):
    results = {}
    for name in ("market", "resting_limit", "stop_activation", "partial_sequence", "protective_exit"):
        samples = []
        phase_samples = {}
        for _ in range(repeats):
            if name == "protective_exit":
                owner, _ = setup(); execute(owner)
                owner.process(exit_command(owner, order_type="stop_market", stop_price=D("96"),
                    protective_role="stop_loss"))
                events = [q(7,"95"), q(8,"94"), q(9,"93")]
                process = owner.process
            else:
                kwargs = {"maximum_age": 1000*MINUTE}
                if name == "resting_limit":
                    kwargs.update(order_type="limit", limit_price=D("99"))
                elif name == "stop_activation":
                    kwargs.update(order_type="stop_market", stop_price=D("103"))
                elif name == "partial_sequence":
                    kwargs.update(budget="1", quantity=str(observations))
                owner = kernel(**kwargs)
                count = 1 if name == "market" else observations
                events = [quote(n, ask="104" if name == "stop_activation" else "102") for n in range(3,3+count)]
                process = owner.process
            for item in events:
                start = perf_counter(); output = process(item); elapsed = perf_counter()-start
                samples.append(elapsed)
                # Classification happens outside the measured processing interval.
                if name in ("stop_activation", "protective_exit"):
                    records = output.orders if name == "protective_exit" else output
                    if any(isinstance(record, StopTrigger) for record in records):
                        phase = "trigger"
                    else:
                        fill = next((record for record in records if isinstance(record, AdvancedFillRecord)), None)
                        phase = "terminal_delivery" if fill is None else (
                            "final_fill" if fill.remaining_quantity == 0 else "partial_fill")
                    phase_samples.setdefault(phase, []).append(elapsed)
        results[name] = summarize(samples)
        if phase_samples:
            results[name]["phase_breakdown"] = {phase: summarize(values) for phase, values in phase_samples.items()}
    return results


def durable(count, repeats, directory):
    with tempfile.TemporaryDirectory(dir=directory, prefix="phase18f-") as temp:
        path = Path(temp)/"session.db"
        owner, store, reference, owners, policy = setup(path)
        execute(owner); execute(reference)
        items = [exit_command(owner, order_type="stop_market", stop_price=D("96"), protective_role="stop_loss"),
            q(7,"95"), q(8,"94"), q(9,"93")]
        items.extend(control("heartbeat", n, T+(n-6)*MINUTE) for n in range(10,count+10))
        samples = []
        for item in items:
            start=perf_counter(); actual=owner.process(item); samples.append(perf_counter()-start)
            assert actual == reference.process(item)
        expected = owner.snapshot
        owner.checkpoint(); store.close()
        recovery = {"full":[], "checkpoint":[]}
        for repeat in range(repeats):
            for mode in (("full","checkpoint") if repeat%2 == 0 else ("checkpoint","full")):
                start=perf_counter(); restored, store=reopened(path, reference.config, owners, policy, mode=="checkpoint")
                recovery[mode].append(perf_counter()-start)
                assert restored.snapshot == expected
                store.close()
        return dict(processing=summarize(samples), recovery={k:dict(median_seconds=statistics.median(v),
            samples_seconds=v) for k,v in recovery.items()}, db_bytes=path.stat().st_size,
            fees=str(expected.account.fees_paid), realized_pnl=str(expected.account.realized_pnl))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--repeats",type=int,default=10)
    parser.add_argument("--observations",type=int,default=32)
    parser.add_argument("--persisted-events",type=int,default=90)
    parser.add_argument("--recovery-repeats",type=int,default=3)
    parser.add_argument("--profile",type=Path)
    args=parser.parse_args()
    if not 1 <= args.observations <= 100 or not 1 <= args.persisted_events <= 90:
        parser.error("observations 1..100; persisted heartbeat events 1..90")
    root=Path(__file__).resolve().parents[1]
    if args.profile:
        profile=cProfile.Profile(); profile.enable()
        work(2,args.observations)
        durable(20,1,root/"tmp")
        profile.disable()
        with args.profile.open("w") as stream:
            pstats.Stats(profile,stream=stream).sort_stats("cumulative").print_stats(35)
        return
    print(json.dumps(dict(environment=dict(python=sys.version,platform=platform.platform(),
        sqlite=sqlite3.sqlite_version, processor=platform.processor()),
        workload=vars(args)|{"profile":None},
        method="one serialized writer; warm shared host; event construction/snapshots outside timings; DELETE/EXTRA; checkpoint interval 2; recovery includes admission and complete verification; no SLA",
        in_memory=work(args.repeats,args.observations),
        durable=durable(args.persisted_events,args.recovery_repeats,root/"tmp")),indent=2))


if __name__ == "__main__":
    main()
