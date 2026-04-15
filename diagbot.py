# SPDX-License-Identifier: MIT
"""
DiagBot - MeshBBS diagnostic bot module.
Handles PING, ECHO, SELFTEST, LINKTEST, STATUS, QUEUES, PEERS, LASTSYNC commands.
"""
import os
import time
import uuid
import logging
import sqlite3
from typing import Optional, Dict, List

from .models import MessageType, MessageStatus, Priority, QueueStatus, QueueEntry

log = logging.getLogger("MeshBBS.diag")

# ─── Constants ───────────────────────────────────────────────────────────────

NODE_ID = "YOUR-NODE-ID"
BOT_VERSION = "v0.3"
RATE_LIMIT = 10          # max commands per minute
RATE_WINDOW = 60         # seconds
SYSOP_KEY = os.environ.get("MESHMAIL_SYSOP_KEY", "").strip()
_START_TIME = int(time.time())

# In-memory rate limit
_rate_limit: Dict[str, List[tuple]] = {}


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _check_rate_limit(from_pubkey: str) -> bool:
    now = time.time()
    entries = _rate_limit.get(from_pubkey, [])
    entries = [(ts, cmd) for ts, cmd in entries if now - ts < RATE_WINDOW]
    _rate_limit[from_pubkey] = entries
    if len(entries) >= RATE_LIMIT:
        return False
    entries.append((now, ""))
    _rate_limit[from_pubkey] = entries
    return False


def _is_sysop(from_pubkey: str) -> bool:
    """Check if caller is sysop. Deny by default if no SYSOP_KEY configured."""
    if not SYSOP_KEY:
        return False
    return from_pubkey == SYSOP_KEY


def _queue_size(db) -> int:
    try:
        row = db.conn.execute(
            "SELECT COUNT(*) FROM forward_queue WHERE status IN (0, 3) AND next_retry <= ?",
            (int(time.time()),)
        ).fetchone()
        return row[0] if row else 0
    except Exception:
        return -1


def _queue_sizes(db) -> dict:
    try:
        rows = db.conn.execute(
            "SELECT status, COUNT(*) FROM forward_queue GROUP BY status"
        ).fetchall()
        inbound, outbound, retry = 0, 0, 0
        for status, cnt in rows:
            s = QueueStatus(status)
            if s == QueueStatus.PENDING:
                outbound = cnt
            elif s == QueueStatus.SENDING:
                outbound += cnt
            elif s == QueueStatus.FAILED:
                retry = cnt
        total = sum(r[1] for r in rows if r[0] in (0, 3))
        return {"inbound": inbound, "outbound": outbound, "retry": retry, "total": total}
    except Exception:
        return {"inbound": 0, "outbound": 0, "retry": 0, "total": -1}


def _msg_count(db) -> int:
    try:
        row = db.conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        return row[0] if row else 0
    except Exception:
        return -1


def _peer_count(db) -> int:
    try:
        row = db.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def _last_sync_time(db) -> Optional[int]:
    try:
        row = db.conn.execute("SELECT MAX(last_success) FROM nodes").fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _peers_list(db) -> List[dict]:
    try:
        rows = db.conn.execute(
            "SELECT node_id, host, status, last_seen FROM nodes ORDER BY node_id"
        ).fetchall()
        return [{"node_id": r[0], "host": r[1], "status": r[2], "last_seen": r[3]} for r in rows]
    except Exception:
        return []


def _ensure_test_area(db) -> str:
    try:
        db.conn.execute(
            "INSERT OR IGNORE INTO bulletins "
            "(area_id, area_name, description, is_public, replication, created_by, created_at, msg_count, last_post) "
            "VALUES ('TEST', 'TEST', 'DiagBot sandbox area', 1, 1, 'diagbot', ?, 0, 0)",
            (int(time.time()),)
        )
        db.conn.commit()
    except Exception as e:
        log.warning("Could not create TEST area: %s", e)
    return "TEST"


# ─── Direct command implementations ──────────────────────────────────────────

def _maidenhead(lat: float, lon: float) -> str:
    """Convert lat/lon to maidenhead grid square (6 chars)."""
    import math
    # 18° longitude bands, 15° latitude bands
    lon_band = int((lon + 180) / 18) + 1
    lat_band = int((lat + 90) / 15) + 1
    lon_sq = chr(ord('A') + (lon_band - 1) % 18)
    lat_sq = chr(ord('A') + (lat_band - 1) % 18)
    # Subsquares: 5° lon x 2.5° lat
    lon_sub = int((lon + 180) % 18 / 5)
    lat_sub = int((lat + 90) % 15 / 2.5)
    # Final subsubsquares: 30' lon x 15' lat
    lon_subsub = int(((lon + 180) % 5) / 0.5)
    lat_subsub = int(((lat + 90) % 2.5) / 0.25)
    return f"{lon_sq}{lat_sq}{lon_sub}{lat_sub}{lon_subsub}{lat_subsub}"


