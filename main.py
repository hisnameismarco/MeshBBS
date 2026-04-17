#!/usr/bin/env python3
"""MeshBBS Entry Point — MeshCore BBS via TCP Bridge"""
import asyncio
import sys
import signal
import logging
import time
import uuid
import re
from typing import Optional
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from meshmail.config import MeshBBSConfig
from meshmail.store import Database
from meshmail.routing import RoutingEngine
from meshmail.sync import SyncEngine
from meshmail.meshcore_if import MeshCoreBridge
from meshmail.models import (
    MessageType,
    MessageStatus,
    MailboxUser,
    parse_address,
    Priority,
    MeshMessage,
    InboxEntry,
    NodeStatus,
    PeerNode,
)
from meshmail.diagbot import DiagBot, _cmd_ping_direct, _cmd_echo_direct, _cmd_selftest_direct, _cmd_status_direct, _cmd_queues_direct, _cmd_peers_direct, _cmd_lastsync_direct, _cmd_bboard_direct, _grid_from_config

log = logging.getLogger("MeshBBS")


# ─── BBS Command Registry ─────────────────────────────────────────────────────

BBS_COMMANDS = {}
_VALID_USER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_VALID_NODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_SUBJECT_LEN = 40
_MAX_BODY_LEN = 512
_MAX_DM_TEXT_LEN = 1024
_MAX_CHANNEL_CMD_LEN = 128
_INBOX_PAGE_SIZE = 10
_VALID_DISPLAY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_REF_PREFIX_RE = re.compile(r"^(?:ref:|>>)\s*([A-Za-z0-9-]{8,64})\s*(?:\r?\n|$)", re.IGNORECASE)
_DISCOVERY_RE = re.compile(r"^DISCOVER\s+([A-Za-z0-9._-]{1,64})(?:\s+(\d+))?$", re.IGNORECASE)
_PRESENCE_RE = re.compile(r"^PRESENCE\s+([A-Za-z0-9._-]{1,64})(?:\s+(\d+))?$", re.IGNORECASE)
_FINGER_RE = re.compile(r"^FINGER\s+([A-Za-z0-9._-]{1,64})\b", re.IGNORECASE)
_ERR = {
    "usage_msg_simple": "ERROR usage: !MSG @<username>[@<node>] <subject> [text]",
    "usage_msg_extended": "ERROR usage: !MSG @<username>[@<node>] | <subject> | [text]",
    "usage_delete": "ERROR usage: !DELETE <number>",
    "db_unavailable": "ERROR database unavailable",
    "sender_invalid": "ERROR sender identity invalid",
    "dm_invalid_payload": "ERROR invalid DM payload",
    "dm_too_long": f"ERROR DM too long (max {_MAX_DM_TEXT_LEN} chars)",
    "invalid_username": "ERROR invalid username",
    "invalid_destination_node": "ERROR invalid destination node",
    "invalid_subject_empty": "ERROR subject must not be empty",
    "invalid_subject_too_long": f"ERROR subject exceeds {_MAX_SUBJECT_LEN} chars",
    "invalid_subject_chars": "ERROR subject contains unsupported control characters",
    "invalid_body_too_long": f"ERROR body exceeds {_MAX_BODY_LEN} chars",
    "invalid_body_chars": "ERROR body contains unsupported control characters",
    "msg_save_failed": "ERROR message persistence failed",
    "inbox_save_failed": "ERROR inbox persistence failed",
    "msg_save_internal": "ERROR internal failure while saving message",
    "inbox_item_missing": "ERROR inbox item not found",
    "inbox_item_malformed": "ERROR malformed inbox entry",
    "delete_failed": "ERROR could not delete message",
    "usage_thread": "ERROR usage: !THREAD <inbox-number|message-id>",
    "thread_not_found": "ERROR thread not found",
    "cmd_internal_failure": "ERROR internal command failure",
}

def bbs_command(name):
    def decorator(func):
        BBS_COMMANDS[name.upper()] = func
        return func
    return decorator


def _with_crlf(text: str) -> str:
    return f"{text}.\r\n"


def _err(key: str) -> str:
    return _with_crlf(_ERR[key])


def _config_node_id(config: MeshBBSConfig) -> str:
    return str(getattr(config, "node_id", "") or MeshBBSConfig.DEFAULTS["node_id"])


