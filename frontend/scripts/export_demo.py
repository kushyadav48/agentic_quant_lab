"""Build the read-only synthetic UI workspace from deterministic Python services.

No private data, credentials, provider calls, fake browser P&L or backend changes.
Run from the repository root with the installed project/dev environment.
"""
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from quantlab.api import ApplicationServices
from quantlab.journal import JournalSession, SQLiteJournal
from quantlab.mcp import tools
from quantlab.mcp.operations import ResearchOperations
from quantlab.strategies import Direction
from tests.api.helpers import client, READ, ROOT
from tests.mcp.helpers import valid_strategy_content, valid_backtest_request
from tests.mcp.research_helpers import NAMES, requests
from tests.mcp.test_operations import submission
from tests.portfolio.helpers import member_owner, portfolio, enroll, observe, entry_inputs
from tests.persistence.test_advanced import q

class FixtureClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 2, tzinfo=timezone.utc)

captured = {}
def factory():
    journal = SQLiteJournal(':memory:')
    p = portfolio(capital='100000', encumbered='20000', gross='20000')
    members = [member_owner(name, direction=direction, capital='25000', encumbered='5000', gross='5000')
        for name, direction in [('threshold_long', Direction.LONG), ('threshold_short', Direction.SHORT), ('baseline_long', Direction.LONG)]]
    for member, owner in members:
        enroll(p, member)
        journal.register_session(JournalSession(config=owner.config, strategy=member.strategy, admission=owner.snapshot.runtime.admission))
        for item in entry_inputs(short=member.strategy.content.direction is Direction.SHORT):
            record = owner.process(item)
            observe(p, member, record)
            journal.ingest_trade(record)
    # Fixed observations, not a stochastic generator. Python computes every mark.
    prices = ['101.25', '102.10', '101.80', '103.40', '104.25', '103.90', '105.10', '104.70',
        '106.20', '107.15', '106.55', '105.90', '107.80', '108.45', '107.60', '109.20',
        '110.10', '108.85', '109.70', '111.25', '110.60', '112.15', '111.75', '113.40']
    for sequence, price in enumerate(prices, 6):
        for index, (member, owner) in enumerate(members):
            record = owner.process(q(sequence, str(Decimal(price) + Decimal(index))))
            observe(p, member, record)
            journal.ingest_trade(record)
    operations = ResearchOperations()
    kinds = ['holdout', 'walk_forward', 'robustness', 'ml_dataset', 'ml_training', 'ml_prediction', 'ml_prediction_features']
    raw = requests()
    inputs = [('backtest', valid_backtest_request()), *[(kind, raw[name]) for kind, name in zip(kinds, NAMES)],
        ('performance', {'result': tools.run_backtest(valid_backtest_request()).value.model_dump(mode='json')})]
    ids = []
    for kind, request in inputs:
        queued = operations.submit(submission(key='synthetic_' + kind, kind=kind, request=request))
        done = operations.execute(queued.operation_id)
        journal.ingest_research('synthetic-demo', done)
        ids.append(done.operation_id)
    captured.update(ids=ids, sessions=[owner.config.strategy.session_id for _, owner in members])
    return ApplicationServices(operations=operations, paper_sessions={owner.config.strategy.session_id: owner for _, owner in members},
        portfolios={'portfolio': p}, journal=journal)

def history(connection, kind):
    records, after = [], None
    while True:
        response = connection.post(ROOT + f'/journal/{kind}/query', json={'limit': 20, 'after': after}, headers=READ)
        assert response.status_code == 200, response.status_code
        page = response.json()
        records.extend(page['records'])
        after = page['next_cursor']
        if after is None:
            return records

with patch('quantlab.mcp.operations.datetime', FixtureClock), client(factory=factory) as connection:
    def get(path):
        response = connection.get(ROOT + path, headers=READ)
        assert response.status_code == 200, response.status_code
        return response.json()
    data = {'source': 'SYNTHETIC DEMO — fixed Python-generated TEST records, never API account data or real market results',
        'fixture_version': 2, 'capabilities': get('/capabilities'),
        'paper': {identity: get(f'/paper/sessions/{identity}/snapshot') for identity in captured['sessions']},
        'portfolio': get('/portfolios/portfolio/snapshot'),
        'summaries': {identity: get(f'/journal/sessions/{identity}/summary') for identity in captured['sessions']},
        'operations': [get('/research/operations/' + identity) for identity in captured['ids']],
        'results': {identity: get('/research/operations/' + identity + '/result') for identity in captured['ids']},
        'trades': history(connection, 'trades'), 'research': history(connection, 'research'),
        'example_request': valid_backtest_request(), 'strategy_content': valid_strategy_content(),
        'strategy_validation': connection.post(ROOT + '/strategies/validate', json={'content': valid_strategy_content()}, headers=READ).json()}
    # Deduplicate exact source/config records already present in paper snapshots.
    # The browser only joins these references; it never calculates journal deltas.
    data['journal_sessions'] = {row['source']['session_id']: row['session'] for row in data['trades']}
    data['trades'] = [{**{key: row[key] for key in ('schema_version', 'realized_pnl_delta', 'fees_delta', 'net_realized_pnl_delta')},
        'session_id': row['source']['session_id'], 'source_id': row['source']['record_id']} for row in data['trades']]
    # Store each canonical result once. Read-only views restore projections from
    # these exact bytes and history references without changing record identities.
    for retained in data['results'].values():
        retained.pop('result')
    for record in data['research']:
        if record['operation'] is not None:
            record['operation'].pop('result_json', None)
    for paper in data['paper'].values():
        config = paper['snapshot']['account']['config']
        for record in paper['snapshot']['records']:
            assert record['account']['config'] == config
            record['account'].pop('config')
output = Path(__file__).resolve().parents[1] / 'src' / 'demo' / 'fixture.json'
output.parent.mkdir(exist_ok=True)
# Python JSON preserves arbitrary-sized provenance integer tokens exactly.
output.write_text(json.dumps(data, separators=(',', ':'), ensure_ascii=True) + '\n', encoding='utf-8')
print(f'Exported deterministic synthetic workspace: {len(data["paper"])} sessions, {len(data["trades"])} records, {len(data["operations"])} results; {output.stat().st_size} bytes.')
