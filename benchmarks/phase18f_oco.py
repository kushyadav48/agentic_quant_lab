"""Serialized OCO latency, bounded partial chains and real durable recovery."""
import argparse
import cProfile
import json
from pathlib import Path
import platform
import pstats
import statistics
import sys
import tempfile
from time import perf_counter
from decimal import Decimal as D
from benchmarks.phase18f_advanced import summarize
from tests.paper.test_sessions import session, inputs, control, T
from tests.paper.test_oco import create
from tests.persistence.test_advanced import setup, execute, q, reopened, COSTS
from tests.backtesting.helpers import CONFIG, MINUTE
from quantlab.paper import AdvancedReplayConfig, PaperSession, OCOFillRecord, StopTrigger


def partial_owner(quantity):
    base,owners=session(age=10*MINUTE,maximum_inputs=300,capital="50000",
        research_config=CONFIG.model_copy(update={"quantity":D(quantity),"execution_costs":COSTS}))
    config=AdvancedReplayConfig(**base.config.model_dump(exclude={"schema_version"}),liquidity_per_observation=D("1"))
    owner=PaperSession(config,**owners)
    for item in inputs(): owner.process(item)
    return owner


def work(repeats,observations,profile=None):
    results={}
    for name in ("oco_stop_exit","oco_target_exit","oco_partial_sequence"):
        samples=[]; phases={}
        for _ in range(repeats):
            if name == "oco_partial_sequence": owner=partial_owner(observations)
            else: owner,_=setup(); execute(owner)
            owner.process(create(owner))
            events=[q(7,"95"),q(8,"94"),q(9,"93")] if name == "oco_stop_exit" else (
                [q(7,"110"),q(8,"111")] if name == "oco_target_exit" else
                [q(n,"110") for n in range(7,7+observations)])
            if profile is not None: profile.enable()
            for item in events:
                start=perf_counter(); out=owner.process(item); elapsed=perf_counter()-start
                samples.append(elapsed)
                fill=next((e for e in out.orders if isinstance(e,OCOFillRecord)),None)
                phase="trigger" if any(isinstance(e,StopTrigger) for e in out.orders) else (
                    "final_fill" if fill.remaining_quantity == 0 else "partial_fill")
                phases.setdefault(phase,[]).append(elapsed)
            if profile is not None: profile.disable()
            # Explicit full consumer validation and hand financial checks outside timing.
            assert owner.snapshot.oco.group.state == "closed"
            assert owner.snapshot.account.position.quantity == 0
            if name != "oco_partial_sequence":
                assert owner.snapshot.account.fees_paid == D("3.4")
                assert owner.snapshot.account.realized_pnl == D("-15" if name == "oco_stop_exit" else "19")
        results[name]=summarize(samples)|{"phase_breakdown":{k:summarize(v) for k,v in phases.items()}}
    return results


def durable(count,repeats,directory):
    with tempfile.TemporaryDirectory(dir=directory,prefix="phase18f-oco-") as temp:
        path=Path(temp)/"oco.db"
        owner,store,reference,owners,policy=setup(path)
        execute(owner); execute(reference)
        items=[create(owner),q(7,"95"),q(8,"94"),q(9,"93")]
        items += [control("heartbeat",n,T+(n-6)*MINUTE) for n in range(10,10+count)]
        samples=[]; phases={}
        for index,item in enumerate(items):
            start=perf_counter(); out=owner.process(item); elapsed=perf_counter()-start
            samples.append(elapsed)
            phase=("creation","trigger","partial_fill","final_fill")[index] if index<4 else "heartbeat"
            phases.setdefault(phase,[]).append(elapsed)
            assert out == reference.process(item)
        expected=owner.snapshot
        owner.checkpoint(); store.close()
        recovery={"full":[],"checkpoint":[]}
        for repeat in range(repeats):
            for mode in (("full","checkpoint") if repeat%2 == 0 else ("checkpoint","full")):
                start=perf_counter(); restored,store=reopened(path,reference.config,owners,policy,mode=="checkpoint")
                recovery[mode].append(perf_counter()-start)
                assert restored.snapshot == expected
                store.close()
        return dict(processing=summarize(samples),phase_breakdown={k:summarize(v) for k,v in phases.items()},
            recovery={k:dict(median_seconds=statistics.median(v),samples_seconds=v) for k,v in recovery.items()},
            db_bytes=path.stat().st_size,fees=str(expected.account.fees_paid),realized_pnl=str(expected.account.realized_pnl))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--repeats",type=int,default=10)
    parser.add_argument("--observations",type=int,default=32)
    parser.add_argument("--persisted-events",type=int,default=90)
    parser.add_argument("--recovery-repeats",type=int,default=3)
    parser.add_argument("--profile",type=Path)
    args=parser.parse_args()
    if not 2<=args.observations<=100 or not 1<=args.persisted_events<=90 or min(args.repeats,args.recovery_repeats)<1:
        parser.error("observations 2..100; persisted events 1..90; repetitions positive")
    root=Path(__file__).resolve().parents[1]
    if args.profile:
        profile=cProfile.Profile(); work(2,args.observations,profile)
        with args.profile.open('w') as stream: pstats.Stats(profile,stream=stream).sort_stats('cumulative').print_stats(40)
        return
    print(json.dumps(dict(environment=dict(python=sys.version,platform=platform.platform()),
        workload=vars(args)|{"profile":None},method="one serialized writer; warm shared Windows host; construction/snapshots excluded; DELETE/EXTRA/EXCLUSIVE; checkpoints 2/retention 2; complete operational recovery; no timing guarantees",
        in_memory=work(args.repeats,args.observations),durable=durable(args.persisted_events,args.recovery_repeats,root/'tmp')),indent=2))


if __name__ == '__main__': main()
