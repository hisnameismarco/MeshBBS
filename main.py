#!/usr/bin/env python3
"""MeshBBS Entry Point — MeshCore BBS via TCP Bridge"""
import asyncio
import os
import sys
import signal
import hashlib
import logging
import sqlite3
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from MeshBBS.config import MeshBBSConfig
from MeshBBS.store import Database
from MeshBBS.routing import RoutingEngine
from MeshBBS.sync import SyncEngine
from MeshBBS.meshcore_if import MeshCoreBridge
from MeshBBS.models import MessageType, MessageStatus, MailboxUser, parse_address, Priority
from MeshBBS.diagbot import DiagBot, _cmd_ping_direct, _cmd_selftest_direct, _cmd_status_direct, _cmd_queues_direct, _cmd_peers_direct, _cmd_lastsync_direct, _cmd_bboard_direct

log = logging.getLogger("MeshBBS")


# ─── BBS Command Registry ─────────────────────────────────────────────────────

BBS_COMMANDS = {}

def bbs_command(name):
    def decorator(func):
        BBS_COMMANDS[name.upper()] = func
        return func
    return decorator


# ─── Channel Handlers (public commands, no ! prefix) ─────────────────────────
# channel_idx → {command_upper → handler}
_CHANNEL_HANDLERS = {}


def _setup_channel_handlers():
    """Register public channel commands (no ! prefix needed)."""
    _CHANNEL_HANDLERS[1] = {
        "PING": lambda bbs, from_pk, args: _cmd_ping_direct(),
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
            "!MSG !WHOAMI !NODES\r\n"
            "!PING !ECHO !SELFTEST\r\n"
        )

    @bbs_command("STAT")
    def cmd_stat(bbs, from_pk, args):
        stats = bbs.routing.get_stats() if bbs.routing else {}
        return (
            f"MeshBBS BBS: YOUR-NODE-ID\r\n"
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
        username = from_pk[:8].lower()
        node_id = getattr(bbs.config, 'node_id', 'YOUR-NODE-ID')
        entries = bbs.db.get_inbox(username, include_read=True, node_id=node_id)[:5] if bbs.db else []
        if not entries:
            return "Inbox empty.\r\n"
        lines = ["Your messages:"]
        for i, e in enumerate(entries, 1):
            lines.append(f"  {i}. {e['from_addr']} | {e['subject'][:30]}")
        return "\r\n".join(lines)

    @bbs_command("WHOAMI")
    def cmd_whoami(bbs, from_pk, args):
        username = from_pk[:8].lower()
        return f"Your address: {username}@YOUR-NODE-ID\r\n"

    @bbs_command("MSG")
    def cmd_msg(bbs, from_pk, args):
        # Format: !MSG @username <subject> [text]
        # Example: !MSG @sysop Hallo Dies ist eine Nachricht
        parts = args.lstrip()
        if not parts.startswith("@"):
            return "Usage: !MSG @<username> <subject> [text]"
        parts = parts[1:].lstrip()
        space_idx = parts.find(" ")
        if space_idx == -1:
            return "Usage: !MSG @<username> <subject> [text]"
        to_user = parts[:space_idx].lower()
        rest = parts[space_idx+1:].lstrip()
        space_idx2 = rest.find(" ")
        if space_idx2 == -1:
            subject = rest[:40]
            body = ""
        else:
            subject = rest[:space_idx2][:40]
            body = rest[space_idx2+1:]
        username = from_pk[:8].lower()
        from_addr = f"{username}@YOUR-NODE-ID"
        to_addr = f"{to_user}@YOUR-NODE-ID"
        msg_id = str(uuid.uuid4())
        now = int(time.time())
        try:
            db = sqlite3.connect(bbs.config.db_path, isolation_level=None)
            db.execute("""INSERT INTO messages
                (msg_id, from_addr, to_addr, msg_type, subject, body, chunk,
                 total_chunks, priority, ttl, hops, created_at, received_at,
                 status, fwd_history, signature, thread_id, ref_msg_id, chunk_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (msg_id, from_addr, to_addr, MessageType.PERSONAL.value, subject, body,
                 0, 0, Priority.NORMAL.value, 7, 0, now, 0, MessageStatus.LOCAL.value,
                 "[]", None, "", "", "[]"))
            db.execute("""INSERT INTO inbox
                (msg_id, to_user, is_read, is_deleted, received_at, read_at)
                VALUES (?, ?, 0, 0, ?, 0)""", (msg_id, to_user, now))
            db.close()
            return f"Message sent to @{to_user}.\r\n"
        except Exception as e:
            import sys as _sys
            print(f"MC MSG error: {e}", file=_sys.stderr)
            return f"Error: {e}"

    # ── DiagBot commands (registered in BBS registry) ──────────────────────
    @bbs_command("PING")
    def cmd_ping(bbs, from_pk, args):
        return _cmd_ping_direct()

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
    return _cmd_ping_direct()

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
            from_name = text.split(": ", 1)[0].strip()
            after_colon = text.split(": ", 1)[1]
            # Strip trailing SNR/RSSI info like " SNR=12.5 RSSI=None"
            import re
            after_colon = re.sub(r'\s+SNR=[\d.]+\s*RSSI=[\wNone]+$', '', after_colon)
            cmd = after_colon.strip().upper()
        else:
            cmd = text.strip().upper()
        if cmd.lower() not in ("ping", "test", "bboard"):
            return

        is_ping = cmd.lower() == "ping"

        try:
            # Grid square für diesen Node
            import math
            lat, lon = 51.898458, 12.464044
            grid = chr(ord('A') + int((lon + 180) / 18) % 18) + chr(ord('A') + int((lat + 90) / 15) % 18) + str(int((lon + 180) % 18 / 5)) + str(int((lat + 90) % 15 / 2.5)) + str(int(((lon + 180) % 5) / 0.5)) + str(int(((lat + 90) % 2.5) / 0.25))

            # Antwortzeit in Sekunden
            resp_ms = int(time.time() * 1000) - int(sender_ts * 1000) if sender_ts else 0
            resp_s = max(0, resp_ms // 1000)

            if is_ping is True:
                response = _cmd_ping_direct(from_name, grid, hops, resp_s)
            elif cmd.lower() == "test":
                response = f"@{from_name} {self.cfg.location}" if from_name else self.cfg.location
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
                    return f"Error: {e}"
            else:
                return f"Unknown: {cmd}\r\nTry !HELP"
        else:
            username = from_pubkey[:8].lower()
            return (
                f"MeshBBS BBS | Du: {username}@YOUR-NODE-ID\r\n"
                f"Befehle: !HELP !STAT !INBOX !MSG !WHOAMI !NODES"
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

        self.diagbot = DiagBot(db=self.db, routing=self.routing, mc_bridge=self.mc_bridge)
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
