"""Message and User persistence with SQLite"""
import sqlite3
import json
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
from dataclasses import asdict

from .models import (
    MeshMessage, MailboxUser, BulletinBoard, PeerNode,
    QueueEntry, InboxEntry, MessageType, Priority,
    MessageStatus, QueueStatus, NodeStatus, ReplicationLevel,
    parse_address, format_address
)


class Database:
    """SQLite persistence layer for MeshBBS"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Create tables"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                msg_id TEXT PRIMARY KEY,
                from_addr TEXT NOT NULL,
                to_addr TEXT NOT NULL,
                msg_type INTEGER NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                chunk INTEGER DEFAULT 0,
                total_chunks INTEGER DEFAULT 0,
                priority INTEGER DEFAULT 2,
                ttl INTEGER DEFAULT 7,
                hops INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                received_at INTEGER DEFAULT 0,
                status INTEGER DEFAULT 0,
                fwd_history TEXT DEFAULT '[]',
                signature TEXT,
                thread_id TEXT DEFAULT '',
                ref_msg_id TEXT DEFAULT '',
                chunk_ids TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                is_sysop INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                last_login INTEGER DEFAULT 0,
                msg_sent INTEGER DEFAULT 0,
                msg_received INTEGER DEFAULT 0,
                read_msg_ids TEXT DEFAULT '[]',
                last_read_check INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS inbox (
                msg_id TEXT PRIMARY KEY,
                to_user TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                is_deleted INTEGER DEFAULT 0,
                received_at INTEGER NOT NULL,
                read_at INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS bulletins (
                area_id TEXT PRIMARY KEY,
                area_name TEXT NOT NULL,
                description TEXT DEFAULT '',
                is_public INTEGER DEFAULT 1,
                replication INTEGER DEFAULT 1,
                created_by TEXT DEFAULT '',
                created_at INTEGER NOT NULL,
                msg_count INTEGER DEFAULT 0,
                last_post INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS bulletin_msgs (
                msg_id TEXT PRIMARY KEY,
                area_id TEXT NOT NULL,
                from_addr TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (msg_id) REFERENCES messages(msg_id)
            );

            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                host TEXT NOT NULL,
                tcp_port INTEGER DEFAULT 5000,
                last_seen INTEGER DEFAULT 0,
                last_poll INTEGER DEFAULT 0,
                status INTEGER DEFAULT 2,
                msg_seq INTEGER DEFAULT 0,
                bulletins TEXT DEFAULT '[]',
                capabilities TEXT DEFAULT '[]',
                messages_sent INTEGER DEFAULT 0,
                messages_received INTEGER DEFAULT 0,
                last_success INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS forward_queue (
                msg_id TEXT NOT NULL,
                dest_node TEXT NOT NULL,
                chunk_idx INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                last_attempt INTEGER DEFAULT 0,
                next_retry INTEGER DEFAULT 0,
                status INTEGER DEFAULT 0,
                msg_type INTEGER DEFAULT 1,
                priority INTEGER DEFAULT 2,
                last_error TEXT DEFAULT '',
                PRIMARY KEY (msg_id, dest_node, chunk_idx)
            );

            CREATE TABLE IF NOT EXISTS seen_msg_ids (
                msg_node_key TEXT PRIMARY KEY,
                seen_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_to_addr ON messages(to_addr);
            CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(msg_type);
            CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
            CREATE INDEX IF NOT EXISTS idx_inbox_user ON inbox(to_user);
            CREATE INDEX IF NOT EXISTS idx_forward_queue_retry ON forward_queue(next_retry);
        """)
        self.conn.commit()

    # ─── Messages ───────────────────────────────────────────────

    def save_message(self, msg: MeshMessage) -> bool:
        """Save or update a message"""
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO messages
                (msg_id, from_addr, to_addr, msg_type, subject, body, chunk,
                 total_chunks, priority, ttl, hops, created_at, received_at,
                 status, fwd_history, signature, thread_id, ref_msg_id, chunk_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg.msg_id, msg.from_addr, msg.to_addr, msg.msg_type.value,
                msg.subject, msg.body, msg.chunk, msg.total_chunks,
                msg.priority.value, msg.ttl, msg.hops, msg.created_at,
                msg.received_at, msg.status.value,
                json.dumps(msg.fwd_history),
                msg.signature.hex() if msg.signature else None,
                msg.thread_id, msg.ref_msg_id,
                json.dumps(msg.chunk_ids)
            ))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"save_message error: {e}")
            return False

    def get_message(self, msg_id: str) -> Optional[MeshMessage]:
        """Get a single message by ID"""
        row = self.conn.execute(
            "SELECT * FROM messages WHERE msg_id = ?", (msg_id,)
        ).fetchone()
        return self._row_to_message(row) if row else None

    def get_messages_to_addr(self, addr: str, include_deleted: bool = False) -> List[MeshMessage]:
        """Get all messages for an address"""
        sql = "SELECT * FROM messages WHERE to_addr = ?"
        if not include_deleted:
            sql += " AND status != ?"
        sql += " ORDER BY created_at DESC"
        params = (addr, MessageStatus.EXPIRED.value) if not include_deleted else (addr,)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_message(r) for r in rows]

    def delete_message(self, msg_id: str) -> bool:
        """Mark message as expired"""
        self.conn.execute(
            "UPDATE messages SET status = ? WHERE msg_id = ?",
            (MessageStatus.EXPIRED.value, msg_id)
        )
        self.conn.commit()
        return True

    def _row_to_message(self, row: sqlite3.Row) -> MeshMessage:
        return MeshMessage(
            msg_id=row['msg_id'],
            from_addr=row['from_addr'],
            to_addr=row['to_addr'],
            msg_type=MessageType(row['msg_type']),
            subject=row['subject'],
            body=row['body'],
            chunk=row['chunk'],
            total_chunks=row['total_chunks'],
            priority=Priority(row['priority']),
            ttl=row['ttl'],
            hops=row['hops'],
            created_at=row['created_at'],
            received_at=row['received_at'],
            status=MessageStatus(row['status']),
            fwd_history=json.loads(row['fwd_history'] or '[]'),
            signature=bytes.fromhex(row['signature']) if row['signature'] else None,
            thread_id=row['thread_id'] or '',
            ref_msg_id=row['ref_msg_id'] or '',
            chunk_ids=json.loads(row['chunk_ids'] or '[]'),
        )

    # ─── Users ────────────────────────────────────────────────

    def save_user(self, user: MailboxUser) -> bool:
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO users
                (user_id, password_hash, display_name, is_sysop, is_blocked,
                 created_at, last_login, msg_sent, msg_received,
                 read_msg_ids, last_read_check)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user.user_id, user.password_hash, user.display_name,
                int(user.is_sysop), int(user.is_blocked),
                user.created_at, user.last_login, user.msg_sent, user.msg_received,
                json.dumps(list(user.read_msg_ids)), user.last_read_check
            ))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"save_user error: {e}")
            return False

    def get_user(self, user_id: str) -> Optional[MailboxUser]:
        row = self.conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def get_users(self) -> List[MailboxUser]:
        rows = self.conn.execute("SELECT * FROM users").fetchall()
        return [self._row_to_user(r) for r in rows]

    def _row_to_user(self, row: sqlite3.Row) -> MailboxUser:
        return MailboxUser(
            user_id=row['user_id'],
            password_hash=row['password_hash'],
            display_name=row['display_name'],
            is_sysop=bool(row['is_sysop']),
            is_blocked=bool(row['is_blocked']),
            created_at=row['created_at'],
            last_login=row['last_login'],
            msg_sent=row['msg_sent'],
            msg_received=row['msg_received'],
            read_msg_ids=set(json.loads(row['read_msg_ids'] or '[]')),
            last_read_check=row['last_read_check'],
        )

    # ─── Inbox ──────────────────────────────────────────────

    def save_inbox_entry(self, entry: InboxEntry) -> bool:
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO inbox
                (msg_id, to_user, is_read, is_deleted, received_at, read_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                entry.msg_id, entry.to_user, int(entry.is_read),
                int(entry.is_deleted), entry.received_at, entry.read_at
            ))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"save_inbox_entry error: {e}")
            return False

    def get_inbox(self, user: str, include_read: bool = False, node_id: str = "") -> List[tuple]:
        """Get inbox entries for user with message details.
        
        If user is a pubkey prefix (8 hex chars), look up by to_addr matching user@node_id.
        Falls node_id angegeben, suche nach to_addr LIKE '%@node_id' (alle lokalen User).
        """
        # Try direct match first
        sql = """
            SELECT i.*, m.subject, m.from_addr, m.created_at
            FROM inbox i
            JOIN messages m ON i.msg_id = m.msg_id
            WHERE i.to_user = ? AND i.is_deleted = 0
        """
        if not include_read:
            sql += " AND i.is_read = 0"
        sql += " ORDER BY i.received_at DESC"
        rows = self.conn.execute(sql, (user,)).fetchall()
        result = [(dict(r)) for r in rows]
        
        # If no results and node_id given, try matching to_addr by node_id
        if not result and node_id:
            sql2 = """
                SELECT i.*, m.subject, m.from_addr, m.created_at
                FROM inbox i
                JOIN messages m ON i.msg_id = m.msg_id
                WHERE m.to_addr LIKE ? AND i.is_deleted = 0
            """
            if not include_read:
                sql2 += " AND i.is_read = 0"
            sql2 += " ORDER BY i.received_at DESC"
            rows = self.conn.execute(sql2, (f'%@{node_id}',)).fetchall()
            result = [(dict(r)) for r in rows]
        
        return result

    def mark_read(self, msg_id: str, user: str) -> bool:
        self.conn.execute(
            "UPDATE inbox SET is_read = 1, read_at = ? WHERE msg_id = ? AND to_user = ?",
            (int(time.time()), msg_id, user)
        )
        self.conn.commit()
        return True

    # ─── Nodes ───────────────────────────────────────────────

    def save_node(self, node: PeerNode) -> bool:
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO nodes
                (node_id, host, tcp_port, last_seen, last_poll, status,
                 msg_seq, bulletins, capabilities,
                 messages_sent, messages_received, last_success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node.node_id, node.host, node.tcp_port,
                node.last_seen, node.last_poll, node.status.value,
                node.msg_seq, json.dumps(list(node.bulletins)),
                json.dumps(node.capabilities),
                node.messages_sent, node.messages_received, node.last_success
            ))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"save_node error: {e}")
            return False

    def get_node(self, node_id: str) -> Optional[PeerNode]:
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
        return self._row_to_node(row) if row else None

    def get_all_nodes(self) -> List[PeerNode]:
        rows = self.conn.execute("SELECT * FROM nodes").fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_online_nodes(self) -> List[PeerNode]:
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE status = ?", (NodeStatus.ONLINE.value,)
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def _row_to_node(self, row: sqlite3.Row) -> PeerNode:
        return PeerNode(
            node_id=row['node_id'],
            host=row['host'],
            tcp_port=row['tcp_port'],
            last_seen=row['last_seen'],
            last_poll=row['last_poll'],
            status=NodeStatus(row['status']),
            msg_seq=row['msg_seq'],
            bulletins=set(json.loads(row['bulletins'] or '[]')),
            capabilities=json.loads(row['capabilities'] or '[]'),
            messages_sent=row['messages_sent'],
            messages_received=row['messages_received'],
            last_success=row['last_success'],
        )

    # ─── Forward Queue ────────────────────────────────────────

    def queue_message(self, entry: QueueEntry) -> bool:
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO forward_queue
                (msg_id, dest_node, chunk_idx, attempts, last_attempt,
                 next_retry, status, msg_type, priority, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.msg_id, entry.dest_node, entry.chunk_idx,
                entry.attempts, entry.last_attempt, entry.next_retry,
                entry.status.value, entry.msg_type.value,
                entry.priority.value, entry.last_error
            ))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"queue_message error: {e}")
            return False

    def get_pending_queue(self, limit: int = 50) -> List[QueueEntry]:
        rows = self.conn.execute("""
            SELECT * FROM forward_queue
            WHERE status IN (0, 3) AND next_retry <= ?
            ORDER BY priority ASC, next_retry ASC
            LIMIT ?
        """, (int(time.time()), limit)).fetchall()
        return [self._row_to_queue(r) for r in rows]

    def remove_from_queue(self, msg_id: str, dest_node: str) -> bool:
        self.conn.execute(
            "DELETE FROM forward_queue WHERE msg_id = ? AND dest_node = ?",
            (msg_id, dest_node)
        )
        self.conn.commit()
        return True

    def _row_to_queue(self, row: sqlite3.Row) -> QueueEntry:
        return QueueEntry(
            msg_id=row['msg_id'],
            dest_node=row['dest_node'],
            chunk_idx=row['chunk_idx'],
            attempts=row['attempts'],
            last_attempt=row['last_attempt'],
            next_retry=row['next_retry'],
            status=QueueStatus(row['status']),
            msg_type=MessageType(row['msg_type']),
            priority=Priority(row['priority']),
            last_error=row['last_error'] or '',
        )

    # ─── Seen IDs (Dedup) ───────────────────────────────────

    def mark_seen(self, msg_node_key: str) -> bool:
        """Mark msg+dest as seen, for dedup"""
        self.conn.execute(
            "INSERT OR REPLACE INTO seen_msg_ids (msg_node_key, seen_at) VALUES (?, ?)",
            (msg_node_key, int(time.time()))
        )
        self.conn.commit()
        return True

    def is_seen(self, msg_node_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen_msg_ids WHERE msg_node_key = ?", (msg_node_key,)
        ).fetchone()
        return row is not None

    # ─── Bulletin Boards ─────────────────────────────────────

    def save_bulletin_area(self, board: BulletinBoard) -> bool:
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO bulletins
                (area_id, area_name, description, is_public, replication,
                 created_by, created_at, msg_count, last_post)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                board.area_id, board.area_name, board.description,
                int(board.is_public), board.replication.value,
                board.created_by, board.created_at,
                board.msg_count, board.last_post
            ))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"save_bulletin_area error: {e}")
            return False

    def get_bulletin_areas(self) -> List[BulletinBoard]:
        rows = self.conn.execute("SELECT * FROM bulletins ORDER BY area_id").fetchall()
        return [self._row_to_board(r) for r in rows]

    def _row_to_board(self, row: sqlite3.Row) -> BulletinBoard:
        return BulletinBoard(
            area_id=row['area_id'],
            area_name=row['area_name'],
            description=row['description'] or '',
            is_public=bool(row['is_public']),
            replication=ReplicationLevel(row['replication']),
            created_by=row['created_by'] or '',
            created_at=row['created_at'],
            msg_count=row['msg_count'],
            last_post=row['last_post'],
        )

    # ─── Utility ────────────────────────────────────────────

    def close(self):
        self.conn.close()

    def vacuum(self):
        """Cleanup database"""
        self.conn.execute("VACUUM")
        self.conn.commit()
