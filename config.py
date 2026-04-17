"""MeshBBS Konfiguration"""
import os
from typing import Optional


class MeshMailConfig:
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

        "location": "angekommen in DEINE-REGION",
        "latitude": "",
        "longitude": "",
        "auto_finger_enabled": "1",
        "auto_finger_interval": 900,
        "auto_finger_channel": 1,
        "discovery_enabled": "1",
        "discovery_interval": 120,
        "discovery_channel": 0,
        "presence_enabled": "1",
        "presence_interval": 120,
        "presence_channel": 0,
        "presence_timeout": 600,
        "retention_days": 30,
        "retention_interval": 3600,
    }

    def __init__(self, **overrides):
        # Load from env + defaults + overrides
        for key, default in self.DEFAULTS.items():
            setattr(self, key, os.environ.get(f"MESHMAIL_{key.upper()}", default))

        for key, val in overrides.items():
            setattr(self, key, val)

        # Ensure types
        self.tcp_port = self._as_int("tcp_port", self.tcp_port)
        self.sync_interval = self._as_int("sync_interval", self.sync_interval)
        self.queue_interval = self._as_int("queue_interval", self.queue_interval)
        self.max_body_size = self._as_int("max_body_size", self.max_body_size)
        self.latitude = self._as_optional_float("latitude", self.latitude)
        self.longitude = self._as_optional_float("longitude", self.longitude)
        self.auto_finger_enabled = self._as_bool("auto_finger_enabled", self.auto_finger_enabled)
        self.auto_finger_interval = self._as_int("auto_finger_interval", self.auto_finger_interval)
        self.auto_finger_channel = self._as_int("auto_finger_channel", self.auto_finger_channel)
        self.discovery_enabled = self._as_bool("discovery_enabled", self.discovery_enabled)
        self.discovery_interval = self._as_int("discovery_interval", self.discovery_interval)
        self.discovery_channel = self._as_int("discovery_channel", self.discovery_channel)
        self.presence_enabled = self._as_bool("presence_enabled", self.presence_enabled)
        self.presence_interval = self._as_int("presence_interval", self.presence_interval)
        self.presence_channel = self._as_int("presence_channel", self.presence_channel)
        self.presence_timeout = self._as_int("presence_timeout", self.presence_timeout)
        self.retention_days = self._as_int("retention_days", self.retention_days)
        self.retention_interval = self._as_int("retention_interval", self.retention_interval)

    @staticmethod
    def _as_int(name: str, value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid configuration: {name} must be an integer")

    @staticmethod
    def _as_optional_float(name: str, value) -> Optional[float]:
        if value is None:
            return None
        raw = str(value).strip()
        if raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid configuration: {name} must be a float")

    @staticmethod
    def _as_bool(name: str, value) -> bool:
        if isinstance(value, bool):
            return value
        raw = str(value).strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off", ""}:
            return False
        raise ValueError(f"Invalid configuration: {name} must be a boolean-like value")

    def node_addr(self, user: str = "") -> str:
        if user:
            return f"{user}@{self.node_id}"
        return f"@{self.node_id}"


# Backward-compatible name expected by main.py and legacy imports.
MeshBBSConfig = MeshMailConfig
