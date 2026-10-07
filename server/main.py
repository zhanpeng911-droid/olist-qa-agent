from __future__ import annotations

import csv
import io
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pymysql

from engineering.config import ARTIFACTS, DATABASE, DATASET, ROOT, connect, ident
from engineering.contracts import CONTRACTS, MARTS
from engineering.jobs import Jobs
from engineering.pipeline import rollback
from engineering.reports import metadata, overview, growth
from engineering.store import Store
from engineering.policy import validate_policy
from engineering.retention import plan, prune
from engineering import repair
from agent_core.runtime import Agent
from agent_core.model import settings as model_settings

store = Store(ARTIFACTS)
jobs = Jobs(store)
agent = Agent(ARTIFACTS.parent / 'agent', store, jobs, DATABASE)


@asynccontextmanager
async def lifespan(app):
    jobs.recover()
    agent.recover()
    yield


app = FastAPI(title='Olist 数据工程与经营报表', version='3.0', lifespan=lifespan)


@app.get('/api/health')
def health():
    # Startup detection must not wait for million-row metadata counts.
    return {'status':'ok','app_version':'3.0'}


def clean(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: clean(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def response(value):
    return JSONResponse(clean(value))


@app.exception_handler(ValueError)
async def invalid_request(request, error):
    return JSONResponse({'detail': str(error)}, status_code=400)


@app.exception_handler(KeyError)
async def missing(request, error):
    return JSONResponse({'detail': str(error)}, status_code=404)


@app.exception_handler(pymysql.MySQLError)
async def database_error(request, error):
    code=error.args[0] if error.args else 'unknown'
    return JSONResponse({'detail': f'数据库操作未完成（MySQL {code}），请检查数据库服务、表结构或连接权限。'},status_code=503)


@app.get('/api/meta')
def meta():
    result = metadata(DATABASE)
    result.update(app_version='3.0', source_dir=DATASET, files=[s.filename for s in CONTRACTS.values()])
    return response(result)


@app.get('/api/imports')
def imports():
    return response(store.list())


@app.get('/api/imports/{batch}')
def batch_details(batch: str):
    return response(store.get(batch))


class LocalBatch(BaseModel):
    directory: str
    label: str = ''
    merge_mode: str = 'append_only'
    snapshot_at: str | None = None


@app.post('/api/imports/local')
def local_import(body: LocalBatch):
    directory = Path(body.directory)
    if not directory.is_dir():
        raise ValueError('源目录不存在')
    if jobs.lock.locked():
        raise ValueError('当前有数据任务正在执行')
    stamp = validate_policy(body.merge_mode, body.snapshot_at)
    row = store.create(directory, body.label)
    row = store.update(row['id'], merge_mode=body.merge_mode, snapshot_at=stamp, database=DATABASE)
    jobs.submit(jobs.prepare, row['id'])
    return response(row)


class UploadedBatch(BaseModel):
    filenames: list[str]
    label: str = ''
    merge_mode: str = 'append_only'
    snapshot_at: str | None = None


@app.post('/api/imports/upload')
def upload_batch(body: UploadedBatch):
    expected = {s.filename for s in CONTRACTS.values()}
    if len(body.filenames) != 9 or set(body.filenames) != expected:
        raise ValueError('请一次选择九个标准命名的原始 CSV 文件')
    stamp = validate_policy(body.merge_mode, body.snapshot_at)
    row = store.create('', body.label)
    target = store.root / row['id'] / 'source'
    target.mkdir()
    return response(store.update(row['id'], status='uploading', source=str(target), merge_mode=body.merge_mode, snapshot_at=stamp, database=DATABASE))


@app.put('/api/imports/{batch}/files/{filename}')
async def upload_file(batch: str, filename: str, request: Request):
    row = store.get(batch)
    if row['status'] != 'uploading' or filename not in {s.filename for s in CONTRACTS.values()}:
        raise ValueError('当前批次不可上传此文件')
    target = store.root / batch / 'source' / filename
    part = target.with_suffix('.part')
    size = 0
    with part.open('wb') as file:
        async for chunk in request.stream():
            size += len(chunk)
            if size > 2 * 1024**3:
                raise ValueError('单文件超过 2 GiB 上限')
            file.write(chunk)
    part.replace(target)
    return {'filename':filename,'bytes':size}


@app.post('/api/imports/{batch}/validate')
def validate_upload(batch: str):
    if store.get(batch)['status'] != 'uploading':
        raise ValueError('该批次不是待校验上传批次')
    jobs.submit(jobs.prepare, batch)
    return {'id':batch,'status':'validating'}


@app.post('/api/imports/{batch}/execute')
def execute(batch: str):
    if store.get(batch)['status'] != 'ready':
        raise ValueError('批次必须先通过源文件校验')
    jobs.submit(jobs.execute, batch)
    return {'id':batch,'status':'building'}


@app.post('/api/imports/{batch}/rollback')
def rollback_batch(batch: str):
    row = store.get(batch)
    if row['status'] != 'published':
        raise ValueError('批次未发布或已回滚')
    if not jobs.lock.acquire(blocking=False):
        raise ValueError('数据任务执行中')
    try:
        rollback(DATABASE, batch, row['backup'])
        return response(store.event(batch, '已恢复上一版三层数据', 100, status='rolled_back'))
    finally:
        jobs.lock.release()


@app.get('/api/maintenance/backups')
def backup_preview(keep: int = 3):
    return response(plan(store, DATABASE, keep))


class Cleanup(BaseModel):
    keep: int = 3
    token: str
    confirmed_schemas: list[str]


@app.post('/api/maintenance/backups/prune')
def backup_prune(body: Cleanup):
    if not jobs.lock.acquire(blocking=False):
        raise ValueError('数据任务执行中')
    try:
        return response(prune(store, DATABASE, body.keep, body.token, body.confirmed_schemas))
    finally:
        jobs.lock.release()


class TextRepair(BaseModel):
    directory: str
    token: str = ''
    confirmed: bool = False


@app.post('/api/maintenance/source-text/preview')
def preview_text_repair(body: TextRepair):
    return response(repair.preview(DATABASE,body.directory))


@app.post('/api/maintenance/source-text/repair')
def confirm_text_repair(body: TextRepair):
    if not body.confirmed:raise ValueError('请先核对源文件证据、影响行数并确认字符修复')
    if jobs.lock.locked():raise ValueError('数据任务执行中')
    fresh=repair.preview(DATABASE,body.directory)
    if not fresh['ready'] or body.token!=fresh['token']:raise ValueError('没有可修复差异或计划已变化，请重新预览')
    row=store.create(body.directory,'旧导入字符修复')
    store.update(row['id'],database=DATABASE,kind='source_text_repair',repair=fresh)
    jobs.submit(repair.execute,jobs,row['id'],fresh)
    return response({'id':row['id'],'status':'building'})


@app.get('/api/reports/overview')
def report_overview(start: str|None=None,end: str|None=None,state: str|None=None):
    return response(overview(DATABASE,start,end,state))


@app.get('/api/reports/growth')
def report_growth(start: str|None=None,end: str|None=None,state: str|None=None,year_a:int=2017,year_b:int=2018,month_from:int=1,month_to:int=8):
    return response(growth(DATABASE,start,end,state,year_a,year_b,month_from,month_to))


@app.get('/api/exports/{table}')
def export_mart(table: str):
    if table not in MARTS:
        raise ValueError('仅支持导出三张 Mart 表')
    def stream():
        with connect(DATABASE, stream=True) as conn, conn.cursor() as cur:
            cur.execute(f'SELECT * FROM {ident(table)} ORDER BY order_id')
            out = io.StringIO(newline='')
            writer = csv.writer(out)
            writer.writerow([column[0] for column in cur.description])
            yield '\ufeff' + out.getvalue()
            out.seek(0); out.truncate()
            for row in cur:
                writer.writerow(row.values())
                if out.tell()>64*1024:
                    yield out.getvalue()
                    out.seek(0); out.truncate()
            if out.tell():
                yield out.getvalue()
    return StreamingResponse(stream(), media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{table}.csv"'})


class AgentTask(BaseModel):
    question: str
    mode: str = 'query'
    batch: str | None = None
    consent: bool = False
    share_results: bool = False


@app.get('/api/agent/settings')
def agent_settings():
    from agent_core.skills import SKILLS
    return {**model_settings(), 'skills': SKILLS, 'query_row_limit': 5000,
            'full_export':{'max_rows':2_000_000,'max_bytes':1024**3,'seconds':300,'preview_rows':50},
            'engineering_scope': '已注册的 Olist 九表合约；候选库自主建模，用户确认发布'}


@app.get('/api/agent/tasks')
def agent_tasks():
    return response([agent.public(r) for r in agent.store.list()])


@app.post('/api/agent/tasks')
def create_agent_task(body: AgentTask):
    return response(agent.start(body.question, body.mode, body.batch,
                               consent=body.consent, share_results=body.share_results))


@app.get('/api/agent/tasks/{task_id}')
def get_agent_task(task_id: str):
    return response(agent.public(agent.store.get(task_id)))


class Clarification(BaseModel):
    answer: str
    consent: bool = False


@app.post('/api/agent/tasks/{task_id}/continue')
def continue_agent_task(task_id: str, body: Clarification):
    return response(agent.continue_task(task_id, body.answer, body.consent))


class PublishConfirmation(BaseModel):
    confirmed: bool = False


@app.post('/api/agent/tasks/{task_id}/publish')
def publish_agent_task(task_id: str, body: PublishConfirmation):
    if not body.confirmed:
        raise ValueError('请确认候选SQL、校验结果与正式发布')
    return response(agent.confirm_publish(task_id))


@app.get('/api/agent/tasks/{task_id}/rollback-plan')
def agent_rollback_preview(task_id: str):
    return response(agent.rollback_plan(task_id))


class RollbackConfirmation(BaseModel):
    token: str
    confirmed: bool = False


@app.post('/api/agent/tasks/{task_id}/rollback')
def rollback_agent_task(task_id: str,body:RollbackConfirmation):
    return response(agent.confirm_rollback(task_id,body.token,body.confirmed))


@app.get('/api/agent/tasks/{task_id}/exports')
def agent_exports(task_id: str):
    return response(agent.exports.list(task_id))


@app.post('/api/agent/tasks/{task_id}/results/{result_id}/export-full')
def full_query_export(task_id: str,result_id: str,body:PublishConfirmation):
    return response(agent.exports.start(task_id,result_id,body.confirmed))


@app.get('/api/agent/tasks/{task_id}/exports/{export_id}/csv')
def full_export_download(task_id: str,export_id: str):
    return FileResponse(agent.exports.artifact(task_id,export_id),media_type='text/csv',filename='full_query_'+export_id+'.csv')


@app.get('/api/agent/tasks/{task_id}/results/{result_id}/csv')
def agent_result_csv(task_id: str, result_id: str):
    row = agent.store.get(task_id)
    if result_id not in {r['id'] for r in row['queries']}:
        raise ValueError('结果不存在')
    return FileResponse(agent.store.root / task_id / result_id / 'result.csv',
                        media_type='text/csv', filename='query_' + result_id + '.csv')


@app.get('/api/agent/tasks/{task_id}/sql')
def agent_generated_sql(task_id: str):
    row = agent.store.get(task_id)
    if not row['sql_log']:
        raise ValueError('该任务未生成工程SQL')
    return FileResponse(agent.store.root / task_id / 'generated.sql',
                        media_type='text/plain', filename='generated_' + task_id + '.sql')


dist = ROOT / 'web' / 'dist'
if (dist / 'assets').is_dir():
    app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')


@app.get('/{path:path}')
def spa(path: str):
    if path.startswith('api/'):
        raise HTTPException(404, '接口不存在')
    if not (dist / 'index.html').is_file():
        raise HTTPException(503, '前端尚未构建')
    return FileResponse(dist / 'index.html', headers={'Cache-Control':'no-cache'})
