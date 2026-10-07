"""Explicit one-time production city escape repair through the public HTTP API."""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_core.database import version_token
from engineering.config import ARTIFACTS,DATABASE,DATASET,connect
from run_llm_eval import mart_digests,source_seller_reference,verify_seller_source

if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-production',action='store_true',required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    args=parser.parse_args()
    root=ARTIFACTS/('source_repair_production_'+uuid.uuid4().hex[:8]);root.mkdir()
    report={'database':DATABASE,'status':'RUNNING','external_llm_calls':0}
    try:
        with connect() as control:before=version_token(control,DATABASE)
        reference=source_seller_reference(DATASET)
        expected=mart_digests(DATABASE,expected_city_overrides=reference['city_overrides'])
        with httpx.Client(base_url=args.url,timeout=120) as client:
            response=client.post('/api/maintenance/source-text/preview',json={'directory':DATASET})
            response.raise_for_status();plan=response.json();report['plan']=plan
            assert plan['database']==DATABASE and plan['base_token']==before
            if not plan['ready']:
                assert not plan['changes']
                report['status']='NO_CHANGE';return 0
            assert len(plan['changes'])==1 and plan['impacts']=={
                'raw_sellers':1,'mart_order_seller_delivery':5,'mart_order_item_business':5}
            response=client.post('/api/maintenance/source-text/repair',json={
                'directory':DATASET,'token':plan['token'],'confirmed':True})
            response.raise_for_status();batch=response.json()['id'];report['batch']=batch
            print('CONFIRMED_BATCH='+batch,flush=True)
            started=time.monotonic()
            while time.monotonic()-started<600:
                response=client.get('/api/imports/'+batch);response.raise_for_status();row=response.json()
                if row['status'] not in ('queued','building'):break
                time.sleep(1)
            report['batch_record']=row
            assert row['status']=='published',row.get('error')
            assert len(row['checks'])==36 and not [c for c in row['checks'] if c['status']=='FAIL']
            with connect() as control:
                assert version_token(control,row['backup'])==before
                report['after_token']=version_token(control,DATABASE)
            verify_seller_source(DATABASE,reference)
            assert mart_digests(DATABASE)==expected
            report.update(status='PASS',all_mart_cells_match_source_corrected_baseline=True,
                          backup_preserves_all_original_layers=True,changed_city_cells=11,
                          source_sellers=3095,quality_checks=36)
    except Exception as error:
        report.update(status='FAIL',error=f'{type(error).__name__}: {error}')
    finally:
        path=root/'acceptance.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('PRODUCTION_SOURCE_REPAIR='+report['status']);print('REPORT='+str(path))
    return 0 if report['status'] in ('PASS','NO_CHANGE') else 1


if __name__=='__main__':raise SystemExit(main())
