import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlglot
from sqlglot import exp

from agent_core import sql_guard, skills, database
from agent_core.runtime import Agent
from engineering.pipeline import split_sql
from engineering.store import Store
from engineering.config import ROOT


@pytest.mark.parametrize('sql', [
    'DELETE FROM mart_order_delivery', 'DROP DATABASE olist_ecommerce',
    'SELECT * FROM mysql.user', 'SELECT * FROM information_schema.tables',
    'SELECT * FROM another.mart_order_delivery',
    'SELECT * FROM mart_order_delivery; DELETE FROM raw_orders',
    'SELECT SLEEP(2) FROM raw_orders', 'SELECT LOAD_FILE(".env") FROM raw_orders',
    'SELECT GET_LOCK("x",0) FROM raw_orders',
    'SELECT udf_unknown(order_id) FROM raw_orders',
    'SELECT @x FROM raw_orders', 'SELECT 1 INTO OUTFILE "x" FROM raw_orders',
    'SELECT * FROM raw_orders FOR UPDATE',
    'SELECT /*!50000 SLEEP(10) */ 1 FROM raw_orders',
    'SELECT * FROM raw_orders -- bypass',
    'WITH RECURSIVE x AS (SELECT * FROM raw_orders) SELECT * FROM x',
    'SELECT * FROM raw_orders LIMIT @n',
])
def test_query_guard_rejects_unsafe_or_unscoped_sql(sql):
    with pytest.raises(ValueError):
        sql_guard.select(sql, 'olist_ecommerce')


@pytest.mark.parametrize('sql', [
    'SELECT COUNT(*) AS orders FROM mart_order_delivery WHERE customer_state="RJ"',
    'WITH t AS (SELECT order_id,SUM(item_price) AS amount FROM mart_order_item_business GROUP BY order_id) SELECT * FROM t',
    'SELECT customer_state,AVG(product_value) AS aov FROM mart_order_delivery GROUP BY customer_state HAVING COUNT(*)>50',
    "SELECT DATE_FORMAT(order_purchase_timestamp,'%Y-%m') AS m,COUNT(*) AS n FROM raw_orders GROUP BY m",
    'SELECT ROW_NUMBER() OVER (ORDER BY order_id) AS rn FROM mart_order_delivery LIMIT 3',
    "SELECT 'value@#--' AS x FROM raw_orders LIMIT 1",
    'SELECT order_id FROM raw_orders UNION ALL SELECT order_id FROM mart_order_delivery',
])
def test_query_guard_supports_dynamic_query_shapes(sql):
    original, executed, cap = sql_guard.select(sql, 'olist_ecommerce', max_rows=123)
    assert original
    assert cap == 123
    assert 'LIMIT' in executed


def test_limit_clamps_outer_result_without_limiting_aggregation_input():
    _, executed, cap = sql_guard.select('SELECT COUNT(*) AS n FROM raw_orders', 'olist_ecommerce', max_rows=50)
    assert sqlglot.parse_one(executed, read='mysql').args['limit'].expression.this == '51'
    _, executed, _ = sql_guard.select('SELECT order_id FROM raw_orders LIMIT 2', 'olist_ecommerce', max_rows=50)
    assert executed.endswith('LIMIT 2')
    _, executed, _ = sql_guard.select('SELECT order_id FROM raw_orders LIMIT 200', 'olist_ecommerce', max_rows=50)
    assert executed.endswith('LIMIT 51')


@pytest.mark.parametrize('sql', [
    'CREATE DATABASE other', 'DROP TABLE raw_orders', 'UPDATE mart_order_delivery SET review_score=5',
    'CREATE VIEW stg_order_payments AS SELECT * FROM raw_orders',
    'INSERT INTO mart_order_delivery VALUES (1)',
    'CREATE TABLE other.stg_order_payments AS SELECT * FROM raw_order_payments',
    'CREATE TABLE stg_order_payments AS SELECT * FROM mysql.user',
    'INSERT INTO olist_ecommerce.mart_order_delivery SELECT * FROM raw_orders',
    'GRANT ALL ON *.* TO x', 'RENAME TABLE raw_orders TO x',
    'DELETE FROM mart_order_delivery', 'ALTER TABLE raw_orders ADD x INT',
    "CREATE TABLE stg_order_payments (order_id CHAR(32)) ENGINE='FEDERATED'",
    "CREATE TABLE stg_order_payments (order_id CHAR(32)) ENGINE=InnoDB DATA DIRECTORY='/tmp'",
])
def test_candidate_guard_preserves_formal_and_raw(sql):
    with pytest.raises(ValueError):
        sql_guard.candidate(sql, '_olist_work_task')


