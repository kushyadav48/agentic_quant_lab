"""Authenticated proposal, explicit review, deterministic research, retained provenance."""
import asyncio
from contextlib import contextmanager
import json

from fastapi.testclient import TestClient
import pytest

from quantlab.api import create_app
from quantlab.api.ai import AIInput, AIJobs
from quantlab.api.services import ApplicationServices
from quantlab.journal import SQLiteJournal
from quantlab.mcp.operation_models import OperationSubmission
from quantlab.strategies import StrategySpecification
from tests.api.helpers import AUTH, READ, ROOT, settings
from tests.backtesting.helpers import approve, bars, observations, INSTRUMENT, CONFIG
from tests.interpretation.helpers import ready, source, clarification
from tests.llm.test_gemini import configuration, provider, ScriptedSDK, sdk_response, SECRET

AI = ROOT + '/ai/interpretations'


def body(**changes):
    return dict(idempotency_key='ai-1', input=source().model_dump(mode='json'),
                external_transmission_approved=True) | changes


@contextmanager
def connection(config=None, sdk=None, journal_path=None):
    config = config or configuration()
    adapter, sdk = provider(config, sdk)
    jobs = AIJobs(config, provider=adapter)
    captured = {}
    def factory():
        captured['services'] = ApplicationServices(journal=SQLiteJournal(journal_path) if journal_path else None)
        return captured['services']
    app = create_app(settings(cloud=config), services_factory=factory, ai_factory=lambda:jobs)
    with TestClient(app, base_url='http://localhost') as client:
        yield client, jobs, sdk, captured


def run(client):
    queued = client.post(AI, json=body(), headers=AUTH)
    assert queued.status_code == 201, queued.text
    job = queued.json()['job_id']
    done = client.post(AI + '/' + job + '/execute', headers=AUTH)
    assert done.status_code == 200, done.text
    return job, done.json()


def test_auth_status_idempotency_result_and_no_secret(caplog):
    with connection() as (client, jobs, sdk, captured):
        assert client.post(AI,json=body(),headers=READ).status_code == 403
        assert client.post(AI,json=body()).status_code == 401
        job, done = run(client)
        assert done['state'] == 'completed'
        assert client.post(AI,json=body(),headers=AUTH).json() == done
        assert client.post(AI+'/'+job+'/execute',headers=AUTH).json() == done
        assert len(sdk.calls) == 2
        altered = body(input=source(strategy_text='changed').model_dump(mode='json'))
        assert client.post(AI,json=altered,headers=AUTH).status_code == 409
        assert client.post(AI+'/'+job+'/execute',headers=READ).status_code == 403
        result = client.get(AI+'/'+job+'/result',headers=READ)
        assert result.status_code == 200
        assert result.json()['result']['proposal']['state'] == 'draft'
        assert result.json()['result']['proposal']['approval'] is None
        for response in [result,client.get(ROOT+'/ai/configuration',headers=READ),client.get(ROOT+'/openapi.json',headers=READ)]:
            assert SECRET not in response.text
        assert SECRET not in caplog.text
        assert client.get(AI+'/'+job,headers=READ).json()['provider_id'] == 'gemini'


@pytest.mark.parametrize('config,code',[(configuration(enabled=False),'cloud_disabled'),
    (configuration(api_key=None),'credentials_missing'),(configuration(verification=None),'free_policy_rejected'),
    (configuration(provider_id='other'),'unsupported_provider')])
def test_disabled_and_configuration_failures(config,code):
    with connection(config) as (client,jobs,sdk,captured):
        response=client.post(AI,json=body(),headers=AUTH)
        assert response.status_code == 503 and response.json()['code']==code
        assert not sdk.calls
        # Existing offline operations still work.
        from tests.mcp.test_operations import submission
        queued=client.post(ROOT+'/research/operations',json=submission().model_dump(mode='json'),headers=AUTH)
        assert queued.status_code==201
        done=client.post(ROOT+'/research/operations/'+queued.json()['operation_id']+'/execute',headers=AUTH)
        assert done.json()['state']=='completed'


@pytest.mark.parametrize('sdk,code',[(ScriptedSDK(response=sdk_response('{}')),'invalid_model_response'),
    (ScriptedSDK(error=TimeoutError(SECRET)),'provider_timeout'),
    (ScriptedSDK(response=sdk_response(json.dumps(ready() | {'evidence':[]}))),'invalid_model_response')])
def test_failures_are_terminal_no_success_or_retry(sdk,code):
    with connection(sdk=sdk) as (client,jobs,sdk,captured):
        job,done=run(client)
        assert done['state']=='failed' and done['failure']==code
        assert client.get(AI+'/'+job+'/result',headers=READ).status_code==409
        count=len(sdk.calls)
        assert client.post(AI+'/'+job+'/execute',headers=AUTH).json()==done
        assert len(sdk.calls)==count
        assert SECRET not in json.dumps(done)


