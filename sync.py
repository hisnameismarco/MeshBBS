"""MeshBBS Sync - Epidemic Bulletin Distribution & Peer Synchronisation"""
import asyncio
import json
import time
import hashlib
from typing import Dict, List, Set, Optional
from dataclasses import asdict

from .models import (
    MeshMessage, PeerNode, MessageType, Priority,
    MessageStatus, QueueStatus, NodeStatus,
    parse_address, is_bulletin_addr,
    ReplicationLevel, DEFAULT_TTL_Bulletin
)
from .store import Database
from .routing import RoutingEngine


class SyncProtocol:
    """
    Epidemic-style sync for bulletins + selective message sync.
    Two phases:
    1. SND (Send): Announce what you have
    2. REQ (Request): Request what you need
    """

    VERSION = 1
    MAX_HOPS = 15

    def __init__(self, db: Database, routing: RoutingEngine, node_id: str):
        self.db = db
        self.routing = routing
        self.node_id = node_id

    # ─── Sync Packet Builders ────────────────────────────────

    def build_synd(self, peer_node_id: str) -> dict:
        """
        Build SYND (synopsis) packet - advertises what msgs we have.
        Only bulletins and high-priority personal msgs for this peer.
        """
        our_node_addr = f"@{self.node_id}"

        # Get last N msg IDs we have
        msgs = self.db.get_messages_to_addr(f"@{self.node_id}", include_deleted=True)[-50:]

        synopsis = []
        for m in msgs:
            # Skip if peer already has this (check via their msg_seq)
            peer = self.db.get_node(peer_node_id)
            last_seq = peer.msg_seq if peer else 0

            synopsis.append({
                "id": m.msg_id,
                "type": m.msg_type,
                "area": self._extract_area(m.to_addr),
                "seq": m.created_at,
                "ttl": m.ttl,
            })

        return {
            "sync_ver": self.VERSION,
            "node": self.node_id,
            "seq": int(time.time()),
            "synopsis": synopsis,
        }

    def build_req(self, peer_node_id: str, synd: dict) -> List[dict]:
        """
        Compare synopsis with our knowledge, build REQ (request) list.
        Returns list of msg_ids we want from that peer.
        """
        peer = self.db.get_node(peer_node_id)
        if not peer:
            return []

        our_msg_ids = {m.msg_id for m in self.db.get_messages_to_addr(f"@{self.node_id}")}
        peer_msgs: Dict[str, dict] = {}

        # Index synd by id
        for entry in synd.get("synopsis", []):
            if entry["id"] not in our_msg_ids:
                peer_msgs[entry["id"]] = entry

        # We want msgs we don't have
        req = []
        for msg_id, info in peer_msgs.items():
            # Check TTL viability
            if info.get("ttl", 0) > 0:
                req.append({"id": msg_id, "type": info.get("type", 1)})

        return req[:20]  # Limit per sync round

    def build_push(self, msg: MeshMessage) -> dict:
        """Build PUSH packet for sending a message to peer"""
        return {
            "sync_ver": self.VERSION,
            "node": self.node_id,
            "type": "push",
            "msg": {
                "id": msg.msg_id,
                "from": msg.from_addr,
                "to": msg.to_addr,
                "msg_type": msg.msg_type,
                "subj": msg.subject,
                "body": msg.body,
                "time": msg.created_at,
                "ttl": msg.ttl,
                "hops": msg.hops,
                "thread": msg.thread_id,
                "ref": msg.ref_msg_id,
            }
        }

    def build_nack(self, msg_id: str, reason: str) -> dict:
        """Negative acknowledgement"""
        return {
            "sync_ver": self.VERSION,
            "node": self.node_id,
            "type": "nack",
            "msg_id": msg_id,
            "reason": reason,
        }

    def build_ack(self, msg_id: str) -> dict:
        """Acknowledgement"""
        return {
            "sync_ver": self.VERSION,
            "node": self.node_id,
            "type": "ack",
            "msg_id": msg_id,
        }

    # ─── Sync Processing ────────────────────────────────────

    def process_synd(self, peer_node_id: str, synd: dict) -> List[dict]:
        """Process incoming SYND, return REQ packets"""
        return self.build_req(peer_node_id, synd)

    def process_push(self, peer_node_id: str, push: dict) -> str:
        """Process incoming PUSH, store message, return status"""
        msg_data = push.get("msg", {})

        msg = MeshMessage(
            msg_id=msg_data.get("id", ""),
            from_addr=msg_data.get("from", ""),
            to_addr=msg_data.get("to", ""),
            msg_type=MessageType(msg_data.get("msg_type", 1)),
            subject=msg_data.get("subj", ""),
            body=msg_data.get("body", ""),
            created_at=msg_data.get("time", int(time.time())),
            ttl=msg_data.get("ttl", DEFAULT_TTL_Bulletin),
            hops=msg_data.get("hops", 0),
            thread_id=msg_data.get("thread", ""),
            ref_msg_id=msg_data.get("ref", ""),
        )

        # Dup check
        key = f"{msg.msg_id}:{self.node_id}"
        if self.db.is_seen(key):
            return "duplicate"

        self.db.mark_seen(key)
        self.routing.process_inbound(msg)
        return "stored"

    def process_ack(self, msg_id: str):
        """Message confirmed by peer - remove from queue"""
        # Find and update queue entries
        self.db.remove_from_queue(msg_id, peer_node_id)

    # ─── Helpers ────────────────────────────────────────────

    def _extract_area(self, addr: str) -> str:
        """Extract area from bulletin address"""
        if is_bulletin_addr(addr):
            return addr.split('@')[0].replace('bulletin@', '').replace('#', '')
        return ""


class SyncEngine:
    """
    Orchestrates sync sessions with peers.
    Runs periodically and on demand.
    """

    def __init__(self, db: Database, routing: RoutingEngine, node_id: str,
                 sync_interval: int = 300):
        self.db = db
        self.routing = routing
        self.node_id = node_id
        self.sync_interval = sync_interval
        self.sync_protocol = SyncProtocol(db, routing, node_id)
        self._running = False
        self._sync_task = None

    async def start(self):
        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())

    async def stop(self):
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()

    async def _sync_loop(self):
        """Periodic sync with all known peers"""
        while self._running:
            try:
                await asyncio.sleep(self.sync_interval)
                await self.sync_all_peers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Sync loop error: {e}")

    async def sync_peer(self, peer: PeerNode) -> bool:
        """
        Execute full sync with one peer.
        1. Send SYND
        2. Receive their SYND → send REQ
        3. Receive their REQ → send PUSHes
        4. Send our REQ → receive their PUSHes
        """
        if peer.status != NodeStatus.ONLINE:
            return False

        try:
            # Phase 1: Send our synd
            synd = self.sync_protocol.build_synd(peer.node_id)
            # In real impl: send via MeshCoreAdapter
            # Here we just log
            return True
        except Exception as e:
            print(f"Sync with {peer.node_id} failed: {e}")
            return False

    async def sync_all_peers(self):
        """Sync with all known online peers"""
        nodes = self.db.get_online_nodes()
        for node in nodes:
            if node.node_id != self.node_id:
                await self.sync_peer(node)

    def get_sync_status(self) -> dict:
        """Return sync status summary"""
        nodes = self.db.get_online_nodes()
        return {
            "node_id": self.node_id,
            "peers": len(nodes),
            "sync_interval": self.sync_interval,
        }