def test_all_existing_model_sql_can_be_explicitly_submitted_by_agent():
    count = 0
    for filename in ('04_create_staging_tables.sql', '06_create_mart_tables.sql', '11_create_operating_item_mart.sql'):
        for statement in split_sql((ROOT/'sql'/filename).read_text(encoding='utf-8-sig')):
            tree = sqlglot.parse_one(statement, read='mysql')
            if isinstance(tree, (exp.Create, exp.Insert, exp.Drop)):
                sql_guard.candidate(statement, '_olist_work_task')
                count += 1
    assert count == 21


def test_skills_are_registered_and_versioned():
    for name in skills.SKILLS:
        loaded = skills.load(name)
        assert len(loaded['sha256']) == 64
        assert len(loaded['instructions']) > 500
    with pytest.raises(ValueError):
        skills.load('../.env')


class FakeModel:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.received = []

    def complete(self, messages, tools):
        self.received.append(json.loads(json.dumps(messages)))
        return next(self.messages), {'total_tokens': 5}


def calls(*items):
    return {'role': 'assistant', 'content': None,
            'tool_calls': [{'id': f'call_{i}', 'type': 'function',
                            'function': {'name': name, 'arguments': json.dumps(args)}} for i, (name, args) in enumerate(items)]}


def fixture(tmp_path, messages=()):
    batches = Store(tmp_path/'batches')
    model = FakeModel(messages)
    jobs = SimpleNamespace(lock=threading.Lock())
    return Agent(tmp_path/'tasks', batches, jobs, 'olist_isolated', model), model


def row(agent, mode='query', share=False):
    task = agent.store.create('', '测试')
    return agent.store.update(task['id'], question='动态取数', mode=mode, batch=None,
                              status='running', share_results=share, queries=[], sql_log=[],
                              messages=[], candidate=None, checked=False, usage={}, rounds=0, tool_count=0)


@pytest.mark.parametrize('share', [False, True])
def test_real_tool_loop_loads_skill_and_redacts_data_rows(tmp_path, monkeypatch, share):
    agent, model = fixture(tmp_path, [
        calls(('inspect_schema', {})),
        calls(('run_query', {'sql': 'SELECT COUNT(*) AS n FROM raw_orders'})),
        {'role': 'assistant', 'content': '已完成取数。'},
    ])
    monkeypatch.setattr(database, 'catalog', lambda _: {'tables': {'raw_orders': [{'name': 'order_id'}]}})
    monkeypatch.setattr(database, 'query', lambda *_, **kw: {'sql': 'SELECT COUNT(*) AS n FROM raw_orders',
        'executed_sql': 'SELECT COUNT(*) AS n FROM raw_orders LIMIT 5001', 'columns': ['n'],
        'rows': [{'n': 12345}], 'row_count': 1, 'truncated': False, 'row_limit': 5000})
    task = row(agent, share=share)
    agent.run(task['id'])
    result = agent.store.get(task['id'])
    assert result['status'] == 'completed', result
    assert result['rounds'] == 3 and result['tool_count'] == 2
    assert result['skill']['name'] == 'data-retrieval'
    assert '12345' in json.dumps(model.received[-1]) if share else '12345' not in json.dumps(model.received[-1])
    assert result['queries'][0]['rows'][0]['n'] == 12345
    assert 'messages' not in agent.public(result)