def _cmd_ping_direct(from_name: str = None, grid: str = "", hops: int = 0, resp_s: int = 0) -> str:
    mention = f"@{from_name}" if from_name else ""
    parts = [mention, "PONG", f"📍{grid}" if grid else "COSWIG-SA", f"⏱{hops}hops"]
    if resp_s > 0:
        parts.append(f"{resp_s}s")
    return " ".join(p for p in parts if p)


def _cmd_echo_direct(text: str) -> str:
    return text


def _cmd_status_direct(from_pubkey: str, db) -> str:
    if not _is_sysop(from_pubkey):
        return "STATUS: SYSOP ONLY"
    uptime = int(time.time() - _START_TIME)
    ts = int(time.time())
    msgs = _msg_count(db)
    qs = _queue_sizes(db)
    peers = _peer_count(db)
    last_sync = _last_sync_time(db)
    mode = "UNRESTRICTED" if not SYSOP_KEY else "SYSOP"
    sync_str = str(last_sync) if last_sync else "never"
    return (
        "STATUS %s %s UP:%ds | MSG:%d | "
        "Q(in:%d out:%d retry:%d) | SYNC:%s | PEERS:%d | [%s]"
        % (NODE_ID, BOT_VERSION, uptime, msgs,
           qs["inbound"], qs["outbound"], qs["retry"], sync_str, peers, mode)
    )


def _cmd_queues_direct(from_pubkey: str, db) -> str:
    if not _is_sysop(from_pubkey):
        return "QUEUES: SYSOP ONLY"
    qs = _queue_sizes(db)
    return "QUEUES inbound:%d outbound:%d retry:%d total:%d" % (
        qs["inbound"], qs["outbound"], qs["retry"], qs["total"])


def _cmd_peers_direct(from_pubkey: str, db) -> str:
    if not _is_sysop(from_pubkey):
        return "PEERS: SYSOP ONLY"
    peers = _peers_list(db)
    if not peers:
        return "PEERS: none known"
    lines = ["PEERS:%d" % len(peers)]
    for p in peers[:10]:
        st = {1: 'ON', 2: 'OFF', 3: 'STALE'}.get(p["status"], "?")
        ls = p["last_seen"] or 0
        age = int(time.time()) - ls if ls else 0
        lines.append("  %s %s %s %ds ago" % (p["node_id"], p["host"], st, age))
    return "\r\n".join(lines)


def _cmd_lastsync_direct(from_pubkey: str, db) -> str:
    if not _is_sysop(from_pubkey):
        return "LASTSYNC: SYSOP ONLY"
    last_sync = _last_sync_time(db)
    if not last_sync:
        return "LASTSYNC: never"
    return "LASTSYNC: %d (%ds ago)" % (last_sync, int(time.time()) - last_sync)


