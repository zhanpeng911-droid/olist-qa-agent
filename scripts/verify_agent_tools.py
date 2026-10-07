"""Real MySQL tool acceptance without external LLM calls or formal writes.

The scripted model exercises the same function-call protocol. It does NOT prove
DeepSeek planning quality. Uses a scoped real-Olist CSV bundle and isolated DB.
"""
from __future__ import annotations

import csv
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from agent_core.runtime import Agent
from agent_core import database
from engineering.config import ARTIFACTS, DATABASE, connect, ident
from engineering.contracts import CONTRACTS, TABLES
from engineering.jobs import Jobs
from engineering.pipeline import scalar
from engineering.store import Store


def call(name, args):
    return {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': uuid.uuid4().hex, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}


class ScriptedEngineeringModel:
    def __init__(self):
        self.next_table = iter(TABLES[9:])
        self.position = 0

    def complete(self, messages, tools):
        usage = {'total_tokens': 0}
        initial = [('inspect_batch', {}), ('inspect_schema', {}), ('prepare_workspace', {})]
        if self.position < len(initial):
            name, args = initial[self.position]
            self.position += 1
            return call(name, args), usage
        last = messages[-1]
        if last['role'] == 'tool':
            output = json.loads(last['content'])
            if 'error' in output:
                raise AssertionError(output)
            if 'statements' in output:
                statements = output['statements']
                # Scripted (not real-LLM) modification verifies that execute_sql
                # really accepts SQL rather than silently dispatching a template.
                if output['table'] == 'stg_order_payments':
                    statements = [s.replace('KEY idx_stg_payment_type (primary_payment_type)',
                                            'KEY idx_stg_payment_type (primary_payment_type), KEY idx_agent_payment_total (payment_total)') for s in statements]
                return call('execute_sql', {'statements': statements}), usage
            if output.get('status') == 'validated_pending_user_confirmation':
                return {'role': 'assistant', 'content': '候选建模与质量门已完成，等待用户确认。'}, usage
        table = next(self.next_table, None)
        if table:
            return call('reference_sql', {'table': table}), usage
        return call('validate_candidate', {}), usage


def main():
    key = uuid.uuid4().hex[:8]
    root = ARTIFACTS/('agent_tools_acceptance_'+key)
    root.mkdir(parents=True)
    source = root/'source'; source.mkdir()
    target = 'olist_agent_accept_' + key
    checks = []
    def passed(name):
        checks.append({'name': name, 'status': 'PASS'})
        print('PASS ' + name, flush=True)
    with connect() as control:
        before = database.version_token(control, DATABASE)
    result = database.query(DATABASE, "SELECT customer_state,COUNT(*) AS orders,SUM(product_value) AS gmv FROM mart_order_delivery WHERE order_status='delivered' AND order_purchase_timestamp>='2018-01-01' AND order_purchase_timestamp<'2018-07-01' GROUP BY customer_state ORDER BY orders DESC", root/'query')
    assert len(result['rows']) >= 10 and not result['truncated']
    passed('全量正式Mart：新条件组合、SQL执行计划与本地CSV取数')
    cut = database.query(DATABASE, 'SELECT order_id FROM mart_order_delivery ORDER BY order_id', root/'cut', max_rows=10)
    assert cut['row_count'] == 10 and cut['truncated']
    passed('取数输出上限与截断标记')
    with connect(DATABASE) as conn, conn.cursor() as cur:
        cur.execute("SELECT order_id FROM raw_orders WHERE order_status='delivered' ORDER BY order_id LIMIT 30")
        order_ids = [r['order_id'] for r in cur.fetchall()]
        marks = ','.join(['%s']*len(order_ids))
        scopes = {
            'raw_orders': f'order_id IN ({marks})',
            'raw_order_items': f'order_id IN ({marks})',
            'raw_order_payments': f'order_id IN ({marks})',
            'raw_order_reviews': f'order_id IN ({marks})',
            'raw_customers': f'customer_id IN (SELECT customer_id FROM raw_orders WHERE order_id IN ({marks}))',
            'raw_products': f'product_id IN (SELECT product_id FROM raw_order_items WHERE order_id IN ({marks}))',
            'raw_sellers': f'seller_id IN (SELECT seller_id FROM raw_order_items WHERE order_id IN ({marks}))',
        }
        for table, spec in CONTRACTS.items():
            columns = ','.join(ident(c) for c in spec.columns)
            if table in scopes:
                cur.execute(f'SELECT {columns} FROM {ident(table)} WHERE ' + scopes[table], order_ids)
            elif table == 'raw_geolocation':
                cur.execute(f'SELECT {columns} FROM {ident(table)} LIMIT 100')
            else:
                cur.execute(f'SELECT {columns} FROM {ident(table)}')
            with (source/spec.filename).open('w', encoding='utf-8-sig', newline='') as file:
                writer = csv.writer(file); writer.writerow(spec.columns)
                writer.writerows([[r[c] for c in spec.columns] for r in cur.fetchall()])
    batches = Store(root/'batches')
    jobs = Jobs(batches, target)
    batch = batches.create(source, '实际Olist三十笔订单')
    jobs.prepare(batch['id'])
    assert batches.get(batch['id'])['status'] == 'ready'
    passed('真实CSV小批次：九表解析与业务键校验')
    agent = Agent(root/'tasks', batches, jobs, target, ScriptedEngineeringModel())
    task = agent.store.create('', '工具链验收（脚本模型）')
    agent.store.update(task['id'], question='为Olist批次建模并等待确认', mode='engineering', batch=batch['id'],
                       status='running', share_results=False, queries=[], sql_log=[], messages=[], candidate=None,
                       checked=False, usage={}, rounds=0, tool_count=0)
    agent.run(task['id'])
    row = agent.store.get(task['id'])
    assert row['status'] == 'ready_for_publish', row.get('error') or row
    assert len(row['checks']) == 36 and all(c['status'] != 'FAIL' for c in row['checks'])
    passed('工具调用循环：自主候选建库、21条显式SQL、兼容性与36项质量门')
    with connect() as control, control.cursor() as cur:
        cur.execute('SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s', (target,))
        assert cur.fetchone()['n'] == 0
    passed('模型完成后仅待确认，未自动发布')
    agent.confirm_publish(task['id'])
    with connect(target) as conn, conn.cursor() as cur:
        assert scalar(cur, 'SELECT COUNT(*) FROM mart_order_delivery') == 30
    passed('用户确认：只向隔离验收库原子发布')
    # Fresh-schema first publish has no complete prior data version. Keep its backup
    # and candidate as evidence, just as the normal fixed-flow tests do.
    with connect() as control:
        assert database.version_token(control, DATABASE) == before
    passed('正式库全部业务表与版本校验和保持不变')
    evidence = {'external_llm_calls': 0, 'scripted_model_only': True,
                'isolated_database': target, 'task_id': task['id'], 'checks': checks,
                'formal_checksum_unchanged': True, 'query_rows': result['row_count'],
                'model_checks': row['checks'], 'sql_statements': len(row['sql_log'])}
    (root/'acceptance.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(root/'acceptance.json'), flush=True)


if __name__ == '__main__':
    main()
