"""偏难怪题批量探针：直接调后端 /api/chat（SSE），输出每题 intent/execution/answer。

用法：uv run python tests/edge_case_probe.py "题1" "题2" ...
"""
import json
import sys

import requests

BASE = "http://127.0.0.1:8000/api/chat"


def chat(q: str, timeout: int = 150):
    events = {}
    with requests.post(BASE, json={"question": q}, stream=True, timeout=timeout) as r:
        ev = None
        for line in r.iter_lines():
            if line.startswith(b"event:"):
                ev = line[6:].decode().strip()
            elif line.startswith(b"data:") and ev:
                try:
                    events[ev] = json.loads(line[5:])
                except json.JSONDecodeError:
                    pass
    return events


def main():
    questions = sys.argv[1:] or [
        "为什么订单量下降了",
        "退款率高的原因是什么",
    ]
    for q in questions:
        try:
            ev = chat(q)
        except Exception as e:
            print(f"Q: {q}\n  ERROR: {e}\n")
            continue
        intent = ev.get("intent", {}).get("intent", "?")
        res = ev.get("result", {})
        execution = res.get("execution_mode")
        answer = res.get("answer") or ev.get("answer", {}).get("answer", "")
        ok = res.get("ok", True)
        print(f"Q: {q}")
        print(f"  intent={intent}  execution={execution}  ok={ok}")
        print(f"  answer: {str(answer)[:200]}")
        if res.get("incomplete"):
            print(f"  [incomplete={res.get('incomplete')}]")
        if res.get("error"):
            print(f"  [error: {res.get('error')}]")
        if ev.get("error"):
            print(f"  [stream error: {ev['error']}]")
        print()


if __name__ == "__main__":
    main()
