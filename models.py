"""Core data structures for MeshBBS"""
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Set, List, Dict
import time


class MessageType(IntEnum):
    PERSONAL = 1
    BULLETIN = 2
    SYSTEM = 3
    ACK = 4
    POLL = 5
    NDR = 6  # No Delivery Report


class Priority(IntEnum):
    HIGH = 1
    NORMAL = 2
    LOW = 3


class MessageStatus(IntEnum):
    QUEUED = 0
    FORWARDING = 1
    DELIVERED = 2
    FAILED = 3
    EXPIRED = 4
    LOCAL = 5


class QueueStatus(IntEnum):
    PENDING = 0
    SENDING = 1
    ACKED = 2
    FAILED = 3


class NodeStatus(IntEnum):
    ONLINE = 1
    OFFLINE = 2
    STALE = 3


class ReplicationLevel(IntEnum):
    ALL = 1   # Replicate to all bulletin nodes
    REGION = 2  # Only region-nodes
    NONE = 3  # Local only


@dataclass
class MeshMessage:
    """Core message object"""
    msg_id: str           # UUIDv4, global unique
    from_addr: str        # user@NODEID
    to_addr: str           # user@NODEID or bulletin@AREA
    msg_type: MessageType
    subject: str           # max 40 ASCII chars
    body: str              # max 512 bytes per chunk
    chunk: int = 0        # 0 = no chunking
    total_chunks: int = 0
    priority: Priority = Priority.NORMAL
    ttl: int = 7          # max hops
    hops: int = 0
    created_at: int = field(default_factory=lambda: int(time.time()))
    received_at: int = 0
    status: MessageStatus = MessageStatus.QUEUED
    fwd_history: List[str] = field(default_factory=list)
    signature: Optional[bytes] = None
    thread_id: str = ""
    ref_msg_id: str = ""
    # Metadata for chunked messages
    chunk_ids: List[str] = field(default_factory=list)

    def to_header_dict(self) -> dict:
        """Serialize header for MeshCore transport"""
        return {
            "ver": 1,
            "type": self.msg_type,
            "pri": self.priority,
            "ttl": self.ttl,
            "hops": self.hops,
            "chunks": self.total_chunks,
            "chunk": self.chunk,
            "from": self.from_addr,
            "to": self.to_addr,
            "id": self.msg_id,
        }

    def is_expired(self) -> bool:
        return self.hops >= self.ttl

    def should_forward_to(self, node_id: str) -> bool:
        """Check if message should be forwarded to specific node"""
        if node_id in self.fwd_history:
            return False  # Loop protection
        if self.msg_type == MessageType.SYSTEM:
            return False  # System messages not forwarded
        return True

    def update_after_hop(self):
        """Called after each hop"""
        self.hops += 1

    def to_payload_dict(self) -> dict:
        """Serialize body for transport"""
        return {
            "id": self.msg_id,
            "from": self.from_addr,
            "to": self.to_addr,
            "type": self.msg_type,
            "subj": self.subject,
            "body": self.body,
            "time": self.created_at,
            "thread": self.thread_id,
            "ref": self.ref_msg_id,
            "sig": self.signature.decode() if self.signature else None,
        }


@dataclass
class MailboxUser:
    """User account"""
    user_id: str          # user@NODEID
    password_hash: str    # argon2
    display_name: str
    is_sysop: bool = False
    is_blocked: bool = False
    created_at: int = field(default_factory=lambda: int(time.time()))
    last_login: int = 0
    msg_sent: int = 0
    msg_received: int = 0
    read_msg_ids: Set[str] = field(default_factory=set)
    last_read_check: int = 0

    def can_send(self) -> bool:
        return not self.is_blocked

    def address(self) -> str:
        return self.user_id


@dataclass
class BulletinBoard:
    """Bulletin area"""
    area_id: str          # ALLGEMEIN, TECHNIK, REGION, SYSOP
    area_name: str
    description: str
    is_public: bool = True
    replication: ReplicationLevel = ReplicationLevel.ALL
    created_by: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    msg_count: int = 0
    last_post: int = 0


@dataclass
class PeerNode:
    """Known neighbor node"""
    node_id: str          # 8-char MeshCore ID
    host: str             # IP:port
    tcp_port: int = 5000
    last_seen: int = 0
    last_poll: int = 0
    status: NodeStatus = NodeStatus.OFFLINE
    msg_seq: int = 0      # Last known message sequence
    bulletins: Set[str] = field(default_factory=set)  # subscribed areas
    capabilities: List[str] = field(default_factory=list)
    # Stats
    messages_sent: int = 0
    messages_received: int = 0
    last_success: int = 0

    def touch(self):
        self.last_seen = int(time.time())
        self.status = NodeStatus.ONLINE


@dataclass
class QueueEntry:
    """Forward queue entry"""
    msg_id: str
    dest_node: str
    chunk_idx: int = 0
    attempts: int = 0
    last_attempt: int = 0
    next_retry: int = 0
    status: QueueStatus = QueueStatus.PENDING
    msg_type: MessageType = MessageType.PERSONAL
    priority: Priority = Priority.NORMAL
    last_error: str = ""

    def should_retry(self) -> bool:
        if self.status == QueueStatus.ACKED:
            return False
        return int(time.time()) >= self.next_retry

    def retry_backoff(self):
        """Exponential backoff"""
        self.attempts += 1
        self.last_attempt = int(time.time())
        self.next_retry = self.last_attempt + min(300, 2 ** self.attempts)


@dataclass
class InboxEntry:
    """User inbox entry"""
    msg_id: str
    to_user: str          # local user part
    is_read: bool = False
    is_deleted: bool = False
    received_at: int = field(default_factory=lambda: int(time.time()))
    read_at: int = 0


# Constants
MAX_SUBJECT_LEN = 40
MAX_BODY_LEN = 256       # per chunk
MAX_CHUNKS = 32
DEFAULT_TTL_Personal = 7
DEFAULT_TTL_Bulletin = 15
DEFAULT_TTL_System = 3
DEDUP_CACHE_SIZE = 5000
QUEUE_MAX_SIZE = 200
MAX_HOPS = 15


def parse_address(addr: str) -> tuple:
    """Parse address into user, node parts"""
    if '@' in addr:
        user, node = addr.rsplit('@', 1)
        return user, node
    return addr, ""


def format_address(user: str, node: str) -> str:
    """Format address string"""
    if node:
        return f"{user}@{node}"
    return user


def is_bulletin_addr(addr: str) -> bool:
    """Check if address is a bulletin"""
    return addr.startswith('bulletin@') or addr.startswith('#')


def is_sysop_addr(addr: str) -> bool:
    """Check if address is sysop"""
    return addr.startswith('sysop@')