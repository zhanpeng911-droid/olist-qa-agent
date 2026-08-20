"""会话历史数据库存储：chat_session / chat_message 两表（存现有 olist_ecommerce 库）。

- 结果（result JSON）整份入库：query 等小结果可直接还原表格；attribution 大结果 405KB
  也能完整存（DB TEXT 上限 64KB 不够，用 LONGTEXT），不再受 localStorage ~5MB 总量限制。
- 首次调用自动建表（幂等）。连接复用 main.py 的 DB_* 环境变量。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager

import pymysql

_DDL = """
CREATE TABLE IF NOT EXISTS chat_session (
  id         VARCHAR(40) PRIMARY KEY,
  title      VARCHAR(120) NOT NULL DEFAULT '新对话',
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS chat_message (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(40) NOT NULL,
  role       VARCHAR(16) NOT NULL,
  intent     VARCHAR(32) DEFAULT NULL,
  text       TEXT,
  summary    TEXT,
  result     LONGTEXT,
  error      TEXT,
  created_at BIGINT NOT NULL,
  KEY idx_session (session_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


class SessionStore:
    """基于 MySQL 的会话历史存取。

    每个方法独立开/关连接（用完即关），避免全局单例共享连接在并发请求下
    出现"一个请求关闭、另一个请求仍在用已失效连接"导致的 struct.error。
    """

    def __init__(self) -> None:
        self._schema_ok = False

    # ---------- 连接 ----------
    @contextmanager
    def _cursor(self):
        conn = pymysql.connect(
            host=os.environ.get("DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("DB_PORT", "3306")),
            user=os.environ.get("DB_USER", "root"),
            password=os.environ.get("DB_PASSWORD", ""),
            database=os.environ.get("DB_NAME", "olist_ecommerce"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
        try:
            if not self._schema_ok:
                self._ensure_schema(conn)
                self._schema_ok = True
            with conn.cursor() as cur:
                yield cur
        finally:
            conn.close()

    def _ensure_schema(self, conn) -> None:
        with conn.cursor() as cur:
            for stmt in _DDL.split(";"):
                if stmt.strip():
                    cur.execute(stmt)

    def close(self) -> None:
        """兼容旧接口：连接已在每次调用后关闭，无需额外操作。"""

    # ---------- 会话 ----------
    def list_sessions(self) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, title, created_at, updated_at, "
                "(SELECT COUNT(*) FROM chat_message m WHERE m.session_id = s.id) AS message_count "
                "FROM chat_session s ORDER BY updated_at DESC"
            )
            rows = cur.fetchall()
        out = []
        for r in rows:
            r["created_at"] = int(r["created_at"])
            r["updated_at"] = int(r["updated_at"])
            r["message_count"] = int(r["message_count"])
            out.append(r)
        return out

    def create_session(self, title: str = "新对话") -> dict:
        now = int(time.time() * 1000)
        sid = uuid.uuid4().hex[:12]
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO chat_session (id, title, created_at, updated_at) VALUES (%s, %s, %s, %s)",
                (sid, title[:120], now, now),
            )
        return {"id": sid, "title": title, "created_at": now, "updated_at": now, "message_count": 0}

    def rename_session(self, sid: str, title: str) -> bool:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE chat_session SET title = %s, updated_at = %s WHERE id = %s",
                (title[:120], int(time.time() * 1000), sid),
            )
            return cur.rowcount > 0

    def delete_session(self, sid: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM chat_message WHERE session_id = %s", (sid,))
            cur.execute("DELETE FROM chat_session WHERE id = %s", (sid,))
            return True

    # ---------- 消息 ----------
    def get_messages(self, sid: str) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT role, intent, text, summary, result, error, created_at "
                "FROM chat_message WHERE session_id = %s ORDER BY id",
                (sid,),
            )
            rows = cur.fetchall()
        out = []
        for r in rows:
            item = {
                "role": r["role"],
                "intent": r["intent"],
                "text": r["text"],
                "summary": r["summary"],
                "error": r["error"],
            }
            try:
                item["result"] = json.loads(r["result"]) if r["result"] else None
            except (json.JSONDecodeError, TypeError):
                item["result"] = None
            out.append(item)
        return out

    def save_messages(self, sid: str, messages: list[dict]) -> None:
        """整批覆盖该会话消息：先清后插，保证与前端状态一致（前端是完整快照）。"""
        now = int(time.time() * 1000)
        with self._cursor() as cur:
            cur.execute("DELETE FROM chat_message WHERE session_id = %s", (sid,))
            for m in messages:
                role = m.get("role", "assistant")
                result = m.get("result")
                cur.execute(
                    "INSERT INTO chat_message (session_id, role, intent, text, summary, result, error, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        sid, role,
                        m.get("intent"),
                        m.get("text"),
                        m.get("summary"),
                        json.dumps(result, ensure_ascii=False) if result is not None else None,
                        m.get("error"),
                        now,
                    ),
                )
            cur.execute("UPDATE chat_session SET updated_at = %s WHERE id = %s", (now, sid))

    def append_messages(self, sid: str, messages: list[dict]) -> None:
        """追加消息（不删除已有）。

        用于流式对话结束后增量落库本轮新增消息，避免「先删后插」全量覆盖在
        切换会话 / 并发下互相覆盖。
        """
        if not messages:
            return
        now = int(time.time() * 1000)
        with self._cursor() as cur:
            for m in messages:
                role = m.get("role", "assistant")
                result = m.get("result")
                cur.execute(
                    "INSERT INTO chat_message (session_id, role, intent, text, summary, result, error, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        sid, role,
                        m.get("intent"),
                        m.get("text"),
                        m.get("summary"),
                        json.dumps(result, ensure_ascii=False) if result is not None else None,
                        m.get("error"),
                        now,
                    ),
                )
            cur.execute("UPDATE chat_session SET updated_at = %s WHERE id = %s", (now, sid))
