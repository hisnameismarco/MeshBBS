"""Routing engine and peer synchronization for MeshMail"""
import asyncio
import json
import time
import uuid
import hashlib
from typing import Optional, List, Dict, Set, Callable
from dataclasses import asdict

from .models import (
    MeshMessage, PeerNode, QueueEntry, InboxEntry,
    MessageType, Priority, MessageStatus, QueueStatus, NodeStatus,
    parse_address, is_bulletin_addr, is_sysop_addr,
    DEFAULT_TTL_Personal, DEFAULT_TTL_Bulletin, DEFAULT_TTL_System,
    MAX_CHUNKS, MAX_BODY_LEN
)
from .store import Database


class RoutingEngine:
    """Handles message routing, queueing, and peer sync"""

    def __init__(self, db: Database, node_id: str, tcp_host: str = "192.168.2.30", tcp_port: int = 5000):
        self.db = db
        self.node_id = node_id  # 8-char MeshCore node ID
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self._callbacks: Dict[str, Callable] = {}
        self._running = False
        self._queue_processor_task = None

    def on_packet(self, callback: Callable):
        """Register callback for outbound packets"""
        self._callbacks['packet'] = callback

    async def start(self):
        self._running = True
        self._queue_processor_task = asyncio.create_task(self._process_queue_loop())

    async def stop(self):
        self._running = False
        if self._queue_processor_task:
            self._queue_processor_task.cancel()

    # ─── Message Handling ──────────────────────────────────────

    def handle_inbound(self, packet: dict) -> Optional[MeshMessage]:
        """Parse incoming packet into MeshMessage"""
        try:
            payload = packet.get('payload', {})
            msg = MeshMessage(
                msg_id=payload.get('id', ''),
                from_addr=payload.get('from', ''),
                to_addr=payload.get('to', ''),
                msg_type=MessageType(payload.get('type', 1)),
                subject=payload.get('subj', ''),
                body=payload.get('body', ''),
                created_at=payload.get('time', int(time.time())),
                hops=payload.get('hops', 0),
                ttl=payload.get('ttl', 7),
                thread_id=payload.get('thread', ''),
                ref_msg_id=payload.get('ref', ''),
                signature=payload.get('sig', '').encode() if payload.get('sig') else None,
            )
            msg.update_after_hop()
            return msg
        except Exception as e:
            print(f"handle_inbound error: {e}")
            return None

    def route_message(self, msg: MeshMessage) -> List[tuple]:
        """Determine where to forward a message. Returns list of (node_id, action)"""
        _, to_node = parse_address(msg.to_addr)

        # Local delivery
        if not to_node or to_node == self.node_id:
            return [(None, 'local')]

        # Check if we know this node directly
        peer = self.db.get_node(to_node)
        if peer and peer.status == NodeStatus.ONLINE:
            return [(to_node, 'forward')]

        # Need to forward to all online peers (flooding for unknown destinations)
        peers = self.db.get_online_nodes()
        result = []
        for p in peers:
            if p.node_id != self.node_id and msg.should_forward_to(p.node_id):
                result.append((p.node_id, 'forward'))
        return result

    def process_inbound(self, msg: MeshMessage) -> str:
        """Process received message"""
        _, to_node = parse_address(msg.to_addr)
        _, from_node = parse_address(msg.from_addr)

        # Check TTL
        if msg.is_expired():
            return "expired"

        # Check if we've seen this message before
        key = f"{msg.msg_id}:{self.node_id}"
        if self.db.is_seen(key):
            return "duplicate"

        # Mark as seen
        self.db.mark_seen(key)

        # Update forward history
        msg.fwd_history.append(self.node_id)

        # Save message
        self.db.save_message(msg)

        # Local delivery
        if not to_node or to_node == self.node_id:
            self._deliver_local(msg)
            return "delivered_local"

        # Check if we should forward
        if msg.should_forward_to(self.node_id):
            self._schedule_forward(msg)

        return "queued"

    def _deliver_local(self, msg: MeshMessage):
        """Deliver message to local inbox"""
        _, local_user = parse_address(msg.to_addr)

        if is_sysop_addr(msg.to_addr):
            # Sysop message
            pass

        elif is_bulletin_addr(msg.to_addr):
            # Bulletin - handled separately
            area = msg.to_addr.split('@')[0].replace('bulletin@', '').replace('#', '')
            self._save_bulletin(msg, area)

        else:
            # Personal message
            if local_user:
                entry = InboxEntry(
                    msg_id=msg.msg_id,
                    to_user=local_user,
                    received_at=int(time.time())
                )
                self.db.save_inbox_entry(entry)

                # Update user stats
                user = self.db.get_user(msg.to_addr)
                if user:
                    user.msg_received += 1
                    self.db.save_user(user)

    def _save_bulletin(self, msg: MeshMessage, area_id: str):
        """Save bulletin to area"""
        self.db.save_message(msg)

    def _schedule_forward(self, msg: MeshMessage):
        """Add message to forward queue for all relevant peers"""
        _, to_node = parse_address(msg.to_addr)

        peers = self.db.get_online_nodes()
        for peer in peers:
            if peer.node_id != self.node_id and msg.should_forward_to(peer.node_id):
                entry = QueueEntry(
                    msg_id=msg.msg_id,
                    dest_node=peer.node_id,
                    priority=msg.priority,
                    msg_type=msg.msg_type,
                    next_retry=int(time.time()),
                )
                self.db.queue_message(entry)

    # ─── Send Message ──────────────────────────────────────────

    def send(self, from_addr: str, to_addr: str, subject: str, body: str,
             msg_type: MessageType = MessageType.PERSONAL,
             thread_id: str = "", ref_msg_id: str = "") -> Optional[MeshMessage]:
        """Create and send a new message"""

        # Chunk body if needed
        chunks = self._chunk_body(body)
        first_msg_id = str(uuid.uuid4())

        for i, chunk_body in enumerate(chunks):
            msg = MeshMessage(
                msg_id=first_msg_id if i == 0 else str(uuid.uuid4()),
                from_addr=from_addr,
                to_addr=to_addr,
                msg_type=msg_type,
                subject=subject[:40],
                body=chunk_body,
                chunk=i,
                total_chunks=len(chunks),
                priority=Priority.HIGH if msg_type == MessageType.PERSONAL else Priority.LOW,
                ttl=DEFAULT_TTL_Bulletin if msg_type == MessageType.BULLETIN else DEFAULT_TTL_Personal,
                created_at=int(time.time()),
                fwd_history=[self.node_id],
                thread_id=thread_id,
                ref_msg_id=ref_msg_id,
            )

            self.db.save_message(msg)

            # Local delivery
            _, to_node = parse_address(to_addr)
            if not to_node or to_node == self.node_id:
                self._deliver_local(msg)
            else:
                self._schedule_forward(msg)

        # Trigger queue processing (only if we have a running loop)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._process_queue_once())
        except RuntimeError:
            pass  # No running loop (sync call)

        return self.db.get_message(first_msg_id)

    def _chunk_body(self, body: str) -> List[str]:
        """Split body into chunks"""
        chunks = []
        for i in range(0, len(body), MAX_BODY_LEN):
            chunks.append(body[i:i+MAX_BODY_LEN])
        return chunks if chunks else ['']

    # ─── Queue Processing ─────────────────────────────────────

    async def _process_queue_loop(self):
        """Background queue processor"""
        while self._running:
            try:
                await asyncio.sleep(10)
                await self._process_queue_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"queue processor error: {e}")

    async def _process_queue_once(self):
        """Process pending queue entries"""
        entries = self.db.get_pending_queue(limit=20)
        for entry in entries:
            await self._forward_entry(entry)

    async def _forward_entry(self, entry: QueueEntry):
        """Forward a single queue entry to its destination"""
        msg = self.db.get_message(entry.msg_id)
        if not msg:
            return

        peer = self.db.get_node(entry.dest_node)
        if not peer or peer.status != NodeStatus.ONLINE:
            entry.status = QueueStatus.PENDING
            entry.retry_backoff()
            self.db.queue_message(entry)
            return

        # Build packet
        packet = self._build_packet(msg)

        # Send via callback
        if 'packet' in self._callbacks:
            try:
                success = await self._callbacks['packet'](peer, packet)
                if success:
                    entry.status = QueueStatus.ACKED
                    peer.messages_sent += 1
                    peer.last_success = int(time.time())
                    self.db.save_node(peer)
                else:
                    entry.status = QueueStatus.FAILED
                    entry.last_error = "send failed"
                    entry.retry_backoff()
            except Exception as e:
                entry.status = QueueStatus.FAILED
                entry.last_error = str(e)
                entry.retry_backoff()
        else:
            entry.status = QueueStatus.ACKED  # No callback = direct call

        self.db.queue_message(entry)

    def _build_packet(self, msg: MeshMessage) -> dict:
        """Serialize message for MeshCore transport"""
        header = msg.to_header_dict()
        payload = msg.to_payload_dict()
        return {"header": header, "payload": payload}

    # ─── Peer Management ──────────────────────────────────────

    def register_peer(self, node_id: str, host: str, port: int = 5000) -> PeerNode:
        """Register a new peer node"""
        node = PeerNode(
            node_id=node_id,
            host=host,
            tcp_port=port,
            last_seen=int(time.time()),
            status=NodeStatus.ONLINE,
            capabilities=["meshmail-0.1"],
        )
        self.db.save_node(node)
        return node

    def touch_peer(self, node_id: str):
        """Mark peer as seen"""
        node = self.db.get_node(node_id)
        if node:
            node.touch()
            self.db.save_node(node)

    def set_peer_offline(self, node_id: str):
        """Mark peer as offline"""
        node = self.db.get_node(node_id)
        if node:
            node.status = NodeStatus.OFFLINE
            self.db.save_node(node)

    # ─── Stats ────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get mailbox statistics"""
        nodes = self.db.get_all_nodes()
        queue = self.db.get_pending_queue(limit=1000)
        messages = self.db.get_messages_to_addr(f"@{self.node_id}", include_deleted=True)

        return {
            "node_id": self.node_id,
            "total_nodes": len(nodes),
            "online_nodes": sum(1 for n in nodes if n.status == NodeStatus.ONLINE),
            "queue_size": len(queue),
            "total_messages": len(messages),
            "uptime": int(time.time()),
        }
