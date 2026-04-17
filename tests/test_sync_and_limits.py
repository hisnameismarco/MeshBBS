import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ensure_package():
    if "MeshBBS" not in sys.modules:
        pkg = types.ModuleType("MeshBBS")
        pkg.__path__ = [str(ROOT)]
        sys.modules["MeshBBS"] = pkg


def _load_module(name: str):
    _ensure_package()
    fq_name = f"MeshBBS.{name}"
    if fq_name in sys.modules:
        return sys.modules[fq_name]
    spec = importlib.util.spec_from_file_location(fq_name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[fq_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


models = _load_module("models")
store_mod = _load_module("store")
sync_mod = _load_module("sync")
main_mod = _load_module("main")
diagbot_mod = _load_module("diagbot")


class _Cfg:
    def __init__(self, node_id: str, db_path: str):
        self.node_id = node_id
        self.db_path = db_path
        self.location = "angekommen in TEST-REGION"
        self.latitude = 51.898458
        self.longitude = 12.464044


class _BridgeStub:
    def __init__(self):
        self.dm = []
        self.chan = []

    def send_dm(self, dest, text):
        self.dm.append((dest, text))

    def send_channel_message(self, channel_idx, text):
        self.chan.append((channel_idx, text))


class _RoutingStub:
    def process_inbound(self, msg):
        return "stored"


class SyncAndLimitTests(unittest.TestCase):
    def test_process_ack_removes_specific_peer_entry(self):
        with tempfile.TemporaryDirectory() as td:
            db = store_mod.Database(str(Path(td) / "mesh.db"))
            try:
                db.queue_message(models.QueueEntry(msg_id="m1", dest_node="NODEA"))
                proto = sync_mod.SyncProtocol(db=db, routing=_RoutingStub(), node_id="LOCAL")
                ok = proto.process_ack("m1", "NODEA")
                self.assertTrue(ok)
                rows = db.conn.execute("SELECT COUNT(*) FROM forward_queue WHERE msg_id = ?", ("m1",)).fetchone()
                self.assertEqual(rows[0], 0)
            finally:
                db.close()

    def test_process_ack_without_peer_removes_all_entries(self):
        with tempfile.TemporaryDirectory() as td:
            db = store_mod.Database(str(Path(td) / "mesh.db"))
            try:
                db.queue_message(models.QueueEntry(msg_id="m2", dest_node="NODEA"))
                db.queue_message(models.QueueEntry(msg_id="m2", dest_node="NODEB"))
                proto = sync_mod.SyncProtocol(db=db, routing=_RoutingStub(), node_id="LOCAL")
                ok = proto.process_ack("m2")
                self.assertTrue(ok)
                rows = db.conn.execute("SELECT COUNT(*) FROM forward_queue WHERE msg_id = ?", ("m2",)).fetchone()
                self.assertEqual(rows[0], 0)
            finally:
                db.close()

    def test_meshcore_dm_length_limit(self):
        main_mod.BBS_COMMANDS.clear()
        main_mod._setup_bbs_commands()
        srv = main_mod.MeshBBSServer(_Cfg(node_id="NODE1", db_path="/tmp/mesh.db"))
        srv.diagbot = None

        long_cmd = "!ECHO " + ("x" * 1100)
        out = srv._handle_meshcore_dm("abcdef123456", long_cmd)
        self.assertIn("DM too long", out)

    def test_diag_echo_is_bounded(self):
        out = diagbot_mod._cmd_echo_direct("x" * 1000)
        self.assertEqual(len(out), diagbot_mod.MAX_ECHO_LEN)

    def test_channel_test_uses_config_location_and_sanitizes_sender_name(self):
        srv = main_mod.MeshBBSServer(_Cfg(node_id="NODE1", db_path="/tmp/mesh.db"))
        srv.mc_bridge = _BridgeStub()

        # Invalid display name should be stripped from mention.
        srv._handle_meshcore_channel(
            channel_idx=2,
            text="bad name!: test",
            sender_ts=0,
            rssi=0,
            snr=0,
            from_pubkey="deadbeef",
            hops=0,
        )
        self.assertTrue(srv.mc_bridge.chan)
        _, payload = srv.mc_bridge.chan[-1]
        self.assertIn("angekommen in TEST-REGION", payload)
        self.assertFalse(payload.startswith("@"))

    def test_channel_ping_uses_config_coordinates_for_grid(self):
        cfg = _Cfg(node_id="NODE1", db_path="/tmp/mesh.db")
        srv = main_mod.MeshBBSServer(cfg)
        srv.mc_bridge = _BridgeStub()
        expected_grid = main_mod._maidenhead_6(cfg.latitude, cfg.longitude)

        srv._handle_meshcore_channel(
            channel_idx=1,
            text="PING",
            sender_ts=0,
            rssi=0,
            snr=0,
            from_pubkey="deadbeef",
            hops=0,
        )
        self.assertTrue(srv.mc_bridge.chan)
        _, payload = srv.mc_bridge.chan[-1]
        self.assertIn(expected_grid, payload)


if __name__ == "__main__":
    unittest.main()
