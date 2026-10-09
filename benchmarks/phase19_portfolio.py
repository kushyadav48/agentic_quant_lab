"""Static membership/current-head workload; no timing-dependent financial choices."""
import argparse
import json
import platform
import statistics
from time import perf_counter

from quantlab.portfolio import RefreshPortfolio
from tests.portfolio.helpers import member_owner, portfolio, enroll, observe
from tests.paper.test_sessions import command


def measure(count, width):
    p = portfolio(capital="40000", encumbered="20000", gross="20000", maximum_operations=100_000)
    for i in range(width):
        member, owner = member_owner(f"strategy-{i}")
        enroll(p, member)
        observe(p, member, owner.start(command("start")))
    timings = []
    snapshot_sizes, event_sizes = set(), []
    for n in range(count):
        item = RefreshPortfolio(operation_id=f"refresh:{n}", sequence=p.snapshot.sequence+1,
            timestamp=p.snapshot.timestamp)
        started = perf_counter()
        outcome = p.process(item)
        timings.append(perf_counter()-started)
        if n in (0, count-1):
            snapshot_sizes.add(len(p.snapshot.canonical_json().encode()))
            event_sizes.append(len(outcome.canonical_json().encode()))
    assert p.snapshot.equity == 40000 and p.snapshot.net_pnl == 0
    # Record IDs/sequence digits can grow; retained history never enters a head.
    assert max(snapshot_sizes)-min(snapshot_sizes) < 16
    return dict(observations=count, members=width, median_ms=statistics.median(timings)*1000,
        first_quarter_median_ms=statistics.median(timings[:max(1,count//4)])*1000,
        last_quarter_median_ms=statistics.median(timings[-max(1,count//4):])*1000,
        maximum_ms=max(timings)*1000, total_seconds=sum(timings),
        snapshot_bytes=sorted(snapshot_sizes), event_bytes=event_sizes,
        retained_events=len(p.events), equity=str(p.snapshot.equity))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--members", type=int, nargs="+", default=[1, 4])
    args = parser.parse_args()
    if any(n < 4 or n > 50_000 for n in args.counts) or any(w < 1 or w > 32 for w in args.members):
        parser.error("counts must be 4..50000 and members 1..32")
    print(json.dumps(dict(python=platform.python_version(), platform=platform.platform(),
        results=[measure(n,w) for w in args.members for n in args.counts]), indent=2))
