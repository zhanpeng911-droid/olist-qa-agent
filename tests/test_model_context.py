import copy
import json

from agent_core.model import compact_sql_history


def result(value):
    return {'role':'tool','tool_call_id':'test','content':json.dumps(value)}


def test_completed_sql_echoes_are_compact_but_exact_submissions_and_audit_remain():
    sql='INSERT INTO stg_order_payments SELECT * FROM raw_order_payments'
    messages=[{'role':'system','content':'完整skill'},
              result({'table':'stg_order_payments','statements':['CREATE TABLE ...',sql]}),
              {'role':'assistant','tool_calls':[{'function':{'name':'execute_sql','arguments':json.dumps({'statements':[sql]})}}]},
              result({'executed':[{'table':'stg_order_payments','sql':sql,'affected_rows':60}]}),
              result({'checks':[{'status':'WARNING','actual':1}]})]
    original=copy.deepcopy(messages)
    sent=compact_sql_history(messages)
    assert messages==original
    assert sent[0]==original[0] and sent[2]==original[2] and sent[4]==original[4]
    assert 'statements' not in json.loads(sent[1]['content'])
    receipt=json.loads(sent[3]['content'])['executed'][0]
    assert receipt['affected_rows']==60 and len(receipt['sql_sha256'])==64 and 'sql' not in receipt


def test_references_are_not_compacted_before_successful_insert_or_after_error_only():
    messages=[result({'table':'stg_order_payments','statements':['CREATE TABLE ...']}),
              result({'executed':[{'table':'stg_order_payments','sql':'CREATE TABLE stg_order_payments (...)','affected_rows':0}]}),
              result({'error':'MySQL错误 1054'})]
    sent=compact_sql_history(messages)
    assert sent[0]==messages[0] and sent[-1]==messages[-1]


def test_data_rows_and_failed_sql_are_never_removed_by_sql_compaction():
    messages=[result({'rows':[{'text':'private'}],'sql':'SELECT * FROM raw_orders'}),
              result({'error':'SQL failed','sql':'INSERT INTO t SELECT invalid'}),
              {'role':'tool','content':'not-json'}]
    assert compact_sql_history(messages)==messages
