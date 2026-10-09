"""Audited strategy entry/persistence workload; measurements are not an SLA."""
import argparse
import cProfile
import gc
import json
from pathlib import Path
import platform
import pstats
import sqlite3
import statistics
import sys
import tempfile
from time import perf_counter
from decimal import Decimal as D

from quantlab.paper import AdvancedFillRecord, StopTrigger, OrderState
from quantlab.persistence import DurablePaperSession, SQLitePaperStore, StoragePolicy
from tests.paper.advanced_entry_helpers import session, configuration, prefix, quote, T
from tests.paper.test_sessions import control
from tests.persistence.test_advanced_strategy_entries import recover, chain
from tests.backtesting.helpers import MINUTE
from benchmarks.phase18f_advanced import summarize


class GCAudit:
    def __init__(self):
        self.pending = {}
        self.events = []
    def callback(self, phase, info):
        generation = info["generation"]
        if phase == "start":
            self.pending[generation] = perf_counter()
        elif generation in self.pending:
            self.events.append(dict(generation=generation,
                seconds=perf_counter()-self.pending.pop(generation)))
    def __enter__(self):
        gc.callbacks.append(self.callback)
        return self
    def __exit__(self, *args):
        gc.callbacks.remove(self.callback)
    def summary(self):
        return dict(collections=len(self.events), major_collections=sum(e["generation"]==2 for e in self.events),
            total_seconds=sum(e["seconds"] for e in self.events),
            maximum_seconds=max((e["seconds"] for e in self.events),default=0))


def work(repeats, observations):
    result = {}
    for name in ("market", "resting_limit", "stop_market", "stop_limit", "partial_sequence"):
        samples, phases, gc_events = [], {}, []
        for _ in range(repeats):
            kind = "market" if name == "partial_sequence" else "limit" if name == "resting_limit" else name
            owner, _ = session(kind=kind, quantity=D(observations) if name == "partial_sequence" else D("2"),
                capital="50000",maximum_inputs=256)
            if name == "resting_limit":
                items = (*prefix(),*(quote(n,"101") for n in range(6,6+observations)),
                    quote(6+observations,"100"),quote(7+observations,"99"))
            elif name == "partial_sequence":
                items = (*prefix(),*(quote(n,"101") for n in range(6,5+observations)))
            else:
                items = chain(kind)
            for item in items:
                with GCAudit() as audit:
                    start = perf_counter()
                    output = owner.process(item)
                    elapsed = perf_counter()-start
                samples.append(elapsed); gc_events.extend(audit.events)
                fill = next((r for r in output.orders if isinstance(r,AdvancedFillRecord)),None)
                phase = "trigger" if any(isinstance(r,StopTrigger) for r in output.orders) else (
                    "final_fill" if fill is not None and fill.remaining_quantity == 0 else
                    "partial_fill" if fill is not None else output.reason)
                phases.setdefault(phase,[]).append(elapsed)
            expected = owner.snapshot
            assert expected.execution.kernel.state is OrderState.FILLED
            assert expected.account.position.quantity == (observations if name == "partial_sequence" else 2)
            assert expected.account.fees_paid == D("1.1")*expected.account.position.quantity
        audit = GCAudit(); audit.events = gc_events
        result[name] = summarize(samples)|dict(phases={k:summarize(v) for k,v in phases.items()},gc=audit.summary())
    return result


def durable(count, repeats, directory):
    with tempfile.TemporaryDirectory(dir=directory,prefix="entry-bench-") as temp:
        path=Path(temp)/"entries.db"
        config,owners=configuration(kind="stop_market")
        policy=StoragePolicy(checkpoint_interval=2)
        store=SQLitePaperStore(path)
        owner=DurablePaperSession(store,config,storage_policy=policy,**owners)
        reference,_=session(kind="stop_market")
        items=(*chain("stop_market"),*(control("heartbeat",n,T+(n-4)*MINUTE) for n in range(9,9+count)))
        samples,phases,wire_bytes,gc_events=[],{},[],[]
        for item in items:
            with GCAudit() as current_audit:
                start=perf_counter(); actual=owner.process(item); elapsed=perf_counter()-start
            gc_events.extend(current_audit.events)
            samples.append(elapsed)
            fill=next((r for r in actual.orders if isinstance(r,AdvancedFillRecord)),None)
            phase="trigger" if any(isinstance(r,StopTrigger) for r in actual.orders) else (
                "final_fill" if fill is not None and fill.remaining_quantity == 0 else
                "partial_fill" if fill is not None else actual.reason)
            phases.setdefault(phase,[]).append(elapsed)
            assert actual == reference.process(item)
            entry=store.lookup(actual.input_id)
            wire_bytes.append((len(entry.output),len(entry.effects)))
        audit=GCAudit(); audit.events=gc_events
        expected=owner.snapshot
        owner.checkpoint();store.close()
        recovery={"full":[],"checkpoint":[]}
        for repeat in range(repeats):
            for mode in (("full","checkpoint") if repeat%2==0 else ("checkpoint","full")):
                start=perf_counter(); restored,store=recover(path,config,owners,policy,mode=="checkpoint")
                recovery[mode].append(perf_counter()-start)
                assert restored.snapshot == expected and restored.operator_required
                store.close()
        return dict(processing=summarize(samples),phases={k:summarize(v) for k,v in phases.items()},
            gc=audit.summary(),max_output_bytes=max(v[0] for v in wire_bytes),max_effects_bytes=max(v[1] for v in wire_bytes),
            recovery={k:dict(median_seconds=statistics.median(v),samples_seconds=v) for k,v in recovery.items()},
            db_bytes=path.stat().st_size,fees=str(expected.account.fees_paid),position_quantity=str(expected.account.position.quantity))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--repeats",type=int,default=10)
    parser.add_argument("--observations",type=int,default=32)
    parser.add_argument("--persisted-events",type=int,default=90)
    parser.add_argument("--recovery-repeats",type=int,default=3)
    parser.add_argument("--profile",type=Path)
    args=parser.parse_args()
    if not 2 <= args.observations <= 100 or not 1 <= args.persisted_events <= 90 or args.repeats < 1 or args.recovery_repeats < 1:
        parser.error("observations 2..100, heartbeats 1..90; repeats positive")
    root=Path(__file__).resolve().parents[1]
    if args.profile:
        profile=cProfile.Profile();profile.enable()
        work(2,args.observations);durable(20,1,root/"tmp")
        profile.disable()
        with args.profile.open("w") as stream:
            pstats.Stats(profile,stream=stream).sort_stats("cumulative").print_stats(40)
        return
    print(json.dumps(dict(environment=dict(python=sys.version,platform=platform.platform(),sqlite=sqlite3.sqlite_version),
        method="single serialized writer; shared warm Windows host; complete session/audit/account processing; construction/snapshots excluded; SQLite DELETE/EXTRA; checkpoint interval 2; full operational replay in both recovery modes; no SLA",
        workload=vars(args)|{"profile":None},in_memory=work(args.repeats,args.observations),
        durable=durable(args.persisted_events,args.recovery_repeats,root/"tmp")),indent=2))


if __name__ == "__main__":
    main()