def _contains_control_chars(value: str) -> bool:
    return any((ord(ch) < 32 and ch not in ("\n", "\r", "\t")) for ch in value)


def _sender_username(from_pk: str) -> str:
    raw = (from_pk or "").strip().lower()
    if len(raw) < 8:
        return ""
    sender = raw[:8]
    if not all(c in "0123456789abcdef" for c in sender):
        return ""
    return sender


def _parse_msg_args(args: str, default_node_id: str):
    payload = args.strip()
    if not payload.startswith("@"):
        raise ValueError(_ERR["usage_msg_simple"])

    payload = payload[1:].strip()
    if not payload:
        raise ValueError(_ERR["usage_msg_simple"])

    # New extended format: !MSG @user[@node] | subject | body text
    if "|" in payload:
        parts = [p.strip() for p in payload.split("|", 2)]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError(_ERR["usage_msg_extended"])
        target = parts[0]
        subject = parts[1]
        body = parts[2] if len(parts) > 2 else ""
    else:
        first = payload.split(maxsplit=1)
        if len(first) < 2:
            raise ValueError(_ERR["usage_msg_simple"])
        target = first[0].strip()
        rest = first[1].strip()
        if not rest:
            raise ValueError(_ERR["usage_msg_simple"])
        second = rest.split(maxsplit=1)
        subject = second[0]
        body = second[1] if len(second) > 1 else ""

    if "@" in target:
        to_user, to_node = target.split("@", 1)
        to_node = to_node.strip()
    else:
        to_user, to_node = target, default_node_id

    to_user = to_user.strip().lower()
    if not _VALID_USER_RE.fullmatch(to_user):
        raise ValueError(_ERR["invalid_username"])
    if not _VALID_NODE_RE.fullmatch(to_node):
        raise ValueError(_ERR["invalid_destination_node"])

    clean_subject = subject.strip()
    if not clean_subject:
        raise ValueError(_ERR["invalid_subject_empty"])
    if len(clean_subject) > _MAX_SUBJECT_LEN:
        raise ValueError(_ERR["invalid_subject_too_long"])
    if _contains_control_chars(clean_subject):
        raise ValueError(_ERR["invalid_subject_chars"])

    clean_body = body.strip()
    if len(clean_body) > _MAX_BODY_LEN:
        raise ValueError(_ERR["invalid_body_too_long"])
    if _contains_control_chars(clean_body):
        raise ValueError(_ERR["invalid_body_chars"])
    return to_user, to_node, clean_subject, clean_body


def _sanitize_display_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    return raw if _VALID_DISPLAY_NAME_RE.fullmatch(raw) else ""


def _ping_grid(config: MeshBBSConfig) -> str:
    return _grid_from_config(config)


def _presence_counts(db: Database, timeout_s: int):
    timeout_s = max(1, int(timeout_s))
    now = int(time.time())
    total = 0
    online = 0
    changed = 0
    for node in db.get_all_nodes():
        total += 1
        age = now - int(node.last_seen or 0)
        target = NodeStatus.ONLINE if node.last_seen and age <= timeout_s else NodeStatus.OFFLINE
        if target == NodeStatus.ONLINE:
            online += 1
        if node.status != target:
            node.status = target
            db.save_node(node)
            changed += 1
    return online, total, changed


def _parse_inbox_args(args: str):
    page = 1
    include_read = True
    sort_desc = True
    for tok in args.split():
        raw = tok.strip().lower()
        if not raw:
            continue
        if raw.isdigit():
            page = max(1, int(raw))
            continue
        if raw.startswith("page=") and raw[5:].isdigit():
            page = max(1, int(raw[5:]))
            continue
        if raw in {"all", "read"}:
            include_read = True
            continue
        if raw in {"unread", "new"}:
            include_read = False
            continue
        if raw in {"asc", "oldest"}:
            sort_desc = False
            continue
        if raw in {"desc", "newest"}:
            sort_desc = True
            continue
    return page, include_read, sort_desc


def _strip_ref_prefix(body: str):
    if not body:
        return "", ""
    m = _REF_PREFIX_RE.match(body)
    if not m:
        return "", body
    ref = m.group(1).strip()
    rest = body[m.end():].lstrip("\r\n ")
    return ref, rest


