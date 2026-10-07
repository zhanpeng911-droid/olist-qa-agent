from __future__ import annotations

import os
import hashlib
import json
from urllib.parse import urlparse

import httpx


def settings():
    return {'configured': bool(os.getenv('DEEPSEEK_API_KEY')),
            'model': os.getenv('DEEPSEEK_MODEL', 'deepseek-chat'),
            'provider': 'DeepSeek / OpenAI-compatible',
            'result_sharing_default': False}


def compact_sql_history(messages):
    """Compress redundant successful SQL echoes, never the audit or failures.

    Assistant tool calls retain the exact submitted SQL. Once an INSERT has
    succeeded, its earlier reference need not be replayed on every model call.
    Full reference/SQL/error/quality history remains in the local task store.
    """
    decoded={}
    completed_tables=set()
    for index, message in enumerate(messages):
        if message.get('role') != 'tool':
            continue
        try:
            result=json.loads(message.get('content') or '')
        except (ValueError, TypeError):
            continue
        if not isinstance(result,dict):
            continue
        decoded[index]=result
        for item in result.get('executed', []):
            if isinstance(item,dict) and str(item.get('sql','')).lstrip().upper().startswith('INSERT '):
                completed_tables.add(item.get('table'))
    sent=[]
    for index, message in enumerate(messages):
        result=decoded.get(index)
        updated=None
        if result and isinstance(result.get('executed'),list):
            updated={**result,'executed':[
                {**{k:v for k,v in item.items() if k!='sql'},
                 'sql_sha256':hashlib.sha256(str(item.get('sql','')).encode()).hexdigest()}
                for item in result['executed'] if isinstance(item,dict)],
                'note':'执行成功回执已压缩；实际提交SQL仍保留在assistant工具参数及本地审计中'}
        elif result and result.get('table') in completed_tables and isinstance(result.get('statements'),list):
            updated={'table':result['table'],'reference_sha256':hashlib.sha256(message['content'].encode()).hexdigest(),
                     'note':'该参考对应表已成功INSERT；全文保留于本地审计。如需重建可重新调用reference_sql。'}
        sent.append({**message,'content':json.dumps(updated,ensure_ascii=False)} if updated is not None else message)
    return sent


class Model:
    def complete(self, messages, tools):
        key = os.getenv('DEEPSEEK_API_KEY', '')
        if not key:
            raise ValueError('尚未配置 DEEPSEEK_API_KEY，请在本地 .env 配置后重启服务')
        base = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').rstrip('/')
        url = urlparse(base)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('DEEPSEEK_BASE_URL 必须是无凭据参数的 HTTPS API 地址')
        sent=compact_sql_history(messages)
        original_chars=len(json.dumps(messages,ensure_ascii=False))
        sent_chars=len(json.dumps(sent,ensure_ascii=False))
        if sent_chars > 500_000:
            raise ValueError('模型任务上下文超过本机字符预算，请分拆任务；未自动发布正式库')
        try:
            with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
                response = client.post(base + '/chat/completions',
                                       headers={'Authorization': 'Bearer ' + key},
                                       json={'model': settings()['model'], 'messages': sent,
                                             'tools': tools, 'tool_choice': 'auto',
                                             'max_tokens': 8192, 'stream': False})
            if not response.is_success:
                raise ValueError(f'模型调用未完成（HTTP {response.status_code}），请检查模型名称、账户额度与 API 配置')
            result = response.json()
            message = result['choices'][0]['message']
            # Preserve provider-required reasoning_content in round trips, but never
            # expose private reasoning as user-facing execution explanations.
            usage={**result.get('usage', {}),'context_original_chars':original_chars,'context_sent_chars':sent_chars}
            return {k: message[k] for k in ('role', 'content', 'tool_calls', 'reasoning_content') if k in message}, usage
        except (httpx.HTTPError, KeyError, IndexError, TypeError):
            raise ValueError('模型连接或响应解析失败；已执行工具保留审计记录，未自动发布正式数据') from None