def test_consent_size_cancel_and_clarification():
    with connection(sdk=ScriptedSDK(response=sdk_response(json.dumps(clarification())))) as (client,jobs,sdk,captured):
        for item in [body(external_transmission_approved=False),body(input=source().model_dump(mode='json')|{'strategy_text':'x'*16001})]:
            assert client.post(AI,json=item,headers=AUTH).status_code==422
        queued=client.post(AI,json=body(idempotency_key='cancel'),headers=AUTH).json()
        job=queued['job_id']
        assert client.get(AI+'/'+job+'/result',headers=READ).status_code==409
        assert client.post(AI+'/'+job+'/cancel',headers=AUTH).json()['state']=='cancelled'
        assert client.post(AI+'/'+job+'/execute',headers=AUTH).status_code==409
        assert not sdk.calls
        job,done=run(client)
        assert done['state']=='completed'
        result=client.get(AI+'/'+job+'/result',headers=READ).json()['result']
        assert result['proposal'] is None
        assert result['interpretation']['status']=='NEEDS_CLARIFICATION'


def test_integrated_backtest_review_risk_and_journal(tmp_path):
    with connection(journal_path=tmp_path/'research.db') as (client,jobs,sdk,captured):
        job,done=run(client)
        draft=StrategySpecification.model_validate_json(json.dumps(
            client.get(AI+'/'+job+'/result',headers=READ).json()['result']['proposal']),strict=True)
        series=bars((99,101,99,110,99,90))
        def research(spec,key='linked'):
            return {'interpretation_job_id':job,'submission':{'idempotency_key':key,'operation':{
                'kind':'backtest','strategy':spec.model_dump(mode='json'),
                'bars':[b.model_dump(mode='json') for b in series],
                'features':[f.model_dump(mode='json') for f in observations(spec,series)],
                'instrument':INSTRUMENT.model_dump(mode='json'),'config':CONFIG.model_dump(mode='json')}}}
        route=ROOT+'/ai/research/operations'
        assert client.post(route,json=research(draft),headers=AUTH).status_code==422
        approved=approve(draft)  # Existing trusted approval, explicitly outside HTTP/AI.
        assert client.post(route,json=research(approved),headers=READ).status_code==403
        changed=approve(draft.revise(draft.content))
        assert client.post(route,json=research(changed),headers=AUTH).status_code==422
        response=client.post(route,json=research(approved),headers=AUTH)
        assert response.status_code==201,response.text
        queued=response.json()
        assert queued['workflow']['thread_digest']==job
        assert queued['workflow']['content_digest']==draft.content_digest
        assert queued['state']=='queued'
        assert client.post(route,json=research(approved),headers=AUTH).json()==queued
        operation=queued['operation_id']
        done=client.post(ROOT+'/research/operations/'+operation+'/execute',headers=AUTH).json()
        assert done['state']=='completed'
        result=client.get(ROOT+'/research/operations/'+operation+'/result',headers=READ).json()
        assert result['result']['value']['strategy_content_digest']==draft.content_digest
        assert len(result['result']['value']['fills'])==2
        # Ingest on the existing owner's thread, explicitly as Phase 20 requires.
        lane=client.app.state.lane
        async def ingest():
            return await lane.call(lambda: captured['services'].journal.ingest_research(
                'phase23-test',captured['services'].operations.get(operation)))
        client.portal.call(ingest)
        records=client.post(ROOT+'/journal/research/query',json={'limit':10},headers=READ).json()['records']
        assert len(records)==1
        assert job in json.dumps(records) and draft.content_digest in json.dumps(records)
        assert SECRET not in json.dumps(records)
        assert len(sdk.calls)==2  # Backtest/journal never consult LLM.
        limited=research(approved,key='risk-rejection')
        limited['submission']['operation']['config']['risk']={'max_position_quantity':'1'}
        limited_job=client.post(route,json=limited,headers=AUTH).json()['operation_id']
        assert client.post(ROOT+'/research/operations/'+limited_job+'/execute',headers=AUTH).json()['state']=='completed'
        limited_result=client.get(ROOT+'/research/operations/'+limited_job+'/result',headers=READ).json()['result']['value']
        assert not limited_result['fills']
        assert any(d['action']=='reject' for d in limited_result['risk_decisions'])


def test_running_job_survives_disconnect_and_offline_lane_remains_available():
    async def scenario():
        started,release=asyncio.Event(),asyncio.Event()
        sdk=ScriptedSDK()
        original=sdk.count_tokens
        async def wait(**kwargs):
            started.set(); await release.wait(); return await original(**kwargs)
        sdk.count_tokens=wait
        config=configuration()
        adapter,_=provider(config,sdk)
        jobs=AIJobs(config,provider=adapter)
        item=AIInput.model_validate_json(json.dumps(body()),strict=True)
        job=jobs.submit(item)
        caller=asyncio.create_task(jobs.execute(job.job_id))
        await started.wait()
        assert jobs.get(job.job_id).state=='running'
        from quantlab.api.services import ServiceLane
        from tests.mcp.test_operations import submission
        lane=ServiceLane()
        await lane.start(ApplicationServices)
        queued=await lane.call(lambda:lane.services.operations.submit(submission()))
        assert (await lane.call(lambda:lane.services.operations.execute(queued.operation_id))).state.value=='completed'
        await lane.close()
        with pytest.raises(Exception):
            jobs.cancel(job.job_id)
        second=jobs.submit(AIInput.model_validate_json(json.dumps(body(idempotency_key='second')),strict=True))
        with pytest.raises(Exception) as caught:
            await jobs.execute(second.job_id)
        assert caught.value.code=='cloud_busy' and jobs.get(second.job_id).state=='queued'
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        await jobs.close()
        assert jobs.get(job.job_id).state=='completed' and not jobs.tasks
    asyncio.run(scenario())
