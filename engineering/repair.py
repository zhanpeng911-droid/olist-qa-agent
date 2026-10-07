"""Narrow, source-proven legacy text repair, not a general upsert bypass."""
import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

from agent_core.database import require_compatible, version_token
from engineering.config import connect, ident
from engineering.contracts import CONTRACTS, TABLES
from engineering.pipeline import existing_tables, publish, read_version, require_pass, scalar
from engineering.quality import raw_checks, model_checks


def seller_differences(baseline,source):
    old={r['seller_id']:r for r in baseline};new={r['seller_id']:r for r in source}
    if len(old)!=len(baseline) or len(new)!=len(source) or old.keys()!=new.keys():
        raise ValueError('修复要求完整卖家快照且业务键唯一；键集合不一致，不能作为字符修复')
    changes=[]
    for key,row in new.items():
        if not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('卖家业务键无效')
        for field in ('seller_zip_code_prefix','seller_state'):
            if str(old[key][field])!=row[field]:raise ValueError('存在非城市字段变化，不适用字符修复')
        if old[key]['seller_city']==row['seller_city']:continue
        city=row['seller_city']
        if not city or '\\r' not in city or city.replace('\\r','\r')!=old[key]['seller_city']:
            raise ValueError('发现不能追溯到旧反斜杠转义的城市差异；需独立业务审核')
        changes.append({'seller_id':key,'before':old[key]['seller_city'],'after':city})
    return changes


def preview(database,directory):
    path=Path(directory)/CONTRACTS['raw_sellers'].filename
    if not path.is_file():raise ValueError('源目录缺少标准卖家CSV')
    if path.stat().st_size>20*1024**2:raise ValueError('字符修复卖家文件超过20MiB，请单独审核')
    with path.open(encoding='utf-8-sig',newline='') as file:
        reader=csv.DictReader(file)
        if reader.fieldnames!=list(CONTRACTS['raw_sellers'].columns):raise ValueError('卖家CSV字段不匹配')
        source=list(reader)
    with connect(database) as conn,conn.cursor() as cur:
        if not set(TABLES)<=existing_tables(conn,database):raise ValueError('需有完整已发布三层数据')
        cur.execute('SELECT seller_id,seller_zip_code_prefix,seller_city,seller_state FROM raw_sellers')
        changes=seller_differences(cur.fetchall(),source)
        impacts={t:0 for t in ('raw_sellers','mart_order_seller_delivery','mart_order_item_business')}
        for change in changes:
            for table in impacts:
                cur.execute(f'SELECT seller_city FROM {ident(table)} WHERE seller_id=%s',(change['seller_id'],))
                rows=cur.fetchall()
                if any(r['seller_city'] not in (change['before'],change['after']) for r in rows):
                    raise ValueError('Mart城市内容不一致，停止窄范围修复')
                impacts[table]+=sum(r['seller_city']==change['before'] for r in rows)
        base=version_token(conn,database)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    token=hashlib.sha256(json.dumps([database,base,digest,changes,impacts],sort_keys=True).encode()).hexdigest()
    return {'database':database,'source':str(path.resolve()),'source_sha256':digest,'base_token':base,
            'changes':changes,'impacts':impacts,'token':token,'ready':bool(changes),
            'effect':'只修复有CSV证据的旧城市转义；克隆三层、质量验证、原子发布并保存完整前版'}


def execute(jobs,batch,plan):
    store=jobs.store;database=jobs.database
    candidate='_olist_work_'+batch;backup='_olist_backup_'+batch
    store.event(batch,'准备字符修复候选版本',1,status='building',candidate=candidate,backup=backup)
    try:
        source=store.root/batch/'source';source.mkdir(exist_ok=True)
        shutil.copy2(plan['source'],source/CONTRACTS['raw_sellers'].filename)
        with connect() as control,control.cursor() as cur:
            if not scalar(cur,"SELECT GET_LOCK('olist_engineering_publish',0)"):raise ValueError('其他数据任务执行中')
            try:
                fresh=preview(database,source)
                if fresh['token']!=plan['token'] or not fresh['ready']:raise ValueError('源文件或数据库已变化，请重新预览修复')
                previous=read_version(control,database);tables=existing_tables(control,database)
                cur.execute(f'CREATE DATABASE {ident(candidate)} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
                with connect(candidate) as work,work.cursor() as dst:
                    for index,table in enumerate((*TABLES,'_eng_version')):
                        if table not in tables:continue
                        dst.execute(f'CREATE TABLE {ident(table)} LIKE {ident(database)}.{ident(table)}')
                        dst.execute(f'INSERT INTO {ident(table)} SELECT * FROM {ident(database)}.{ident(table)}')
                        store.event(batch,'克隆 '+table,5+index*4)
                    impacts={table:0 for table in fresh['impacts']}
                    for change in fresh['changes']:
                        for table in impacts:
                            dst.execute(f'UPDATE {ident(table)} SET seller_city=%s WHERE seller_id=%s AND BINARY seller_city=BINARY %s',
                                        (change['after'],change['seller_id'],change['before']))
                            impacts[table]+=dst.rowcount
                    if impacts!=fresh['impacts']:raise ValueError('修复影响行数与预览不一致，停止发布')
                    require_compatible(work);checks=raw_checks(work)+model_checks(work);require_pass(checks)
                    if version_token(control,database)!=fresh['base_token']:raise ValueError('正式库已变化，停止修复发布')
                    meta={**previous,'source_text_repair':batch,'repair_source_sha256':fresh['source_sha256']}
                    dst.execute('CREATE TABLE IF NOT EXISTS _eng_version (batch_id VARCHAR(32) PRIMARY KEY,published_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,metadata TEXT NULL) ENGINE=InnoDB')
                    dst.execute('DELETE FROM _eng_version')
                    dst.execute('INSERT INTO _eng_version (batch_id,metadata) VALUES (%s,%s)',(batch,json.dumps(meta)))
                publish(control,database,candidate,backup,tables)
                store.event(batch,'源字符修复已发布，完整上一版保留',100,status='published',checks=checks,repair= fresh,
                            published_token=version_token(control,database))
            finally:cur.execute("SELECT RELEASE_LOCK('olist_engineering_publish')")
    except Exception as error:
        # Detect an already committed atomic rename before declaring failure.
        try:
            with connect() as conn:committed=read_version(conn,database).get('source_text_repair')==batch
        except Exception:committed=False
        store.event(batch,'已核验修复发布完成' if committed else '修复失败，已发布版本未覆盖',100,
                    status='published' if committed else 'failed',error=None if committed else str(error))
