"""Measure actual journal imports and bounded retrieval, outside source execution."""
import argparse
import json
import platform
import statistics
import tempfile
from pathlib import Path
from time import perf_counter

from quantlab.journal import HistoryQuery, JournalSession, SQLiteJournal
from quantlab.paper import PaperSession
from tests.persistence.test_advanced import setup, q
from tests.paper.test_sessions import inputs


def measure(count, durable):
    owner, owners = setup()
    owner = PaperSession(owner.config.model_copy(update={"maximum_inputs": 5000}), **owners)
    with tempfile.TemporaryDirectory(prefix="phase20-") as directory:
        path = Path(directory)/"journal.db" if durable else ":memory:"
        with SQLiteJournal(path) as journal:
            session = JournalSession(config=owner.config, strategy=owners["strategy"],
                admission=owner.snapshot.runtime.admission)
            journal.register_session(session)
            for item in inputs(): journal.ingest_trade(owner.process(item))
            timings, sizes, tail_times = [], [], []
            for n in range(6,6+count):
                source = owner.process(q(n,"100"))
                started = perf_counter()
                journal.ingest_trade(source)
                timings.append(perf_counter()-started)
                if n in (6,5+count): sizes.append(len(source.canonical_json().encode()))
                tail_times.append(source.timestamp)
            query = HistoryQuery(session_id=session.config.strategy.session_id,
                start=tail_times[-10], limit=10)
            retrieval = []
            for _ in range(20):
                started = perf_counter()
                page = journal.trade_history(query)
                retrieval.append(perf_counter()-started)
                assert len(page.records) == 10 and page.next_cursor is None
            quarter = max(1,count//4)
            result = dict(records=count, storage="sqlite-file-full" if durable else "sqlite-memory",
                insertion_median_ms=statistics.median(timings)*1000,
                first_quarter_median_ms=statistics.median(timings[:quarter])*1000,
                last_quarter_median_ms=statistics.median(timings[-quarter:])*1000,
                insertion_total_seconds=sum(timings), insertion_maximum_ms=max(timings)*1000,
                ten_record_retrieval_median_ms=statistics.median(retrieval)*1000,
                source_bytes=sizes, retained_records=journal.summary(session.config.strategy.session_id).record_count,
                database_bytes=Path(path).stat().st_size if durable else None)
        if durable:
            started = perf_counter()
            with SQLiteJournal(path) as recovered:
                assert recovered.summary(session.config.strategy.session_id).record_count == count+4
            result["verified_restart_seconds"] = perf_counter()-started
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", type=int, nargs="+", default=[100,1000])
    parser.add_argument("--durable", action="store_true")
    args = parser.parse_args()
    if any(n < 10 or n > 4000 for n in args.counts): parser.error("counts must be 10..4000")
    print(json.dumps(dict(python=platform.python_version(), platform=platform.platform(),
        results=[measure(n,d) for d in ([False,True] if args.durable else [False]) for n in args.counts]), indent=2))
