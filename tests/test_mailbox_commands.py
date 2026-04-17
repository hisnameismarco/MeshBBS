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
main_mod = _load_module("main")


class _Cfg:
    def __init__(self, node_id: str, db_path: str):
        self.node_id = node_id
        self.db_path = db_path


class _BBS:
    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.routing = None
        self.mc_bridge = None


class MailboxCommandTests(unittest.TestCase):
    def setUp(self):
        main_mod.BBS_COMMANDS.clear()
        main_mod._setup_bbs_commands()

    def test_parse_msg_extended_pipe_format(self):
        user, node, subject, body = main_mod._parse_msg_args(
            "@sysop@DE-NODE | Hallo Welt | Dies ist ein Test", "LOCALNODE"
        )
        self.assertEqual(user, "sysop")
        self.assertEqual(node, "DE-NODE")
        self.assertEqual(subject, "Hallo Welt")
        self.assertEqual(body, "Dies ist ein Test")

    def test_msg_command_uses_open_database_handle(self):
        with tempfile.TemporaryDirectory() as td:
            db = store_mod.Database(str(Path(td) / "mesh.db"))
            bbs = _BBS(db=db, config=_Cfg(node_id="NODE1", db_path="/path/that/does/not/exist.db"))
            try:
                out = main_mod.BBS_COMMANDS["MSG"](bbs, "abcdef123456", "@bob hello there")
                self.assertIn("Message sent", out)
                msgs = db.get_messages_to_addr("bob@NODE1", include_deleted=True)
                self.assertEqual(len(msgs), 1)
            finally:
                db.close()

    def test_get_inbox_fallback_does_not_leak_other_local_mailboxes(self):
        with tempfile.TemporaryDirectory() as td:
            db = store_mod.Database(str(Path(td) / "mesh.db"))
            try:
                now = 1234567890
                msg = models.MeshMessage(
                    msg_id="m1",
                    from_addr="alice@NODE1",
                    to_addr="victim@NODE1",
                    msg_type=models.MessageType.PERSONAL,
                    subject="secret",
                    body="hidden",
                    created_at=now,
                    status=models.MessageStatus.LOCAL,
                )
                db.save_message(msg)
                db.save_inbox_entry(models.InboxEntry(msg_id="m1", to_user="victim", received_at=now))

                leaked = db.get_inbox("attacker", include_read=True, node_id="NODE1")
                self.assertEqual(leaked, [])
            finally:
                db.close()

    def test_delete_command_marks_message_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            db = store_mod.Database(str(Path(td) / "mesh.db"))
            bbs = _BBS(db=db, config=_Cfg(node_id="NODE1", db_path=str(Path(td) / "mesh.db")))
            try:
                send = main_mod.BBS_COMMANDS["MSG"](bbs, "abcdef123456", "@abcdef12 hi test-body")
                self.assertIn("Message sent", send)

                deleted = main_mod.BBS_COMMANDS["DELETE"](bbs, "abcdef123456", "1")
                self.assertIn("Deleted message #1", deleted)

                inbox = db.get_inbox("abcdef12", include_read=True, node_id="NODE1")
                self.assertEqual(inbox, [])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