def _thread_id_from_reference(db: Database, ref_msg_id: str) -> str:
    if not ref_msg_id:
        return ""
    ref_msg = db.get_message(ref_msg_id)
    if not ref_msg:
        return ""
    return ref_msg.thread_id or ref_msg.msg_id


def _build_info_response(bbs) -> str:
    node_id = _config_node_id(bbs.config)
    grid = _ping_grid(bbs.config) or "-"
    location = str(getattr(bbs.config, "location", "") or "-")
    uptime_s = max(0, int(time.time()) - int(getattr(bbs, "_started_at", int(time.time()))))
    online, total, _ = _presence_counts(
        bbs.db,
        int(getattr(bbs.config, "presence_timeout", 600) or 600),
    ) if bbs.db else (0, 0, 0)
    queue_depth = bbs.db.queue_depth() if bbs.db else 0
    return (
        f"Node: {node_id}\r\n"
        f"Grid: {grid}\r\n"
        f"Location: {location}\r\n"
        f"Peers: {online}/{total}\r\n"
        f"Queue: {queue_depth}\r\n"
        f"Uptime: {uptime_s}s\r\n"
    )


# ─── Channel Handlers (public commands, no ! prefix) ─────────────────────────
# channel_idx → {command_upper → handler}
_CHANNEL_HANDLERS = {}


def _normalize_public_command(text: str) -> str:
    """
    Normalize public channel command text.
    Supports variants like: ping, PING, #ping, #PING, test, #test, bboard, #bboard.
    """
    if not text:
        return ""
    cmd = text.strip().lower()
    while cmd.startswith(("#", "!")):
        cmd = cmd[1:].lstrip()
    return cmd


def _setup_channel_handlers():
    """Register public channel commands (no ! prefix needed)."""
    _CHANNEL_HANDLERS[1] = {
        "PING": lambda bbs, from_pk, args: _cmd_ping_direct(grid=_ping_grid(bbs.config)),
    }
    _CHANNEL_HANDLERS[2] = {
        "TEST": lambda bbs, from_pk, args: _cmd_bboard_direct(bbs.db),
        "BBOARD": lambda bbs, from_pk, args: _cmd_bboard_direct(bbs.db),
    }


# ─── BBS Command Definitions ──────────────────────────────────────────────────

