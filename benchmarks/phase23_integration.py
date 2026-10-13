"""Small offline latency evidence; no network or production-throughput claim."""
import json
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.api.test_ai import connection, body, AI
from tests.api.helpers import AUTH, READ, ROOT
from tests.llm.test_gemini import configuration
from tests.backtesting.helpers import approve, bars, observations, INSTRUMENT, CONFIG
from quantlab.strategies import StrategySpecification


def main():
    samples={name:[] for name in ('admission_ms','mock_cloud_validation_ms','result_processing_ms',
        'research_submission_ms','backtest_ms','journal_ingestion_ms')}
    def timed(name, work):
        start=perf_counter(); result=work(); samples[name].append((perf_counter()-start)*1000); return result
    with TemporaryDirectory() as directory, connection(configuration(max_session_requests=100),
        journal_path=Path(directory)/'journal.db') as (client,jobs,sdk,captured):
        for index in range(10):
            queued=timed('admission_ms',lambda:client.post(AI,json=body(idempotency_key=f'bench-{index}'),headers=AUTH))
            job=queued.json()['job_id']
            done=timed('mock_cloud_validation_ms',lambda:client.post(AI+'/'+job+'/execute',headers=AUTH))
            assert done.json()['state']=='completed'
            result=timed('result_processing_ms',lambda:client.get(AI+'/'+job+'/result',headers=READ))
            spec=approve(StrategySpecification.model_validate_json(json.dumps(result.json()['result']['proposal']),strict=True))
            series=bars((99,101,99,110,99,90))
            request={'interpretation_job_id':job,'submission':{'idempotency_key':f'backtest-{index}','operation':{
                'kind':'backtest','strategy':spec.model_dump(mode='json'),
                'bars':[b.model_dump(mode='json') for b in series],
                'features':[f.model_dump(mode='json') for f in observations(spec,series)],
                'instrument':INSTRUMENT.model_dump(mode='json'),'config':CONFIG.model_dump(mode='json')}}}
            op=timed('research_submission_ms',lambda:client.post(ROOT+'/ai/research/operations',json=request,headers=AUTH)).json()['operation_id']
            done=timed('backtest_ms',lambda:client.post(ROOT+'/research/operations/'+op+'/execute',headers=AUTH))
            assert done.json()['state']=='completed'
            lane=client.app.state.lane
            async def ingest():
                return await lane.call(lambda:captured['services'].journal.ingest_research('phase23-benchmark',captured['services'].operations.get(op)))
            timed('journal_ingestion_ms',lambda:client.portal.call(ingest))
    print(json.dumps({'iterations':10,'bars_per_backtest':6,'provider':'scripted Gemini SDK-shaped double',
        'real_network_latency':'not measured','real_provider_inference_latency':'not measured',
        'measurements':{name:{'median':round(statistics.median(values),3),
                             'maximum':round(max(values),3)} for name,values in samples.items()}},indent=2))


if __name__=='__main__':
    main()
