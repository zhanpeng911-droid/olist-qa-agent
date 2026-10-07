import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from agent_core import database,sql_guard
from agent_core.runtime import Agent
from engineering.repair import seller_differences
from engineering.store import Store


def test_full_export_keeps_user_filters_and_limits():
    sql="SELECT order_id FROM raw_orders WHERE order_status='delivered' ORDER BY order_id"
    original,executed,maximum=sql_guard.select(sql,'olist_test',full_export=True)
    assert original==executed and maximum is None and 'LIMIT' not in executed
    _,limited,_=sql_guard.select(sql+' LIMIT 7','olist_test',full_export=True)
    assert limited.endswith('LIMIT 7')
    _,preview,limit=sql_guard.select(sql,'olist_test')
    assert preview.endswith('LIMIT 5001') and limit==5000


@pytest.mark.parametrize('sql',['DELETE FROM raw_orders','SELECT * FROM mysql.user','SELECT LOAD_FILE(\'.env\') FROM raw_orders','SELECT * FROM other.raw_orders'])
def test_full_export_has_same_sql_security(sql):
    with pytest.raises(ValueError):sql_guard.select(sql,'olist_test',full_export=True)


class Cursor:
    description=[('value',)]
    def __init__(self,rows):self.rows=rows
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def execute(self,sql):self.sql=sql
    def fetchone(self):return {'EXPLAIN':json.dumps({'rows_produced_per_join':3})}
    def fetchall(self):return []
    def __iter__(self):return iter(self.rows)


class Connection:
    open=True
    def __init__(self,rows):self.cur=Cursor(rows)
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def cursor(self):return self.cur
    def rollback(self):pass
    def close(self):self.open=False


def mock_export(monkeypatch,rows):
    conn=Connection(rows)
    monkeypatch.setattr(database,'connect',lambda *_,**__:conn)
    monkeypatch.setattr(database,'watchdog',lambda *_:SimpleNamespace(cancel=lambda:None))
    return conn


def test_full_csv_preserves_decimal_and_formula_safety(tmp_path,monkeypatch):
    mock_export(monkeypatch,[{'value':Decimal('123456789.0123456789')},{'value':'=bad'},{'value':'line1\nline2\\r'},{'value':None}])
    result=database.full_csv('olist_test','SELECT payment_value AS value FROM raw_order_payments',tmp_path)
    text=(tmp_path/'result.csv').read_text(encoding='utf-8-sig')
    assert '123456789.0123456789' in text and "'=bad" in text and 'line2\\r' in text
    assert result['row_count']==4 and result['complete'] and not result['truncated']


@pytest.mark.parametrize('budget',[{'max_rows':1},{'max_bytes':1},{'seconds':0}])
def test_export_budget_failure_never_leaves_downloadable_partial(tmp_path,monkeypatch,budget):
    conn=mock_export(monkeypatch,[{'value':1},{'value':2}])
    with pytest.raises(ValueError):database.full_csv('olist_test','SELECT order_id AS value FROM raw_orders',tmp_path,**budget)
    assert not (tmp_path/'result.csv').exists() and not (tmp_path/'result.csv.part').exists()
    assert not conn.open


def fixture(tmp_path):
    agent=Agent(tmp_path/'tasks',Store(tmp_path/'batches'),SimpleNamespace(lock=threading.Lock()),'olist_test')
    row=agent.store.create('','test');agent.store.update(row['id'],status='completed',queries=[{'id':'r','sql':'SELECT order_id FROM raw_orders'}])
    return agent,row['id']


def test_full_export_requires_confirmation_ownership_and_published_scope(tmp_path):
    agent,key=fixture(tmp_path)
    with pytest.raises(ValueError,match='确认'):agent.exports.start(key,'r')
    with pytest.raises(ValueError,match='不属于'):agent.exports.start(key,'other',True)
    agent.store.update(key,queries=[{'id':'r','sql':'SELECT order_id FROM raw_orders','scope':'candidate'}])
    with pytest.raises(ValueError,match='已发布'):agent.exports.start(key,'r',True)
    assert not agent.jobs.lock.locked()


def test_export_download_and_restart_are_fail_closed(tmp_path):
    agent,key=fixture(tmp_path)
    exp=agent.exports.store.create('','export')
    agent.exports.store.update(exp['id'],status='running',task=key,complete=False)
    agent.exports.recover()
    with pytest.raises(ValueError):agent.exports.artifact(key,exp['id'])
    assert agent.exports.store.get(exp['id'])['status']=='interrupted'
    agent.exports.store.update(exp['id'],status='completed',complete=True)
    with pytest.raises(ValueError,match='缺失'):agent.exports.artifact(key,exp['id'])
    (agent.exports.store.root/exp['id']/'result.csv').write_text('x\n1\n')
    with pytest.raises(ValueError):agent.exports.artifact('other',exp['id'])
    assert agent.exports.artifact(key,exp['id']).is_file()


def test_ai_rollback_confirmation_and_plan_token(tmp_path,monkeypatch):
    agent,key=fixture(tmp_path)
    with pytest.raises(ValueError,match='确认'):agent.confirm_rollback(key,'bad')
    monkeypatch.setattr(agent,'rollback_plan',lambda _: {'token':'valid'})
    with pytest.raises(ValueError,match='变化'):agent.confirm_rollback(key,'old',True)
    assert not agent.jobs.lock.locked()
    agent.jobs.lock.acquire()
    with pytest.raises(ValueError,match='执行'):agent.confirm_rollback(key,'valid',True)
    agent.jobs.lock.release()


def test_unpublished_agent_task_cannot_preview_restore(tmp_path):
    agent,key=fixture(tmp_path)
    with pytest.raises(ValueError,match='发布'):agent.rollback_plan(key)


@pytest.mark.parametrize('current,retired,expected',[
    ('previous','current','rolled_back'),('current','absent','published'),
    ('unexpected','current','rolling_back'),(None,None,'rolling_back')])
def test_rollback_recovery_requires_committed_evidence(tmp_path,monkeypatch,current,retired,expected):
    import agent_core.runtime as runtime
    agent,key=fixture(tmp_path)
    batch=agent.batches.create('','test')['id']
    row=agent.store.update(key,status='rolling_back',batch=batch,
                           rollback_previous_token='previous',rollback_current_token='current')
    monkeypatch.setattr(runtime,'connect',lambda:Connection([]))
    def token(_,name):
        if current is None:raise OSError('database unavailable')
        return current if name==agent.database else retired
    monkeypatch.setattr(database,'version_token',token)
    agent._reconcile_rollback(row)
    assert agent.store.get(key)['status']==expected
    if expected=='rolled_back':assert agent.batches.get(batch)['status']=='rolled_back_by_agent'


def test_source_repair_is_not_generic_upsert():
    source=[{'seller_id':'a'*32,'seller_zip_code_prefix':'22050','seller_city':'rio \\rio','seller_state':'RJ'}]
    old=[{**source[0],'seller_city':'rio \rio'}]
    assert seller_differences(old,source)[0]['after']=='rio \\rio'
    assert seller_differences(source,source)==[]
    with pytest.raises(ValueError):seller_differences([{**old[0],'seller_city':'different'}],source)
    with pytest.raises(ValueError):seller_differences([{**old[0],'seller_state':'SP'}],source)
    with pytest.raises(ValueError):seller_differences([],source)
    with pytest.raises(ValueError):seller_differences(old,source*2)
