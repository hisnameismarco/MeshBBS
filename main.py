#!/usr/bin/env python3
"""MeshBBS Entry Point — MeshCore BBS via TCP Bridge"""
import asyncio
import os
import sys
import signal
import hashlib
import logging
import time
import uuid
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from meshmail.config import MeshBBSConfig
from meshmail.store import Database
from meshmail.routing import RoutingEngine
from meshmail.sync import SyncEngine
from meshmail.meshcore_if import MeshCoreBridge
from meshmail.models import MessageType, MessageStatus, MailboxUser, parse_address, Priority, MeshMessage, InboxEntry
from meshmail.diagbot import DiagBot, _cmd_ping_direct, _cmd_selftest_direct, _cmd_status_direct, _cmd_queues_direct, _cmd_peers_direct, _cmd_lastsync_direct, _cmd_bboard_direct, _grid_from_config

log = logging.getLogger("MeshBBS")


# ─── BBS Command Registry ─────────────────────────────────────────────────────

BBS_COMMANDS = {}
_VALID_USER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_VALID_NODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_SUBJECT_LEN = 40
_MAX_BODY_LEN = 512
_MAX_DM_TEXT_LEN = 1024
_MAX_CHANNEL_CMD_LEN = 128
_VALID_DISPLAY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")

def bbs_command(name):
    def decorator(func):
        BBS_COMMANDS[name.upper()] = func
        return func
    return decorator


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
        raise ValueError("Usage: !MSG @<username>[@<node>] <subject> [text]")

    payload = payload[1:].strip()
    if not payload:
        raise ValueError("Usage: !MSG @<username>[@<node>] <subject> [text]")

    # New extended format: !MSG @user[@node] | subject | body text
    if "|" in payload:
        parts = [p.strip() for p in payload.split("|", 2)]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError("Usage: !MSG @<username>[@<node>] | <subject> | [text]")
        target = parts[0]
        subject = parts[1]
        body = parts[2] if len(parts) > 2 else ""
    else:
        first = payload.split(maxsplit=1)
        if len(first) < 2:
            raise ValueError("Usage: !MSG @<username>[@<node>] <subject> [text]")
        target = first[0].strip()
        rest = first[1].strip()
        if not rest:
            raise ValueError("Usage: !MSG @<username>[@<node>] <subject> [text]")
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
        raise ValueError("Error: invalid username.")
    if not _VALID_NODE_RE.fullmatch(to_node):
        raise ValueError("Error: invalid destination node.")

    clean_subject = subject.strip()[:_MAX_SUBJECT_LEN]
    if not clean_subject:
        raise ValueError("Error: subject must not be empty.")

    clean_body = body.strip()[:_MAX_BODY_LEN]
    return to_user, to_node, clean_subject, clean_body


def _sanitize_display_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    return raw if _VALID_DISPLAY_NAME_RE.fullmatch(raw) else ""


def _ping_grid(config: MeshBBSConfig) -> str:
    return _grid_from_config(config)


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
            "!HELP !STAT !INBOX\r\n"
            "!MSG !DELETE !WHOAMI !NODES\r\n"
            "!PING !ECHO !SELFTEST\r\n"
        )

    @bbs_command("STAT")
    def cmd_stat(bbs, from_pk, args):
        stats = bbs.routing.get_stats() if bbs.routing else {}
        node_id = getattr(bbs.config, "node_id", "YOUR-NODE-ID")
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

    @bbs_command("INBOX")
    def cmd_inbox(bbs, from_pk, args):
        username = _sender_username(from_pk)
        if not username:
            return "Error: sender identity invalid.\r\n"
        node_id = getattr(bbs.config, 'node_id', 'YOUR-NODE-ID')
        entries = bbs.db.get_inbox(username, include_read=True, node_id=node_id)[:10] if bbs.db else []
        if not entries:
            return "Inbox empty.\r\n"
        lines = ["Your messages:"]
        for i, e in enumerate(entries, 1):
            lines.append(f"  {i}. {e['from_addr']} | {e['subject'][:30]}")
        return "\r\n".join(lines)

    @bbs_command("WHOAMI")
    def cmd_whoami(bbs, from_pk, args):
        username = _sender_username(from_pk)
        if not username:
            return "Error: sender identity invalid.\r\n"
        node_id = getattr(bbs.config, "node_id", "YOUR-NODE-ID")
        return f"Your address: {username}@{node_id}\r\n"

    @bbs_command("MSG")
    def cmd_msg(bbs, from_pk, args):
        if not bbs.db:
            return "Error: database unavailable.\r\n"
        username = _sender_username(from_pk)
        if not username:
            return "Error: sender identity invalid.\r\n"
        node_id = getattr(bbs.config, "node_id", "YOUR-NODE-ID")
        try:
            to_user, to_node, subject, body = _parse_msg_args(args, node_id)
        except ValueError as e:
            return f"{e}\r\n"

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
                thread_id="",
                ref_msg_id="",
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
                return "Error: message persistence failed.\r\n"
            if not bbs.db.save_inbox_entry(entry):
                return "Error: inbox persistence failed.\r\n"
            return f"Message sent to @{to_user}@{to_node}.\r\n"
        except Exception as e:
            log.exception("MC MSG error")
            return "Error: internal failure while saving message.\r\n"

    @bbs_command("DELETE")
    def cmd_delete(bbs, from_pk, args):
        if not bbs.db:
            return "Error: database unavailable.\r\n"
        username = _sender_username(from_pk)
        if not username:
            return "Error: sender identity invalid.\r\n"
        try:
            idx = int(args.strip())
        except Exception:
            return "Usage: !DELETE <number>\r\n"
        if idx <= 0:
            return "Usage: !DELETE <number>\r\n"
        node_id = getattr(bbs.config, "node_id", "YOUR-NODE-ID")
        entries = bbs.db.get_inbox(username, include_read=True, node_id=node_id)[:50]
        if idx > len(entries):
            return "Error: inbox item not found.\r\n"
        target = entries[idx - 1]
        msg_id = target.get("msg_id")
        if not msg_id:
            return "Error: malformed inbox entry.\r\n"
        if bbs.db.delete_message(msg_id):
            return f"Deleted message #{idx}.\r\n"
        return "Error: could not delete message.\r\n"

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
    return args or "?"

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
            return "Error: invalid DM payload.\r\n"
        if len(text) > _MAX_DM_TEXT_LEN:
            return f"Error: DM too long (max {_MAX_DM_TEXT_LEN} chars).\r\n"

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
                except Exception as e:
                    log.exception("Command handler failed: %s", cmd)
                    return "Error: internal command failure.\r\n"
            else:
                return f"Unknown: {cmd}\r\nTry !HELP"
        else:
            username = _sender_username(from_pubkey) or "unknown"
            node_id = getattr(self.config, "node_id", "YOUR-NODE-ID")
            return (
                f"MeshBBS BBS | Du: {username}@{node_id}\r\n"
                f"Befehle: !HELP !STAT !INBOX !MSG !DELETE !WHOAMI !NODES"
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

        log.info("MeshCore DM BBS: message @YOUR-NODE-ID")
        self._running = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_event_loop().add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        while self._running:
            await asyncio.sleep(30)

    async def stop(self):
        log.info("Shutting down MeshBBS...")
        self._running = False
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
