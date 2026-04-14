#!/usr/bin/env python3
"""Apply fresh-connection fix to meshmail/main.py"""
import re

with open('/root/.openclaw/workspace/meshmail/main.py') as f:
    content = f.read()

# Find and replace _send_msg
pattern = r'    def _send_msg\(self, user_addr: str, args: str\) -> str:.*?(?=\n    def _\w+)'
replacement = '''    def _send_msg(self, user_addr: str, args: str) -> str:
        parts = args.split(maxsplit=2)
        if len(parts) < 2:
            return "Usage: S user@node <betreff> [text]\\r\\n"
        to_addr = parts[0]
        subject = parts[1][:40]
        body = parts[2] if len(parts) > 2 else ""
        # Use fresh connection to avoid DB lock with shared self.db
        import sqlite3, time, uuid
        from meshmail.models import MessageStatus, parse_address
        try:
            db = sqlite3.connect(self.config.db_path, isolation_level=None)
            msg_id = str(uuid.uuid4())
            now = int(time.time())
            db.execute("""INSERT INTO messages
                (msg_id, from_addr, to_addr, msg_type, subject, body, chunk,
                 total_chunks, priority, ttl, hops, created_at, received_at,
                 status, fwd_history, signature, thread_id, ref_msg_id, chunk_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (msg_id, user_addr, to_addr, 1, subject, body, 0, 0, 2, 7, 0,
                 now, 0, MessageStatus.LOCAL.value, "[]", None, "", "", "[]"))
            _, to_node = parse_address(to_addr)
            if not to_node or to_node == self.config.node_id:
                local_user = to_addr.split("@")[0]
                db.execute("""INSERT INTO inbox
                    (msg_id, to_user, is_read, is_deleted, received_at, read_at)
                    VALUES (?, ?, 0, 0, ?, 0)""",
                    (msg_id, local_user, now))
            db.close()
            return f"Gesendet an {to_addr}.\\r\\n"
        except Exception as e:
            import sys
            print(f"_send_msg error: {e}", file=sys.stderr)
            try: db.close()
            except: pass
            return "Fehler beim Senden.\\r\\n"
'''

new_content = re.sub(pattern, replacement, content, count=1, flags=re.DOTALL)
with open('/root/.openclaw/workspace/meshmail/main.py', 'w') as f:
    f.write(new_content)
print('Fixed local main.py')