def test_consent_and_batch_preconditions_before_model_or_tools(tmp_path):
    agent, model = fixture(tmp_path)
    with pytest.raises(ValueError, match='确认'):
        agent.start('取数', 'query')
    with pytest.raises(ValueError):
        agent.start('构建', 'engineering', consent=True)
    assert not model.received
    assert not agent.lock.locked()


def test_query_task_cannot_use_engineering_tool(tmp_path):
    agent, _ = fixture(tmp_path)
    task = row(agent)
    with pytest.raises(ValueError, match='不允许'):
        agent.call(task['id'], 'prepare_workspace', {})
    with pytest.raises(ValueError):
        agent.call(task['id'], 'run_query', {'sql':'SELECT * FROM raw_orders', 'database':'mysql'})


def test_ask_user_pauses_and_does_not_execute_later_calls(tmp_path, monkeypatch):
    agent, _ = fixture(tmp_path, [calls(('ask_user', {'question':'金额是否含运费？'}),
                                      ('run_query', {'sql':'SELECT * FROM raw_orders'}))])
    monkeypatch.setattr(database, 'query', lambda *_: pytest.fail('暂停后不应查询'))
    task = row(agent)
    agent.run(task['id'])
    result = agent.store.get(task['id'])
    assert result['status'] == 'needs_input'
    assert result['answer'] == '金额是否含运费？'
    assert len([m for m in result['messages'] if m['role']=='tool']) == 2


def test_failed_quality_check_invalidates_prior_pass(tmp_path, monkeypatch):
    import agent_core.runtime as runtime
    agent, _ = fixture(tmp_path)
    task = row(agent, mode='engineering')
    agent.store.update(task['id'], candidate='_olist_work_test', checked=True)
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*_):pass
    monkeypatch.setattr(runtime,'connect',lambda _:Connection())
    monkeypatch.setattr(database,'require_compatible',lambda _:None)
    monkeypatch.setattr(runtime,'raw_checks',lambda _: [{'name':'失配','status':'FAIL'}])
    monkeypatch.setattr(runtime,'model_checks',lambda _: [])
    with pytest.raises(ValueError):
        agent.call(task['id'], 'validate_candidate', {})
    assert agent.store.get(task['id'])['checked'] is False


def test_no_candidate_or_missing_confirmation_cannot_publish(tmp_path):
    agent, _ = fixture(tmp_path)
    task = row(agent, mode='engineering')
    with pytest.raises(ValueError):
        agent.confirm_publish(task['id'])


def test_candidate_executor_does_not_accept_formal_database():
    with pytest.raises(ValueError, match='候选库'):
        database.execute_candidate('olist_ecommerce', 'DROP TABLE mart_order_delivery')


def test_candidate_scope_checks_task_ownership(tmp_path):
    agent, _ = fixture(tmp_path)
    task = row(agent, mode='engineering')
    with pytest.raises(ValueError, match='不属于'):
        agent.scope({**task,'candidate':'_olist_work_'+'f'*16}, 'candidate')


def test_three_consecutive_tool_errors_stop_repairs(tmp_path):
    agent, model = fixture(tmp_path, [calls(('unregistered', {}))]*4)
    task = row(agent)
    agent.run(task['id'])
    result = agent.store.get(task['id'])
    assert result['status'] == 'failed'
    assert result['rounds'] == 3


def test_over_budget_response_usage_is_recorded_before_stop(tmp_path):
    agent, model=fixture(tmp_path)
    model.complete=lambda *_: ({'role':'assistant','content':'未执行下一步'}, {'prompt_tokens':350001,'completion_tokens':1,'total_tokens':350002})
    task=row(agent)
    agent.run(task['id'])
    finished=agent.store.get(task['id'])
    assert finished['status']=='failed'
    assert finished['usage']['total_tokens']==350002 and finished['rounds']==1
    assert finished['model_calls'][0]['usage']['total_tokens']==350002
    assert finished['tool_count']==0


def test_recover_marks_running_not_published(tmp_path):
    agent, _ = fixture(tmp_path)
    task = row(agent)
    agent.recover()
    assert agent.store.get(task['id'])['status'] == 'interrupted'
