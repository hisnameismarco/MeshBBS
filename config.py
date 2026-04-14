"""MeshBBS Konfiguration"""
import os
from pathlib import Path


class MeshBBSConfig:
    """Zentrale Konfiguration für MeshBBS"""

    DEFAULTS = {
        "node_id": "LOCALNODE",
        "tcp_host": "YOUR-ESP32-IP",
        "tcp_port": 5000,
        "db_path": "/var/lib/meshmail/MeshBBS.db",
        "log_path": "/var/log/MeshBBS/MeshBBS.log",
        "sync_interval": 300,
        "queue_interval": 30,
        "max_body_size": 4096,
        "default_user": "sysop",
        "listen_host": "0.0.0.0",
        "listen_port": 7800,
        "location": "angekommen in DEINE-REGION",
    }

    def __init__(self, **overrides):
        # Load from env + defaults + overrides
        for key, default in self.DEFAULTS.items():
            setattr(self, key, os.environ.get(f"MESHMAIL_{key.upper()}", default))

        for key, val in overrides.items():
            setattr(self, key, val)

        # Ensure types
        self.tcp_port = int(self.tcp_port)
        self.sync_interval = int(self.sync_interval)
        self.queue_interval = int(self.queue_interval)
        self.max_body_size = int(self.max_body_size)
        self.listen_port = int(self.listen_port)

    def node_addr(self, user: str = "") -> str:
        if user:
            return f"{user}@{self.node_id}"
        return f"@{self.node_id}"
