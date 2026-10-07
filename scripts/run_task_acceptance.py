"""Task-level acceptance through the running application's HTTP API.

Uses an isolated MySQL schema, a separate loopback service and real source CSVs.
Never edits .env or publishes to DB_NAME. All generated files and evidence are
retained in one task_acceptance_* folder. The owned test service is stopped.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engineering.config import ARTIFACTS, DATABASE, DATASET, ROOT, connect
from engineering.contracts import CONTRACTS
from verify_incremental import counts, partition, verify_review_text
import httpx

if hasattr(sys.stdout,'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def log(message):
    print(message,flush=True)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def request(client, method, path, expected=200, **kwargs):
    response=client.request(method,path,**kwargs)
    check(response.status_code==expected,
          f'{method} {path}: HTTP {response.status_code}, {response.text[:500]}')
    return response.json()


def await_batch(client, batch, statuses, label, timeout=1800):
    started=time.monotonic();last=None
    while time.monotonic()-started<timeout:
        row=request(client,'GET','/api/imports/'+batch)
        phase=(row['status'],row['phase'])
        if phase!=last:
            log(f'{label}: {row["status"]} | {row["phase"]}');last=phase
        if row['status'] in statuses:
            return row
        if row['status'] in ('failed','invalid','interrupted','rolled_back'):
            raise AssertionError(f'{label}: {row.get("error") or (row.get("profile") or {}).get("errors") or row["phase"]}')
        time.sleep(2)
    raise TimeoutError(f'{label}: task timeout')


def local(client, directory, label, statuses=('ready',), **policy):
    created=request(client,'POST','/api/imports/local',json={'directory':str(directory),'label':label,**policy})
    return await_batch(client,created['id'],statuses,label)


def publish(client, row, label):
    check(row['status']=='ready',label+': source validation is not ready')
    request(client,'POST',f'/api/imports/{row["id"]}/execute')
    result=await_batch(client,row['id'],('published','unchanged'),label)
    check(len(result['checks'])==36,label+': expected 36 quality checks')
    check(not [c for c in result['checks'] if c['status']=='FAIL'],label+': quality failure')
    check(len(result['merge'])==9,label+': missing merge results')
    return result


def chunks(path):
    with path.open('rb') as file:
        yield from iter(lambda:file.read(1024*1024),b'')


def upload(client, source, label):
    names=[s.filename for s in CONTRACTS.values()]
    created=request(client,'POST','/api/imports/upload',json={'filenames':names,'label':label})
    for name in names:
        path=source/name
        result=request(client,'PUT',f'/api/imports/{created["id"]}/files/{name}',
                       content=chunks(path),headers={'Content-Type':'application/octet-stream'})
        check(result['bytes']==path.stat().st_size,'Uploaded byte count differs: '+name)
        log('UPLOAD_OK '+name)
    request(client,'POST',f'/api/imports/{created["id"]}/validate')
    return await_batch(client,created['id'],('ready',),label)


def bundle(directory, *, header_error=False, orphan=False, invalid_score=False, source=None, overrides=None):
    directory.mkdir()
    for table,spec in CONTRACTS.items():
        with (directory/spec.filename).open('w',encoding='utf-8',newline='') as file:
            writer=csv.writer(file);columns=list(spec.columns)
            if table=='raw_orders' and header_error:columns[0]='wrong_order_id'
            writer.writerow(columns)
            if overrides and table in overrides:
                writer.writerow([overrides[table][c] for c in spec.columns])
            if table=='raw_order_payments' and orphan:
                writer.writerow(['f'*32,1,'credit_card',1,'1.00'])
            if table=='raw_order_reviews' and invalid_score:
                with (source/spec.filename).open(encoding='utf-8-sig',newline='') as original:
                    row=next(csv.DictReader(original));row['review_score']='6'
                writer.writerow([row[c] for c in spec.columns])


def csv_export(client, table, expected):
    with client.stream('GET','/api/exports/'+table) as response:
        check(response.status_code==200,'CSV export failed: '+table)
        response.encoding='utf-8-sig'
        reader=csv.reader((line+'\n' for line in response.iter_lines()))
        header=next(reader)
        check('order_id' in header,'CSV header invalid: '+table)
        total=0
        for row in reader:
            check(len(row)==len(header),'CSV field count differs: '+table)
            total+=1
    check(total==expected,f'{table}: exported {total}, expected {expected}')
    return {'rows':total,'columns':len(header)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',default=DATASET);args=parser.parse_args()
    source=Path(args.source)
    token=uuid.uuid4().hex[:8]
    root=ARTIFACTS/('task_acceptance_'+token);root.mkdir(parents=True)
    database='olist_accept_'+token
    out={'status':'RUNNING','tested_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
         'database':database,'production_database':DATABASE,'root':str(root),'cases':[]}
    report=root/'acceptance.json';process=None;service_log=None;failure=None

    def evidence(name,details=None):
        out['cases'].append({'name':name,'status':'PASS','details':details or {}})
        report.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        log('PASS '+name)

    try:
        # Refuse to compete with a user-initiated publish already in progress.
        with connect() as conn,conn.cursor() as cur:
            cur.execute("SELECT IS_FREE_LOCK('olist_engineering_publish') free")
            check(cur.fetchone()['free']==1,'A data build is running; retry after it finishes')
        production_before=counts(DATABASE)
        old,new=partition(source,root)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=os.environ.copy();env['DB_NAME']=database;env['OLIST_ARTIFACTS_DIR']=str(root/'batches')
        service_log=(root/'service.log').open('w',encoding='utf-8')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','server.main:app','--host','127.0.0.1','--port',str(port)],
                                 cwd=ROOT,env=env,stdout=service_log,stderr=service_log,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        out['test_url']=f'http://127.0.0.1:{port}'
        log('ISOLATED_DATABASE='+database);log('TEST_URL='+out['test_url']);log('REPORT='+str(report))
        # All requests go to our owned loopback service: do not inherit system
        # proxy settings. Retry only failed connection establishment, never
        # replay a POST after a response/read timeout (it may already commit).
        transport=httpx.HTTPTransport(retries=2,limits=httpx.Limits(max_connections=5,max_keepalive_connections=5,keepalive_expiry=30))
        with httpx.Client(base_url=out['test_url'],timeout=httpx.Timeout(600,connect=30),
                          transport=transport,trust_env=False) as client:
            for _ in range(60):
                check(process.poll() is None,'The isolated test service exited; see service.log')
                try:
                    health=client.get('/api/health')
                    if health.status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.5)
            else:raise TimeoutError('The isolated test service did not start')
            meta=request(client,'GET','/api/meta')
            check(meta['database']==database and meta['connected'] and not meta['ready'],
                  'The test service is not bound to the empty isolated database')
            evidence('隔离服务与空库启动',{'app_version':meta['app_version']})

            first=publish(client,local(client,old,'验收：2018年前历史批次'),'首次建库')
            before=counts(database);check(before['raw_orders']==45430,'Historical order count differs')
            evidence('本地目录首次接入与三层构建',{'orders':before['raw_orders'],'batch':first['id'],
                   'quality_checks':len(first['checks']),'warnings':[c for c in first['checks'] if c['status']=='WARNING']})

            second=publish(client,upload(client,new,'验收：2018新增批次'),'新增时间段上传接入')
            full=counts(database)
            check(full['raw_orders']==99441 and full['raw_order_reviews']==99224 and
                  full['raw_geolocation']==1000163 and full['mart_order_item_business']==112650,'Full source counts differ')
            evidence('九文件上传并扩展新时间段',{'before_orders':before['raw_orders'],'after_orders':full['raw_orders'],
                   'batch':second['id'],'merge':second['merge']})

            third=publish(client,local(client,source,'验收：重叠完整快照'),'完整快照重复导入')
            check(counts(database)==full,'Repeated snapshot changed row counts or amounts')
            check(all(r['inserted']==0 and r['updated']==0 for r in third['merge']),'Repeated snapshot reported new data')
            check(third['status']=='unchanged' and third['backup'] is None,'Repeated data must not rebuild/publish a backup')
            check(any(r['mode']=='源文件指纹未变，复用 Raw 快照' for r in second['merge']),'Unchanged dimensions were not reused')
            check('stg_geolocation_zip' in second['reused_staging'],'Unchanged geography staging was not reused')
            for table,spec in CONTRACTS.items():
                file_info=next(f for f in third['profile']['files'] if f['table']==table)
                check(full[table]==file_info['rows'],'Logical source row count differs: '+table)
                with (source/spec.filename).open('rb') as original:
                    digest=hashlib.file_digest(original,'sha256').hexdigest()
                check(file_info['sha256']==digest,'Source snapshot hash differs: '+table)
            evidence('重复导入防膨胀与九表源文件对账',{'batch':third['id'],'counts':full})
            verify_review_text(database,source);evidence('25条复杂评价文本原样回写')

            overview=request(client,'GET','/api/reports/overview',params={'start':'2017-01','end':'2018-08'})
            growth=request(client,'GET','/api/reports/growth',params={'start':'2017-01','end':'2018-08','year_a':2017,'year_b':2018,'month_from':1,'month_to':8})
            check(overview['summary']['delivered_orders']==96211,'Report order count differs')
            check(abs(overview['summary']['product_value']-13181027.13)<.01,'Report amount differs')
            for dimension in ('state','category','seller'):
                check(abs(growth['concentrations'][dimension]['total']-overview['summary']['product_value'])<.01,
                      'Report contribution differs: '+dimension)
            meta=request(client,'GET','/api/meta');check(meta['defaults']['end']=='2018-08','Zero tail month in defaults')
            sp=request(client,'GET','/api/reports/overview',params={'start':'2017-01','end':'2018-08','state':'SP'})
            check(0<sp['summary']['delivered_orders']<96211,'State filter did not change the sample')
            request(client,'GET','/api/reports/overview',expected=400,params={'state':'XX'})
            evidence('A/B报表、维度金额对账与筛选',{'summary':overview['summary'],'growth':growth['change'],'default_end':meta['defaults']['end']})

            exports={table:csv_export(client,table,full[table]) for table in
                     ('mart_order_delivery','mart_order_seller_delivery','mart_order_item_business')}
            evidence('三张Mart CSV流式导出',exports)

            for name,kwargs in (('字段错误',{'header_error':True}),('无效评分',{'invalid_score':True,'source':source})):
                path=root/('invalid_'+str(len(out['cases'])));bundle(path,**kwargs)
                bad=local(client,path,'验收：'+name,('invalid',))
                request(client,'POST',f'/api/imports/{bad["id"]}/execute',expected=400)
                check(counts(database)==full,name+': changed published data')
                evidence(name+'在源校验阶段被拒绝',{'batch':bad['id'],'errors':bad['profile']['errors']})

            orphan=root/'orphan';bundle(orphan,orphan=True)
            bad=local(client,orphan,'验收：孤立支付记录')
            request(client,'POST',f'/api/imports/{bad["id"]}/execute')
            failed=await_batch(client,bad['id'],('failed',),'孤立引用质量门')
            check('质量门' in failed.get('error',''),'Unexpected failure stage for orphan batch')
            check(counts(database)==full,'Failed batch changed published data')
            check(request(client,'GET','/api/meta')['version']['batch_id']==second['id'],'Failed batch changed the published version')
            evidence('引用完整性失败不覆盖已发布数据',{'batch':bad['id'],'error':failed['error']})

            def failed_build(path,label,contains,**policy):
                row=local(client,path,'验收：'+label,**policy)
                request(client,'POST',f'/api/imports/{row["id"]}/execute')
                result=await_batch(client,row['id'],('failed',),label)
                check(contains in result.get('error',''),label+': wrong failure reason')
                check(counts(database)==full,label+': changed published counts')
                return result

            with (source/CONTRACTS['raw_products'].filename).open(encoding='utf-8-sig',newline='') as file:
                product=next(csv.DictReader(file))
            original_weight=int(product['product_weight_g']);product['product_weight_g']=str(original_weight+1)
            changed=root/'changed_product';bundle(changed,overrides={'raw_products':product})
            failure_row=failed_build(changed,'仅追加拒绝已有商品覆盖','仅追加')
            evidence('仅追加模式拒绝覆盖已有业务键',{'error':failure_row['error']})
            fourth=publish(client,local(client,changed,'验收：可信新快照更新',merge_mode='upsert',snapshot_at='2026-10-01T12:00:00+08:00'),'带版本快照更新')
            with connect(database) as conn,conn.cursor() as cur:
                cur.execute('SELECT product_weight_g FROM raw_products WHERE product_id=%s',(product['product_id'],))
                check(cur.fetchone()['product_weight_g']==original_weight+1,'New snapshot was not applied')
            evidence('可信新快照更新与Staging依赖复用',{'batch':fourth['id'],'reused_staging':fourth['reused_staging']})
            failure_row=failed_build(changed,'拒绝旧快照','旧快照',merge_mode='upsert',snapshot_at='2026-09-30T12:00:00+08:00')
            evidence('旧快照时间被拒绝',{'error':failure_row['error']})

            with (source/CONTRACTS['raw_orders'].filename).open(encoding='utf-8-sig',newline='') as file:
                order=next(r for r in csv.DictReader(file) if r['order_status']=='delivered')
            regression=root/'status_regression';old_order=dict(order);old_order['order_status']='created'
            bundle(regression,overrides={'raw_orders':old_order})
            failure_row=failed_build(regression,'订单状态回退拦截','回退',merge_mode='upsert',snapshot_at='2026-10-02T12:00:00+08:00')
            evidence('订单状态回退阻止发布',{'error':failure_row['error']})
            missing=root/'missing_items';new_order=dict(order);new_order['order_id']='f'*32
            bundle(missing,overrides={'raw_orders':new_order})
            failure_row=failed_build(missing,'已送达缺少商品项','已送达订单商品项完整性')
            evidence('已送达缺少商品项阻止发布',{'error':failure_row['error']})
            cleanup=request(client,'GET','/api/maintenance/backups',params={'keep':2})
            check('_olist_backup_'+fourth['id'] in cleanup['protected'],'Current predecessor backup is not protected')
            request(client,'POST','/api/maintenance/backups/prune',expected=400,json={'keep':2,'token':cleanup['token'],'confirmed_schemas':[database]})
            evidence('备份预览保护与错误目标拒绝',{'protected':cleanup['protected'],'targets':cleanup['targets']})

            # Older versions must not be rolled back over a newer published batch.
            request(client,'POST',f'/api/imports/{first["id"]}/rollback',expected=400)
            request(client,'POST',f'/api/imports/{third["id"]}/rollback',expected=400)
            request(client,'POST',f'/api/imports/{fourth["id"]}/rollback')
            with connect(database) as conn,conn.cursor() as cur:
                cur.execute('SELECT product_weight_g FROM raw_products WHERE product_id=%s',(product['product_id'],))
                check(cur.fetchone()['product_weight_g']==original_weight,'Rollback did not restore product dimension')
            request(client,'POST',f'/api/imports/{second["id"]}/rollback')
            check(counts(database)==before,'Rollback did not restore historical data')
            check(request(client,'GET','/api/meta')['version']['batch_id']==first['id'],'Rollback version marker differs')
            evidence('回滚顺序保护、未变化批次无回滚与版本恢复',{'restored_orders':before['raw_orders']})

        check(counts(DATABASE)==production_before,'The production snapshot changed during testing')
        evidence('正式库行数与金额未变化',production_before)
        out['status']='PASS';out['before']=before;out['full']=full
    except Exception as error:
        failure=error;out['status']='FAIL';out['error']=f'{type(error).__name__}: {error}'
        log('TASK_ACCEPTANCE=FAIL '+out['error'])
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        if service_log is not None:service_log.close()
        out['finished_at']=datetime.now(timezone(timedelta(hours=8))).isoformat()
        report.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    if failure is not None:return 1
    log('TASK_ACCEPTANCE=PASS');log('REPORT='+str(report));return 0


if __name__=='__main__':raise SystemExit(main())