def _cmd_bboard_direct(db) -> str:
    _ensure_test_area(db)
    try:
        rows = db.conn.execute(
            "SELECT msg_id, from_addr, subject, created_at FROM messages "
            "WHERE subject LIKE 'TEST:%' ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        if not rows:
            return "TEST bulletin: no messages"
        lines = ["TEST bulletin area:", "  %d messages" % len(rows)]
        for r in rows:
            age = int(time.time()) - (r[3] or 0)
            lines.append("  [%s] %s | %s | %ds ago" % (
                r[0][:8], r[1], r[2][:35], age))
        return "\r\n".join(lines)
    except Exception as e:
        return "TEST bulletin: error - %s" % e


def _cmd_selftest_direct(db, routing=None, mc_bridge=None) -> str:
    start = time.time() * 1000
    now = int(time.time())
    msg_id = "diag-selftest-%s" % uuid.uuid4().hex[:8]
    test_ok, msg_ok, q_ok, del_ok = False, False, False, False
    try:
        db.conn.execute(
            "INSERT INTO messages "
            "(msg_id, from_addr, to_addr, msg_type, subject, body, chunk, "
            "total_chunks, priority, ttl, hops, created_at, received_at, "
            "status, fwd_history, signature, thread_id, ref_msg_id, chunk_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, "diagbot@TEST", "diagbot@TEST", MessageType.BULLETIN.value,
             "TEST:DIAGBOT SELFTEST %d" % now, "selftest body",
             0, 0, Priority.NORMAL.value, 7, 0, now, 0,
             MessageStatus.LOCAL.value, "[]", None, "", "", "[]")
        )
        db.conn.commit()
        test_ok = True
        row = db.conn.execute(
            "SELECT msg_id FROM messages WHERE msg_id = ?", (msg_id,)
        ).fetchone()
        msg_ok = row is not None
        if hasattr(db, "queue_message"):
            qe = QueueEntry(
                msg_id=msg_id, dest_node="TESTNODE", chunk_idx=0,
                attempts=0, last_attempt=0, next_retry=now + 60,
                status=QueueStatus.PENDING, msg_type=MessageType.BULLETIN,
                priority=Priority.NORMAL,
            )
            q_ok = db.queue_message(qe)
        db.conn.execute("DELETE FROM messages WHERE msg_id = ?", (msg_id,))
        db.conn.execute("DELETE FROM forward_queue WHERE msg_id = ?", (msg_id,))
        db.conn.commit()
        del_ok = True
    except Exception as e:
        log.error("SELFTEST error: %s", e)
    elapsed = int(time.time() * 1000 - start)
    status = "OK" if (test_ok and msg_ok and q_ok and del_ok) else "FAIL"
    return (
        "SELFTEST %s | DB:%s | MSG:%s | Q:%s | DEL:%s | TIME:%dms"
        % (status,
           "OK" if test_ok else "FAIL",
           "OK" if msg_ok else "FAIL",
           "OK" if q_ok else "FAIL",
           "OK" if del_ok else "FAIL",
           elapsed)
    )


# ─── DiagBot class ────────────────────────────────────────────────────────────

class DiagBot:
    """Diagnostic bot for MeshBBS. Mostly deprecated - BBS commands use direct wrappers."""

    def __init__(self, db=None, routing=None, mc_bridge=None):
        self.db = db
        self.routing = routing
        self.mc_bridge = mc_bridge
        self._start_time = _START_TIME
        self._node_id = NODE_ID
        self._version = BOT_VERSION
        log.info("DiagBot initialized")

    def handle_dm(self, from_pubkey: str, text: str) -> Optional[str]:
        """Handle DM - returns response or None for BBS passthrough."""
        if not _check_rate_limit(from_pubkey):
            return "RATE LIMIT: max 10 commands/minute"
        cmd = text.strip()
        if cmd.startswith("!"):
            cmd = cmd[1:]
        if not cmd:
            return None
        log.info("[DIAG] from=%s cmd=%s", from_pubkey[:12], cmd[:60])
        upper = cmd.upper()
        if upper == "PING":
            return _cmd_ping_direct()
        if upper.startswith("ECHO "):
            return cmd[5:]
        if upper == "SELFTEST":
            return _cmd_selftest_direct(self.db, self.routing, self.mc_bridge)
        if upper.startswith("LINKTEST "):
            parts = cmd.split(maxsplit=1)
            target = parts[1] if len(parts) > 1 else ""
            return self._cmd_linktest(target)
        if upper == "STATUS":
            return _cmd_status_direct(from_pubkey, self.db)
        if upper == "QUEUES":
            return _cmd_queues_direct(from_pubkey, self.db)
        if upper == "PEERS":
            return _cmd_peers_direct(from_pubkey, self.db)
        if upper == "LASTSYNC":
            return _cmd_lastsync_direct(from_pubkey, self.db)
        if upper == "BBOARD" or upper == "TEST":
            return _cmd_bboard_direct(self.db)
        return None  # pass through to BBS

    def _cmd_linktest(self, target_node: str) -> str:
        if not target_node:
            return "LINKTEST: Usage: LINKTEST <node_id>"
        if not self.mc_bridge or not self.mc_bridge.is_connected():
            return "LINKTEST %s ERROR no_meshcore" % target_node
        start = time.time()
        probe_id = "lt-%s" % uuid.uuid4().hex[:8]
        probe_text = "LINKTEST_PROBE:%s:%d" % (probe_id, int(start))
        try:
            peer = self.db.get_node(target_node) if self.db else None
            dest = peer.host if peer else target_node
            self.mc_bridge.send_dm(dest, probe_text)
            elapsed_ms = int((time.time() - start) * 1000)
            return "LINKTEST %s OK %dms" % (target_node, elapsed_ms)
        except Exception as e:
            return "LINKTEST %s ERROR %s" % (target_node, e)