def _setup_bbs_commands():
    """Build the BBS command handlers usable via MeshCore DM."""

    @bbs_command("HELP")
    def cmd_help(bbs, from_pk, args):
        return (
            "MeshBBS BBS | CMDS:\r\n"
            "!HELP !STAT !INFO !INBOX !THREAD\r\n"
            "!MSG !DELETE !WHOAMI !NODES\r\n"
            "!PING !ECHO !SELFTEST\r\n"
        )

    @bbs_command("STAT")
    def cmd_stat(bbs, from_pk, args):
        stats = bbs.routing.get_stats() if bbs.routing else {}
        node_id = _config_node_id(bbs.config)
        return (
            f"MeshBBS BBS: {node_id}\r\n"
            f"Messages: {stats.get('total_messages', 0)}\r\n"
            f"Nodes: {stats.get('online_nodes', 0)}/{stats.get('total_nodes', 0)}\r\n"
            f"Queue: {stats.get('queue_size', 0)}\r\n"
        )

    @bbs_command("NODES")
    def cmd_nodes(bbs, from_pk, args):
        nodes = bbs.db.get_all_nodes() if bbs.db else []
        if not nodes:
            return "No nodes known.\r\n"
        lines = ["Known Nodes:"]
        for n in nodes[:10]:
            st = {1: 'ON', 2: 'OFF', 3: 'STALE'}.get(n.status.value, '?')
            lines.append(f"  {n.node_id} | {st}")
        return "\r\n".join(lines)

    @bbs_command("INFO")
    def cmd_info(bbs, from_pk, args):
        return _build_info_response(bbs)

    @bbs_command("INBOX")
    def cmd_inbox(bbs, from_pk, args):
        if not bbs.db:
            return _err("db_unavailable")
        username = _sender_username(from_pk)
        if not username:
            return _err("sender_invalid")
        page, include_read, sort_desc = _parse_inbox_args(args)
        node_id = _config_node_id(bbs.config)
        total = bbs.db.inbox_count(username, include_read=True)
        unread = bbs.db.inbox_count(username, include_read=False)
        shown_total = unread if not include_read else total
        pages = max(1, (shown_total + _INBOX_PAGE_SIZE - 1) // _INBOX_PAGE_SIZE)
        page = min(page, pages)
        offset = (page - 1) * _INBOX_PAGE_SIZE
        entries = bbs.db.get_inbox(
            username,
            include_read=include_read,
            node_id=node_id,
            sort_desc=sort_desc,
            limit=_INBOX_PAGE_SIZE,
            offset=offset,
        )
        if not entries:
            return "Inbox empty.\r\n"
        lines = [
            f"INBOX page {page}/{pages} "
            f"filter={'all' if include_read else 'unread'} sort={'desc' if sort_desc else 'asc'} "
            f"total={total} unread={unread}"
        ]
        for i, e in enumerate(entries, 1):
            marker = "*" if not e.get("is_read") else " "
            row = offset + i
            subject = str(e.get("subject", ""))[:30]
            ref = str(e.get("ref_msg_id", "") or "")
            ref_suffix = f" ↳{ref[:8]}" if ref else ""
            lines.append(f" {marker}{row:02d}. {e['from_addr']} | {subject}{ref_suffix}")
        return "\r\n".join(lines)

    @bbs_command("THREAD")
    def cmd_thread(bbs, from_pk, args):
        if not bbs.db:
            return _err("db_unavailable")
        username = _sender_username(from_pk)
        if not username:
            return _err("sender_invalid")
        needle = args.strip()
        if not needle:
            return _err("usage_thread")
        node_id = _config_node_id(bbs.config)
        msg_id = needle
        if needle.isdigit():
            idx = int(needle)
            if idx <= 0:
                return _err("usage_thread")
            entries = bbs.db.get_inbox(username, include_read=True, node_id=node_id, limit=100)
            if idx > len(entries):
                return _err("inbox_item_missing")
            msg_id = str(entries[idx - 1].get("msg_id", "")).strip()
        if not msg_id:
            return _err("inbox_item_malformed")
        messages = bbs.db.get_thread_messages(msg_id, username, node_id=node_id)
        if not messages:
            return _err("thread_not_found")
        lines = [f"THREAD {msg_id[:8]} ({len(messages)} messages)"]
        for m in messages[-10:]:
            ts = time.strftime("%d.%m %H:%M", time.localtime(m.created_at))
            ref = f" ref={m.ref_msg_id[:8]}" if m.ref_msg_id else ""
            lines.append(f" {ts} {m.from_addr} -> {m.to_addr} | {m.subject[:20]}{ref}")
        return "\r\n".join(lines)

    @bbs_command("WHOAMI")
    def cmd_whoami(bbs, from_pk, args):
        username = _sender_username(from_pk)
        if not username:
            return _err("sender_invalid")
        node_id = _config_node_id(bbs.config)
        if bbs.db:
            online, total, _ = _presence_counts(
                bbs.db,
                int(getattr(bbs.config, "presence_timeout", 600) or 600),
            )
            return (
                f"Your address: {username}@{node_id}\r\n"
                f"Presence: online ({online}/{total} peers)\r\n"
            )
        return f"Your address: {username}@{node_id}\r\n"

    @bbs_command("MSG")
    def cmd_msg(bbs, from_pk, args):
        if not bbs.db:
            return _err("db_unavailable")
        username = _sender_username(from_pk)
        if not username:
            return _err("sender_invalid")
        node_id = _config_node_id(bbs.config)
        try:
            to_user, to_node, subject, body = _parse_msg_args(args, node_id)
        except ValueError as e:
            return _with_crlf(str(e))
        ref_msg_id, body = _strip_ref_prefix(body)
        thread_id = _thread_id_from_reference(bbs.db, ref_msg_id)
        if ref_msg_id and not thread_id:
            ref_msg_id = ""
        if ref_msg_id and not body:
            quoted = bbs.db.get_message(ref_msg_id)
            if quoted:
                preview = "\n".join(f"> {line}" for line in (quoted.body or "").splitlines()[:4])
                body = f"> {quoted.from_addr} wrote:\n{preview}".strip()
        if len(body) > _MAX_BODY_LEN:
            return _err("invalid_body_too_long")

        from_addr = f"{username}@{node_id}"
        to_addr = f"{to_user}@{to_node}"
        msg_id = str(uuid.uuid4())
        now = int(time.time())
        try:
            msg = MeshMessage(
                msg_id=msg_id,
                from_addr=from_addr,
                to_addr=to_addr,
                msg_type=MessageType.PERSONAL,
                subject=subject,
                body=body,
                priority=Priority.NORMAL,
                ttl=7,
                hops=0,
                created_at=now,
                received_at=0,
                status=MessageStatus.LOCAL,
                fwd_history=[],
                signature=None,
                thread_id=thread_id,
                ref_msg_id=ref_msg_id,
                chunk_ids=[],
            )
            entry = InboxEntry(
                msg_id=msg_id,
                to_user=to_user,
                is_read=False,
                is_deleted=False,
                received_at=now,
                read_at=0,
            )
            if not bbs.db.save_message(msg):
                return _err("msg_save_failed")
            if not bbs.db.save_inbox_entry(entry):
                return _err("inbox_save_failed")
            if ref_msg_id:
                return f"Message sent to @{to_user}@{to_node} (thread {thread_id[:8]}).\r\n"
            return f"Message sent to @{to_user}@{to_node}.\r\n"
        except Exception:
            log.exception("MC MSG error")
            return _err("msg_save_internal")

    @bbs_command("DELETE")
    def cmd_delete(bbs, from_pk, args):
        if not bbs.db:
            return _err("db_unavailable")
        username = _sender_username(from_pk)
        if not username:
            return _err("sender_invalid")
        try:
            idx = int(args.strip())
        except Exception:
            return _err("usage_delete")
        if idx <= 0:
            return _err("usage_delete")
        node_id = _config_node_id(bbs.config)
        entries = bbs.db.get_inbox(username, include_read=True, node_id=node_id)[:50]
        if idx > len(entries):
            return _err("inbox_item_missing")
        target = entries[idx - 1]
        msg_id = target.get("msg_id")
        if not msg_id:
            return _err("inbox_item_malformed")
        if bbs.db.delete_message(msg_id):
            return f"Deleted message #{idx}.\r\n"
        return _err("delete_failed")

    # ── DiagBot commands (registered in BBS registry) ──────────────────────
    @bbs_command("PING")
    def cmd_ping(bbs, from_pk, args):
        return _cmd_ping_direct(grid=_ping_grid(bbs.config))

    @bbs_command("ECHO")
    def cmd_echo(bbs, from_pk, args):
        return _diag_echo(bbs, from_pk, args)

    @bbs_command("SELFTEST")
    def cmd_selftest(bbs, from_pk, args):
        return _diag_selftest(bbs, from_pk, args)

    @bbs_command("STATUS")
    def cmd_status(bbs, from_pk, args):
        return _diag_status(bbs, from_pk, args)

    @bbs_command("QUEUES")
    def cmd_queues(bbs, from_pk, args):
        return _diag_queues(bbs, from_pk, args)

    @bbs_command("PEERS")
    def cmd_peers(bbs, from_pk, args):
        return _diag_peers(bbs, from_pk, args)

    @bbs_command("LASTSYNC")
    def cmd_lastsync(bbs, from_pk, args):
        return _diag_lastsync(bbs, from_pk, args)

    @bbs_command("BBOARD")
    def cmd_bboard(bbs, from_pk, args):
        return _cmd_bboard_direct(bbs.db)

    @bbs_command("TEST")
    def cmd_test(bbs, from_pk, args):
        return _cmd_bboard_direct(bbs.db)

    return BBS_COMMANDS


# ─── DiagBot standalone command wrappers (for BBS registry) ──────────────────

def _diag_ping(bbs, from_pk, args):
    return _cmd_ping_direct(grid=_ping_grid(bbs.config))

def _diag_echo(bbs, from_pk, args):
    return _cmd_echo_direct(args or "?")

def _diag_selftest(bbs, from_pk, args):
    return _cmd_selftest_direct(bbs.db, bbs.routing, bbs.mc_bridge)

def _diag_status(bbs, from_pk, args):
    return _cmd_status_direct(from_pk, bbs.db)

def _diag_queues(bbs, from_pk, args):
    return _cmd_queues_direct(from_pk, bbs.db)

def _diag_peers(bbs, from_pk, args):
    return _cmd_peers_direct(from_pk, bbs.db)

def _diag_lastsync(bbs, from_pk, args):
    return _cmd_lastsync_direct(from_pk, bbs.db)


# ─── Server ───────────────────────────────────────────────────────────────────

class MeshBBSServer:
    def __init__(self, config: MeshBBSConfig):
        self.config = config
        self.db = None
        self.routing = None
        self.sync = None
        self.mc_bridge = None
        self.diagbot = None
        self._running = False
        self._auto_finger_task: Optional[asyncio.Task] = None
        self._discovery_task: Optional[asyncio.Task] = None
        self._presence_task: Optional[asyncio.Task] = None
        self._retention_task: Optional[asyncio.Task] = None
        self._started_at = int(time.time())

    def _build_auto_finger_payload(self) -> str:
        node_id = _config_node_id(self.config)
        grid = _ping_grid(self.config) or "-"
        location = str(getattr(self.config, "location", "") or "-")
        peers = self.db.get_all_nodes() if self.db else []
        online = sum(1 for p in peers if p.status.value == 1)
        queue_depth = self.db.queue_depth() if self.db else 0
        uptime = max(0, int(time.time()) - int(self._started_at))
        return (
            f"FINGER {node_id} grid={grid} loc={location} "
            f"peers={online}/{len(peers)} queue={queue_depth} up={uptime}s"
        )

    async def _auto_finger_loop(self):
        interval = max(60, int(getattr(self.config, "auto_finger_interval", 900) or 900))
        channel = int(getattr(self.config, "auto_finger_channel", 1) or 1)
        while self._running:
            try:
                if self.mc_bridge and self.mc_bridge.is_connected():
                    self.mc_bridge.send_channel_message(channel, self._build_auto_finger_payload())
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Auto-finger loop failed")
                await asyncio.sleep(min(60, interval))

    def _upsert_presence(self, node_id: str, announced_ts: Optional[int] = None):
        if not self.db:
            return
        local = _config_node_id(self.config).lower()
        target = (node_id or "").strip()
        if not target or target.lower() == local:
            return
        node = self.db.get_node(target)
        if not node:
            node = PeerNode(node_id=target, host="mesh", tcp_port=0)
        node.last_seen = int(announced_ts or time.time())
        node.status = NodeStatus.ONLINE
        self.db.save_node(node)

    def _handle_presence_announce(self, raw_cmd: str, channel_idx: int):
        msg = (raw_cmd or "").strip()
        if not msg:
            return False
        discover = _DISCOVERY_RE.match(msg)
        if discover:
            node_id = discover.group(1)
            announced = int(discover.group(2)) if discover.group(2) else int(time.time())
            self._upsert_presence(node_id, announced)
            if self.mc_bridge and self.mc_bridge.is_connected():
                local = _config_node_id(self.config)
                self.mc_bridge.send_channel_message(
                    channel_idx,
                    f"PRESENCE {local} {int(time.time())}",
                )
            return True
        presence = _PRESENCE_RE.match(msg)
        if presence:
            node_id = presence.group(1)
            announced = int(presence.group(2)) if presence.group(2) else int(time.time())
            self._upsert_presence(node_id, announced)
            return True
        finger = _FINGER_RE.match(msg)
        if finger:
            self._upsert_presence(finger.group(1), int(time.time()))
            return True
        return False

    async def _discovery_loop(self):
        interval = max(30, int(getattr(self.config, "discovery_interval", 120) or 120))
        channel = int(getattr(self.config, "discovery_channel", 0) or 0)
        while self._running:
            try:
                if self.mc_bridge and self.mc_bridge.is_connected():
                    node_id = _config_node_id(self.config)
                    self.mc_bridge.send_channel_message(channel, f"DISCOVER {node_id} {int(time.time())}")
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Discovery loop failed")
                await asyncio.sleep(min(30, interval))

    async def _presence_loop(self):
        interval = max(30, int(getattr(self.config, "presence_interval", 120) or 120))
        channel = int(getattr(self.config, "presence_channel", 0) or 0)
        timeout = max(30, int(getattr(self.config, "presence_timeout", 600) or 600))
        while self._running:
            try:
                if self.db:
                    self.db.mark_stale_nodes_offline(timeout)
                if self.mc_bridge and self.mc_bridge.is_connected():
                    node_id = _config_node_id(self.config)
                    self.mc_bridge.send_channel_message(channel, f"PRESENCE {node_id} {int(time.time())}")
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Presence loop failed")
                await asyncio.sleep(min(30, interval))

    async def _retention_loop(self):
        interval = max(300, int(getattr(self.config, "retention_interval", 3600) or 3600))
        retention_days = max(1, int(getattr(self.config, "retention_days", 30) or 30))
        while self._running:
            try:
                if self.db:
                    deleted = self.db.prune_messages(retention_days)
                    if deleted:
                        log.info("Retention cleanup removed %s messages", deleted)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Retention loop failed")
                await asyncio.sleep(min(300, interval))

    def _handle_meshcore_channel(self, channel_idx: int, text: str, sender_ts: int, rssi: int, snr: int, from_pubkey: str = None, hops: int = 0):
        """Handle incoming channel message — PING/TEST on any channel, respond on same channel + DM."""
        if not text:
            return
        # Extract command from "SENDER: COMMAND" format (strip SNR/RSSI suffix)
        from_name = None
        if ": " in text:
            from_name = _sanitize_display_name(text.split(": ", 1)[0])
            after_colon = text.split(": ", 1)[1]
            # Strip trailing SNR/RSSI info like " SNR=12.5 RSSI=None"
            import re
            after_colon = re.sub(r'\s+SNR=[\d.]+\s*RSSI=[\wNone]+$', '', after_colon)
            raw_cmd = after_colon.strip()
        else:
            raw_cmd = text.strip()

        if len(raw_cmd) > _MAX_CHANNEL_CMD_LEN:
            return

        if self._handle_presence_announce(raw_cmd, channel_idx):
            return

        cmd = _normalize_public_command(raw_cmd)
        if cmd not in ("ping", "test", "bboard"):
            return

        is_ping = cmd == "ping"

        try:
            # Grid square and location from config
            grid = _ping_grid(self.config)
            location = str(getattr(self.config, "location", "angekommen in DEINE-REGION")).strip()
            if not location:
                location = "angekommen in DEINE-REGION"

            # Antwortzeit in Sekunden
            resp_ms = int(time.time() * 1000) - int(sender_ts * 1000) if sender_ts else 0
            resp_s = max(0, resp_ms // 1000)

            if is_ping is True:
                response = _cmd_ping_direct(from_name, grid, hops, resp_s)
            elif cmd == "test":
                test_msg = f"Test erfolgreich {location}"
                response = f"@{from_name} {test_msg}" if from_name else test_msg
            else:
                response = _cmd_bboard_direct(self.db)

            if response and self.mc_bridge:
                if from_pubkey and from_pubkey != "?":
                    self.mc_bridge.send_dm(from_pubkey, response)
                self.mc_bridge.send_channel_message(channel_idx, response)
                log.info(f"[CHAN#{channel_idx}] {cmd} -> {response[:40]}")
        except Exception as e:
            log.error(f"Channel handler error ch={channel_idx}: {e}")

    def _handle_meshcore_dm(self, from_pubkey: str, text: str) -> str:
        """Handle incoming DM from MeshCore — route to BBS command."""
        if not isinstance(text, str):
            return _err("dm_invalid_payload")
        if len(text) > _MAX_DM_TEXT_LEN:
            return _err("dm_too_long")

        if self.diagbot:
            resp = self.diagbot.handle_dm(from_pubkey, text)
            if resp is not None:
                return resp

        if text.startswith("!"):
            parts = text[1:].split(maxsplit=1)
            cmd = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ""
            if cmd in BBS_COMMANDS:
                try:
                    return BBS_COMMANDS[cmd](self, from_pubkey, args)
                except Exception:
                    log.exception("Command handler failed: %s", cmd)
                    return _err("cmd_internal_failure")
            else:
                return f"Unknown: {cmd}\r\nTry !HELP"
        else:
            username = _sender_username(from_pubkey) or "unknown"
            node_id = _config_node_id(self.config)
            return (
                f"MeshBBS BBS | Du: {username}@{node_id}\r\n"
                f"Befehle: !HELP !STAT !INFO !INBOX !THREAD !MSG !DELETE !WHOAMI !NODES"
            )

    async def start(self):
        log.info("MeshBBS v0.3 starting — MeshCore BBS only")
        Path(self.config.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.config.db_path)
        log.info(f"Database: {self.config.db_path}")

        _setup_channel_handlers()
        _setup_bbs_commands()

        self.mc_bridge = MeshCoreBridge(
            host=self.config.tcp_host,
            port=self.config.tcp_port,
            node_id=self.config.node_id,
            on_dm_received=self._handle_meshcore_dm,
            on_channel_message=self._handle_meshcore_channel
        )
        if await self.mc_bridge.connect():
            log.info(f"MeshCore bridge connected ({self.config.tcp_host}:{self.config.tcp_port})")
            asyncio.create_task(self.mc_bridge.run())
        else:
            log.warning("MeshCore bridge failed - running in degraded mode")

        self.diagbot = DiagBot(db=self.db, routing=self.routing, mc_bridge=self.mc_bridge, config=self.config)
        log.info("DiagBot initialized")

        self.routing = RoutingEngine(self.db, self.config.node_id, self.config.tcp_host, self.config.tcp_port)
        self.routing.on_packet(self._send_to_meshcore)
        await self.routing.start()
        log.info("Routing engine started")

        self.sync = SyncEngine(self.db, self.routing, self.config.node_id, self.config.sync_interval)
        await self.sync.start()
        log.info("Sync engine started")

        self._started_at = int(time.time())
        log.info("MeshCore DM BBS: message @%s", _config_node_id(self.config))
        self._running = True
        retention_days = max(1, int(getattr(self.config, "retention_days", 30) or 30))
        deleted = self.db.prune_messages(retention_days)
        if deleted:
            log.info("Retention startup cleanup removed %s messages", deleted)
        if bool(getattr(self.config, "auto_finger_enabled", True)):
            self._auto_finger_task = asyncio.create_task(self._auto_finger_loop())
            log.info(
                "Auto-finger enabled: channel=%s interval=%ss",
                getattr(self.config, "auto_finger_channel", 1),
                getattr(self.config, "auto_finger_interval", 900),
            )
        if bool(getattr(self.config, "discovery_enabled", True)):
            self._discovery_task = asyncio.create_task(self._discovery_loop())
            log.info(
                "Discovery enabled: channel=%s interval=%ss",
                getattr(self.config, "discovery_channel", 0),
                getattr(self.config, "discovery_interval", 120),
            )
        if bool(getattr(self.config, "presence_enabled", True)):
            self._presence_task = asyncio.create_task(self._presence_loop())
            log.info(
                "Presence enabled: channel=%s interval=%ss timeout=%ss",
                getattr(self.config, "presence_channel", 0),
                getattr(self.config, "presence_interval", 120),
                getattr(self.config, "presence_timeout", 600),
            )
        self._retention_task = asyncio.create_task(self._retention_loop())

        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_event_loop().add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        while self._running:
            await asyncio.sleep(30)

    async def stop(self):
        log.info("Shutting down MeshBBS...")
        self._running = False
        if self._auto_finger_task:
            self._auto_finger_task.cancel()
            try:
                await self._auto_finger_task
            except asyncio.CancelledError:
                pass
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass
        if self._presence_task:
            self._presence_task.cancel()
            try:
                await self._presence_task
            except asyncio.CancelledError:
                pass
        if self._retention_task:
            self._retention_task.cancel()
            try:
                await self._retention_task
            except asyncio.CancelledError:
                pass
        if self.sync:
            await self.sync.stop()
        if self.routing:
            await self.routing.stop()
        if self.mc_bridge:
            await self.mc_bridge.disconnect()
        log.info("MeshBBS stopped.")

    async def _send_to_meshcore(self, peer, packet: dict) -> bool:
        if self.mc_bridge and self.mc_bridge.is_connected():
            dest = packet.get("dest_pubkey", "")
            text = packet.get("text", "")
            if dest and text:
                self.mc_bridge.send_dm(dest, text)
                return True
        return False


def main():
    config = MeshBBSConfig()
    server = MeshBBSServer(config)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()
