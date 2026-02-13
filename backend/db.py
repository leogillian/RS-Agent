"""SQLite persistence for RS-Agent conversations and messages."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.config import settings


@contextmanager
def get_conn() -> Iterable[sqlite3.Connection]:
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they do not exist."""
    db_file = Path(settings.db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                intent TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                payload_type TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
            """
        )


def create_conversation(conv_id: str, intent: str, status: str = "active") -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO conversations (
                id,
                intent,
                status,
                created_at,
                updated_at
            )
            VALUES (
                ?,
                ?,
                ?,
                datetime('now', 'localtime'),
                datetime('now', 'localtime')
            )
            """,
            (conv_id, intent, status),
        )
    # 每次创建新会话后，清理只保留最近 10 条记录
    trim_old_conversations(max_count=10)


def update_conversation_status(conv_id: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE conversations
            SET status = ?, updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (status, conv_id),
        )


def add_message(
    conv_id: str,
    role: str,
    payload_type: str,
    content: str,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO messages (conversation_id, role, payload_type, content, created_at)
            VALUES (?, ?, ?, ?, datetime('now', 'localtime'))
            """,
            (conv_id, role, payload_type, content),
        )


def list_conversations(limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT
                c.id,
                c.intent,
                c.status,
                c.created_at,
                c.updated_at,
                (
                    SELECT m.content
                    FROM messages m
                    WHERE m.conversation_id = c.id AND m.role = 'user'
                    ORDER BY m.id ASC
                    LIMIT 1
                ) AS first_user_text
            FROM conversations c
            ORDER BY c.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_conversation(conv_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, intent, status, created_at, updated_at
            FROM conversations
            WHERE id = ?
            """,
            (conv_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        conv = dict(row)
        mcur = conn.execute(
            """
            SELECT role, payload_type, content, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conv_id,),
        )
        conv["messages"] = [dict(r) for r in mcur.fetchall()]
        return conv


def trim_old_conversations(max_count: int = 10) -> None:
    """保留最近 max_count 条会话，删除更早的会话及其消息。"""
    if max_count <= 0:
        return
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id
            FROM conversations
            WHERE id NOT IN (
                SELECT id
                FROM conversations
                ORDER BY created_at DESC
                LIMIT ?
            )
            """,
            (max_count,),
        )
        old_ids = [row["id"] for row in cur.fetchall()]
        if not old_ids:
            return
        conn.executemany(
            "DELETE FROM messages WHERE conversation_id = ?",
            [(cid,) for cid in old_ids],
        )
        conn.executemany(
            "DELETE FROM conversations WHERE id = ?",
            [(cid,) for cid in old_ids],
        )

