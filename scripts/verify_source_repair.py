"""Real full-data HTTP source-repair/restore acceptance in an isolated clone."""
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from agent_core.database import version_token
from agent_core.runtime import Agent
from engineering.config import ARTIFACTS,DATABASE,DATASET,connect,ident
from engineering.contracts import TABLES
from engineering.jobs import Jobs
from engineering.pipeline import existing_tables,scalar
from engineering.store import Store
import server.main as server
from run_llm_eval import mart_digests,source_seller_reference,verify_seller_source

if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')


def main():
    key=uuid.uuid4().hex[:8];target='olist_accept_'+key
    root=ARTIFACTS/('source_repair_acceptance_'+key);root.mkdir()
    report={'database':target,'production':DATABASE,'external_llm_calls':0,'cases':[],'status':'RUNNING'}
    def record(name,fn):
        started=time.monotonic();row={'id':name};report['cases'].append(row)
        try:row.update(fn() or {},status='PASS')
        except Exception as error:row.update(status='FAIL',error=f'{type(error).__name__}: {error}');raise
        finally:
            row['seconds']=round(time.monotonic()-started,2)
            (root/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(row['status']+' '+name,flush=True)
        return row
    with connect() as control:before=version_token(control,DATABASE)
    try:
        def clone():
            with connect() as control,control.cursor() as cur:
                assert scalar(cur,"SELECT GET_LOCK('olist_engineering_publish',0)")
                try:
                    cur.execute(f'CREATE DATABASE {ident(target)} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
                    present=existing_tables(control,DATABASE)
                    for table in (*TABLES,'_eng_version'):
                        if table not in present:continue
                        cur.execute(f'CREATE TABLE {ident(target)}.{ident(table)} LIKE {ident(DATABASE)}.{ident(table)}')
                        cur.execute(f'INSERT INTO {ident(target)}.{ident(table)} SELECT * FROM {ident(DATABASE)}.{ident(table)}')
                    assert version_token(control,target)==before
                finally:cur.execute("SELECT RELEASE_LOCK('olist_engineering_publish')")
            return {'tables':len(present&set(TABLES)),'orders':99441}
        record('REPAIR-CLONE-FULL',clone)
        store=Store(root/'batches');jobs=Jobs(store,target)
        server.DATABASE=target;server.store=store;server.jobs=jobs
        server.agent=Agent(root/'agent',store,jobs,target)
        with TestClient(server.app) as client:
            response=client.post('/api/maintenance/source-text/preview',json={'directory':DATASET})
            assert response.status_code==200,response.text
            plan=response.json();report['plan']=plan
            def guards():
                assert plan['ready'] and len(plan['changes'])==1, 'Expected the confirmed old literal-backslash-r city difference'
                assert plan['impacts']=={'raw_sellers':1,'mart_order_seller_delivery':5,'mart_order_item_business':5}
                for body in ({'directory':DATASET,'token':plan['token']},
                             {'directory':DATASET,'token':'wrong','confirmed':True}):
                    assert client.post('/api/maintenance/source-text/repair',json=body).status_code==400
                with connect() as conn:assert version_token(conn,target)==before
                return {'source_sha256':plan['source_sha256'],'impacts':plan['impacts']}
            record('REPAIR-PREVIEW-AND-GUARDS',guards)
            def repair():
                response=client.post('/api/maintenance/source-text/repair',json={'directory':DATASET,'token':plan['token'],'confirmed':True})
                assert response.status_code==200,response.text
                batch=response.json()['id'];report['batch']=batch
                started=time.monotonic()
                while time.monotonic()-started<600:
                    row=store.get(batch)
                    if row['status'] not in ('queued','building') and not jobs.lock.locked():break
                    time.sleep(.2)
                assert row['status']=='published',row.get('error')
                reference=source_seller_reference(DATASET)
                verify_seller_source(target,reference)
                assert mart_digests(target)==mart_digests(DATABASE,expected_city_overrides=reference['city_overrides'])
                assert len(row['checks'])==36 and not [c for c in row['checks'] if c['status']=='FAIL']
                assert client.post('/api/maintenance/source-text/repair',json={'directory':DATASET,'token':plan['token'],'confirmed':True}).status_code==400
                fresh=client.post('/api/maintenance/source-text/preview',json={'directory':DATASET}).json()
                assert not fresh['ready'] and not fresh['changes']
                return {'batch':batch,'checks':36,'all_mart_cells_match_source_corrected_baseline':True,'source_sellers':3095}
            result=record('REPAIR-PUBLISH-AND-CONTENT',repair)
            def restore():
                response=client.post('/api/imports/'+result['batch']+'/rollback')
                assert response.status_code==200,response.text
                with connect() as conn:assert version_token(conn,target)==before
                assert client.post('/api/imports/'+result['batch']+'/rollback').status_code==400
                return {'all_layers_restored':True,'original_checksum_restored':True}
            record('REPAIR-RESTORE-ALL-LAYERS',restore)
        report['status']='PASS'
    except Exception as error:
        report['status']='FAIL';report['error']=f'{type(error).__name__}: {error}'
    finally:
        with connect() as conn:report['formal_checksum_unchanged']=version_token(conn,DATABASE)==before
        if not report['formal_checksum_unchanged']:report['status']='FAIL'
        (root/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('SOURCE_REPAIR='+report['status']);print('REPORT='+str(root/'acceptance.json'))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
