"""
MeshCore Interface for MeshBBS using the meshcore library.
Uses MeshCore.create_tcp() for proper TCP connection (same pattern as MeshBBS TCPMeshCoreConnection).
"""
import asyncio
import logging
import queue
from typing import Optional, Callable

try:
    from meshcore import MeshCore, EventType
    MESHcore_AVAILABLE = True
except ImportError:
    MESHcore_AVAILABLE = False

log = logging.getLogger("MeshBBS.mc")


class MeshCoreBridge:
    """Connect MeshBBS to MeshCore via TCP using proper meshcore library."""

    def __init__(self, host: str = "YOUR-ESP32-IP", port: int = 5000,
                 node_id: str = "BBSCOSWIG",
                 on_dm_received: Optional[Callable] = None,
                 on_channel_message: Optional[Callable] = None):
        self.host = host
        self.port = port
        self.node_id = node_id
        self.on_dm_received = on_dm_received
        self.on_channel_message = on_channel_message

        self._mc: Optional[MeshCore] = None
        self._running = False
        self._subscription = None
        self._channel_subscription = None
        self._auto_fetch_running = False
        self._outgoing_queue: queue.Queue = queue.Queue()

    def _on_dm(self, event) -> None:
        """Handle incoming direct message."""
        try:
            payload = event.payload or {}
            text = payload.get("text", "").strip()
            from_pubkey = payload.get("pubkey_prefix", "?")
            if isinstance(from_pubkey, bytes):
                from_pubkey = from_pubkey.hex()
            if not text or not from_pubkey:
                return

            log.info(f"[MC<-] DM from {from_pubkey[:12]}: {text[:60]}")

            if self.on_dm_received:
                try:
                    response = self.on_dm_received(from_pubkey, text)
                except Exception as e:
                    response = f"BBS Error: {e}"
            else:
                response = "MeshBBS BBS | telnet YOUR-SERVER-IP 7800"

            # Queue response for sending via sender loop (thread-safe)
            if response:
                self._outgoing_queue.put((from_pubkey, response))

        except Exception as e:
            log.error(f"DM handler error: {e}")

    def _on_channel(self, event) -> None:
        """Handle incoming channel message (e.g. PING/TEST on a channel)."""
        try:
            payload = event.payload or {}
            text = payload.get("text", "").strip()
            channel_idx = payload.get("channel_idx", -1)
            txt_type = payload.get("txt_type", 0)
            sender_ts = payload.get("sender_timestamp", 0)
            rssi = payload.get("RSSI", None)
            snr = payload.get("SNR", None)
            from_pubkey = payload.get("pubkey_prefix", "?")
            if isinstance(from_pubkey, bytes):
                from_pubkey = from_pubkey.hex()
            path_len = payload.get("path_len", 0)

            if not text:
                return

            log.info(f"[MC<-] CHAN #{channel_idx} text={text[:40]} SNR={snr} RSSI={rssi} hops={path_len} from={from_pubkey[:8]}")

            # Route channel commands via callback if registered
            if self.on_channel_message:
                try:
                    self.on_channel_message(channel_idx, text, sender_ts, rssi, snr, from_pubkey=from_pubkey, hops=path_len)
                except Exception as e:
                    log.error(f"Channel handler error: {e}")

        except Exception as e:
            log.error(f"Channel handler error: {e}")

    async def _sender_loop(self):
        """Background loop sending queued messages (DM + channel)."""
        while self._running:
            try:
                if not self._outgoing_queue.empty():
                    dest, text = self._outgoing_queue.get(timeout=0.5)
                    if self._mc and self._auto_fetch_running:
                        try:
                            if isinstance(dest, str) and dest.startswith("CHAN:"):
                                # Channel message
                                channel_idx = int(dest[5:])
                                await self._mc.commands.send_chan_msg(channel_idx, text)
                                log.info(f"[MC->] Sent CHAN #{channel_idx}: {text[:40]}")
                            else:
                                # DM
                                await self._mc.commands.send_msg(dest, text)
                                log.info(f"[MC->] Sent DM to {dest[:12]}")
                        except Exception as e:
                            log.error(f"Send error: {e}")
                else:
                    await asyncio.sleep(0.3)
            except queue.Empty:
                await asyncio.sleep(0.1)
            except Exception as e:
                log.error(f"Sender loop error: {e}")
                await asyncio.sleep(1)

    async def connect(self) -> bool:
        """Async connect to MeshCore."""
        if not MESHcore_AVAILABLE:
            log.error("meshcore library not available!")
            return False

        # Clean up any stale connection state
        if self._mc:
            try:
                if self._auto_fetch_running:
                    await self._mc.stop_auto_message_fetching()
            except Exception:
                pass
            try:
                await self._mc.disconnect()
            except Exception:
                pass
            self._mc = None
            self._auto_fetch_running = False
            self._subscription = None
            self._channel_subscription = None

        try:
            log.info(f"Connecting to MeshCore TCP {self.host}:{self.port}...")
            self._mc = await MeshCore.create_tcp(self.host, self.port)

            if self._mc.self_info:
                name = self._mc.self_info.get("name", "?")
                pk = self._mc.self_info.get("pubkey", b"")
                if isinstance(pk, bytes):
                    pk = pk.hex()
                log.info(f"MeshCore connected: {name} ({pk[:12]}...)")
            else:
                log.warning("MeshCore connected but no self_info")

            self._subscription = self._mc.subscribe(
                EventType.CONTACT_MSG_RECV, self._on_dm)
            self._channel_subscription = self._mc.subscribe(
                EventType.CHANNEL_MSG_RECV, self._on_channel)

            await self._mc.start_auto_message_fetching()
            self._auto_fetch_running = True
            self._running = True
            log.info("MeshCore subscriptions active")
            return True

        except Exception as e:
            log.error(f"MeshCore connect error: {e}")
            self._mc = None
            return False

    async def run(self):
        """Run sender loop + auto-reconnect watchdog."""
        sender = asyncio.create_task(self._sender_loop())
        reconnect_delay = 10
        max_reconnect_delay = 120
        try:
            while self._running:
                await asyncio.sleep(15)
                if not self.is_connected() and self._running:
                    log.warning(f"MeshCore connection lost, reconnecting in {reconnect_delay}s...")
                    for attempt in range(5):
                        if await self.connect():
                            log.info(f"Reconnected after {attempt+1} attempt(s)")
                            reconnect_delay = 10
                            break
                        log.error(f"Reconnect attempt {attempt+1}/5 failed, retry in {reconnect_delay}s")
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    else:
                        log.error("Max reconnect attempts reached, giving up")
                        self._running = False
                        break
        finally:
            sender.cancel()
            try:
                await sender
            except asyncio.CancelledError:
                pass

    async def disconnect(self):
        """Disconnect from MeshCore."""
        self._running = False
        if self._mc:
            try:
                if self._auto_fetch_running:
                    await self._mc.stop_auto_message_fetching()
                if self._subscription:
                    self._mc.unsubscribe(self._subscription)
                if self._channel_subscription:
                    self._mc.unsubscribe(self._channel_subscription)
                await self._mc.disconnect()
            except Exception as e:
                log.error(f"Disconnect error: {e}")
        log.info("MeshCore bridge disconnected")

    def is_connected(self) -> bool:
        return self._mc is not None and self._auto_fetch_running

    def send_dm(self, dest_pubkey: str, text: str):
        """Queue a DM for sending (thread-safe)."""
        self._outgoing_queue.put((dest_pubkey, text))

    def send_channel_message(self, channel_idx: int, text: str):
        """Queue a channel message for sending (thread-safe)."""
        self._outgoing_queue.put(("CHAN:%d" % channel_idx, text))
