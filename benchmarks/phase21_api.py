r"""Offline ASGI latency observations over actual existing service fixtures.

Run: .venv\Scripts\python.exe benchmarks/phase21_api.py
In-process timings include routing, auth, validation and serialization, not TCP.
"""
import asyncio
from importlib.metadata import version
import json
import platform
from pathlib import Path
from secrets import token_urlsafe
from statistics import median
from time import perf_counter
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from pydantic import SecretStr
from quantlab.api import APISettings, ApplicationServices, create_app
from quantlab.journal import JournalSession, SQLiteJournal
from tests.mcp.helpers import valid_strategy_content
from tests.mcp.test_operations import submission
from tests.portfolio.helpers import member_owner, portfolio, enroll, observe, entry_inputs


def factory():
    member, owner = member_owner()
    p, journal = portfolio(), SQLiteJournal()
    enroll(p, member)
    journal.register_session(JournalSession(config=owner.config, strategy=member.strategy,
        admission=owner.snapshot.runtime.admission))
    for item in entry_inputs():
        record = owner.process(item)
        observe(p, member, record)
        journal.ingest_trade(record)
    return ApplicationServices(paper_sessions={owner.config.strategy.session_id: owner},
        portfolios={p.snapshot.config.portfolio_id: p}, journal=journal)


async def main():
    credential = token_urlsafe(48)
    app = create_app(APISettings(operator_token=SecretStr(credential)), services_factory=factory)
    headers = {"Authorization": "Bearer "+credential}
    root = "/api/v1"
    results = {}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=headers) as c:
            admitted = await c.post(root+"/research/operations", json=submission().model_dump(mode="json"))
            admitted.raise_for_status()
            identity = admitted.json()["operation_id"]
            cases = {
                "health": ("GET", root+"/health", None),
                "status": ("GET", root+"/research/operations/"+identity, None),
                "strategy_validation": ("POST", root+"/strategies/validate", {"content": valid_strategy_content()}),
                "paper_snapshot": ("GET", root+"/paper/sessions/session:a/snapshot", None),
                "portfolio_snapshot": ("GET", root+"/portfolios/portfolio/snapshot", None),
                "journal_page": ("POST", root+"/journal/trades/query", {"limit": 10}),
            }
            async def measure(name, call, repeats):
                timings = []
                for _ in range(repeats):
                    start = perf_counter()
                    response = await call()
                    response.raise_for_status()
                    timings.append((perf_counter()-start)*1000)
                ordered = sorted(timings)
                results[name] = {"samples": repeats, "median_ms": round(median(timings), 3),
                    "p95_ms": round(ordered[max(0, (95*repeats+99)//100-1)], 3),
                    "max_ms": round(max(timings), 3)}
            for name, (method, path, body) in cases.items():
                async def call(method=method, path=path, body=body):
                    return await c.request(method, path, **({} if body is None else {"json": body}))
                warmup = await call()
                warmup.raise_for_status()
                await measure(name, call, 30)
            counter = 0
            async def execution():
                nonlocal counter
                counter += 1
                queued = await c.post(root+"/research/operations", json=submission(key=f"bench_{counter}").model_dump(mode="json"))
                queued.raise_for_status()
                return await c.post(root+"/research/operations/"+queued.json()["operation_id"]+"/execute")
            await measure("backtest_submit_and_execute", execution, 10)
    print(json.dumps({"python": platform.python_version(), "fastapi": version("fastapi"),
        "transport": "in_process_asgi", "fixture": "four committed paper records; four-bar approved backtest",
        "observations": results}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
