import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_core import database
from agent_core.model import Model
from agent_core.runtime import Agent
from engineering.store import Store
import server.main as server


class SequenceModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item, {'total_tokens': 1}


def tool(name, args):
    return {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'test_'+name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}


def fixture(tmp_path, monkeypatch, responses):
    model = SequenceModel(responses)
    jobs = SimpleNamespace(lock=threading.Lock())
    agent = Agent(tmp_path/'tasks', Store(tmp_path/'batches'), jobs, 'olist_api_test', model)
    monkeypatch.setattr(server, 'agent', agent)
    return TestClient(server.app), agent, model


def finish(agent, key):
    for _ in range(300):
        row = agent.store.get(key)
        if row['status'] != 'running' and not agent.lock.locked():
            return row
        time.sleep(.01)
    pytest.fail('Test worker did not finish')


def test_http_rejects_missing_consent_mode_and_batch_without_model(tmp_path, monkeypatch):
    client, agent, model = fixture(tmp_path, monkeypatch, [])
    for body in ({'question': '取数'}, {'question': '取数', 'consent': True, 'mode': 'shell'},
                 {'question': '', 'consent': True}, {'question': '建库', 'mode': 'engineering', 'consent': True}):
        assert client.post('/api/agent/tasks', json=body).status_code == 400
    assert not agent.store.list() and model.calls == 0


def test_http_actual_results_and_download_ownership(tmp_path, monkeypatch):
    client, agent, _ = fixture(tmp_path, monkeypatch, [
        tool('run_query', {'sql': 'SELECT COUNT(*) AS n FROM raw_orders'}),
        {'role': 'assistant', 'content': '已完成取数，结果见本地表格。'},
    ])
    def query(db, sql, directory, **kwargs):
        directory.mkdir(parents=True)
        (directory/'result.csv').write_text('\ufeffn\n12\n', encoding='utf-8')
        return {'sql': sql, 'executed_sql': sql+' LIMIT 5001', 'columns': ['n'], 'rows': [{'n': 12}],
                'row_count': 1, 'truncated': False, 'row_limit': 5000}
    monkeypatch.setattr(database, 'query', query)
    response = client.post('/api/agent/tasks', json={'question': '取订单量', 'consent': True})
    assert response.status_code == 200
    row = finish(agent, response.json()['id'])
    public = client.get('/api/agent/tasks/'+row['id']).json()
    assert public['status'] == 'completed' and 'messages' not in public and 'base_token' not in public
    key = row['queries'][0]['id']
    assert client.get(f'/api/agent/tasks/{row["id"]}/results/{key}/csv').content.decode('utf-8-sig').splitlines() == ['n', '12']
    assert client.get(f'/api/agent/tasks/{row["id"]}/results/other/csv').status_code == 400
    assert client.get('/api/agent/tasks/missing').status_code == 404
    assert client.post(f'/api/agent/tasks/{row["id"]}/publish', json={'confirmed': False}).status_code == 400
    assert client.post(f'/api/agent/tasks/{row["id"]}/publish', json={'confirmed': True}).status_code == 400


def test_http_api_failure_is_visible_and_releases_shared_locks(tmp_path, monkeypatch):
    client, agent, _ = fixture(tmp_path, monkeypatch, [ValueError('模型连接或响应解析失败')])
    result = client.post('/api/agent/tasks', json={'question': '取数', 'consent': True})
    row = finish(agent, result.json()['id'])
    assert row['status'] == 'failed' and '连接' in row['error']
    assert not agent.lock.locked() and not agent.jobs.lock.locked()


def test_http_clarification_retains_task_and_requires_renewed_consent(tmp_path, monkeypatch):
    client, agent, model = fixture(tmp_path, monkeypatch, [tool('ask_user', {'question': '金额阈值？'}),
        {'role': 'assistant', 'content': '还需说明统计范围。'}])
    result = client.post('/api/agent/tasks', json={'question': '高价值客户', 'consent': True})
    row = finish(agent, result.json()['id'])
    assert row['status'] == 'needs_input' and row['answer'] == '金额阈值？'
    route = f'/api/agent/tasks/{row["id"]}/continue'
    assert client.post(route, json={'answer': '500'}).status_code == 400
    assert model.calls == 1
    assert client.post(route, json={'answer': '500', 'consent': True}).status_code == 200
    row = finish(agent, row['id'])
    assert row['status'] == 'needs_input' and row['rounds'] == 2
    assert any(m.get('content') == '500' for m in row['messages'])


@pytest.mark.parametrize('base', ['http://api.deepseek.com','https://x:secret@api.deepseek.com','https://api.deepseek.com?key=secret'])
def test_model_rejects_unsafe_api_destination_before_network(monkeypatch, base):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-secret')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', base)
    with pytest.raises(ValueError, match='HTTPS'):
        Model().complete([{'role':'user','content':'test'}], [])


def test_model_http_failure_does_not_leak_response_or_key(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-secret')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    class Client:
        def __init__(self, **kwargs):pass
        def __enter__(self):return self
        def __exit__(self, *args):pass
        def post(self, *args, **kwargs):
            return httpx.Response(401, json={'private': 'test-secret'})
    monkeypatch.setattr(httpx, 'Client', Client)
    with pytest.raises(ValueError) as error:
        Model().complete([{'role':'user','content':'test'}], [])
    assert '401' in str(error.value) and 'test-secret' not in str(error.value)
