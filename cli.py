"""MeshMail CLI - klassische BBS-artige Bedienung"""
import sys
import readline
from typing import Optional, List
from .models import parse_address, is_bulletin_addr, MessageType
from .routing import RoutingEngine
from .store import Database
import time


class MeshMailCLI:
    """Interaktive CLI für MeshMail"""

    PROMPT = "MeshMail> "
    COMMANDS = {
        'L': 'Liste ungelesene Nachrichten',
        'LA': 'Liste alle Nachrichten',
        'R': 'Nachricht lesen (R <nr>)',
        'S': 'Nachricht senden (S user@node <betreff>)',
        'N': 'Neue Nachricht (interaktiv)',
        'D': 'Nachricht löschen (D <nr>)',
        'F': 'Weiterleiten (F <nr> user@node)',
        'RP': 'Antworten (RP <nr>)',
        'B': 'Bulletin-Bereiche',
        'BR': 'Bulletin lesen (BR <area>)',
        'BA': 'Ins Bulletin schreiben (BA <area>)',
        'Q': 'Queue-Status',
        'NODES': 'Bekannte Nodes',
        'STAT': 'Eigene Statistik',
        'SYNC': 'Synchronisation starten',
        'U': 'User-Profil',
        'H': 'Hilfe',
        'Q': 'Queue-Status',
        'EXIT': 'Beenden',
    }

    def __init__(self, db: Database, routing: RoutingEngine, current_user: str):
        self.db = db
        self.routing = routing
        self.current_user = current_user  # user@NODEID
        self._last_list: List[dict] = []

    def run(self):
        print("=== MeshMail CLI ===")
        print(f"Login: {self.current_user}")
        print("Befehle: H für Hilfe, EXIT zum Beenden\n")

        while True:
            try:
                cmd = input(self.PROMPT).strip()
                if not cmd:
                    continue

                # Handle EXIT
                if cmd.upper() in ('EXIT', 'QUIT', 'Q'):
                    print("Tschüss!")
                    break

                # Parse command
                parts = cmd.split(maxsplit=1)
                verb = parts[0].upper()
                args = parts[1] if len(parts) > 1 else ""

                self._handle_command(verb, args)

            except (EOFError, KeyboardInterrupt):
                print("\nTschüss!")
                break
            except Exception as e:
                print(f"Fehler: {e}")

    def _handle_command(self, verb: str, args: str):
        if verb == 'H':
            self._help()
        elif verb == 'L':
            self._list_unread()
        elif verb == 'LA':
            self._list_all(args)
        elif verb == 'R':
            self._read(args)
        elif verb == 'S':
            self._send(args)
        elif verb == 'N':
            self._new_message()
        elif verb == 'D':
            self._delete(args)
        elif verb == 'F':
            self._forward(args)
        elif verb == 'RP':
            self._reply(args)
        elif verb == 'B':
            self._bulletin_areas()
        elif verb == 'BR':
            self._bulletin_read(args)
        elif verb == 'BA':
            self._bulletin_write(args)
        elif verb == 'Q':
            self._queue_status()
        elif verb == 'NODES':
            self._nodes()
        elif verb == 'STAT':
            self._stats()
        elif verb == 'U':
            self._user_info()
        else:
            print(f"Unbekannter Befehl: {verb}. Tippe H für Hilfe.")

    def _list_unread(self):
        """Liste ungelesene Nachrichten"""
        entries = self.db.get_inbox(self._user_for_db(), include_read=False)
        self._last_list = entries

        if not entries:
            print("Keine ungelesenen Nachrichten.")
            return

        print(f"\n=== Ungelesene Nachrichten ({len(entries)}) ===")
        for i, e in enumerate(entries, 1):
            print(f"  {i}. {e['from_addr']:20s} | {e['subject'][:30]:30s}")
        print()

    def _list_all(self, args: str):
        """Liste alle Nachrichten"""
        limit = int(args.split()[0]) if args.strip() else 20
        entries = self.db.get_inbox(self._user_for_db(), include_read=True)[:limit]
        self._last_list = entries

        print(f"\n=== Nachrichten (letzte {limit}) ===")
        for i, e in enumerate(entries, 1):
            status = " " if e['is_read'] else "*"
            print(f"  {status}{i}. {e['from_addr']:20s} | {e['subject'][:30]:30s}")
        print()

    def _read(self, args: str):
        """Nachricht lesen"""
        try:
            nr = int(args.split()[0])
        except:
            print("Usage: R <nummer>")
            return

        if not self._last_list or nr < 1 or nr > len(self._last_list):
            print("Ungültige Nummer. Nutze L oder LA zuerst.")
            return

        entry = self._last_list[nr - 1]
        msg = self.db.get_message(entry['msg_id'])
        if not msg:
            print("Nachricht nicht gefunden.")
            return

        # Mark as read
        self.db.mark_read(entry['msg_id'], self._user_for_db())

        # Show
        print(f"\n=== Nachricht ===")
        print(f"Von:    {msg.from_addr}")
        print(f"An:     {msg.to_addr}")
        print(f"Betref: {msg.subject}")
        print(f"Datum:  {time.strftime('%d.%m.%Y %H:%M', time.localtime(msg.created_at))}")
        print(f"ID:     {msg.msg_id}")
        print()
        print(msg.body)
        print()

        # Reply prompt
        print("Antworten mit: RP", nr)

    def _send(self, args: str):
        """Nachricht senden"""
        parts = args.split(maxsplit=2)
        if len(parts) < 2:
            print("Usage: S user@node <betreff> [body]")
            return

        to_addr = parts[0]
        subject = parts[1][:40]
        body = parts[2] if len(parts) > 2 else ""

        if not body:
            print("Nachrichtentext (Enter für interaktiv):")
            body = input("> ").strip()
            if not body:
                print("Abgebrochen.")
                return

        result = self.routing.send(
            from_addr=self.current_user,
            to_addr=to_addr,
            subject=subject,
            body=body,
            msg_type=MessageType.PERSONAL
        )

        if result:
            print(f"Nachricht gesendet an {to_addr}")
        else:
            print("Fehler beim Senden.")

    def _new_message(self):
        """Interaktiv neue Nachricht erstellen"""
        to_addr = input("An (user@node): ").strip()
        if not to_addr:
            print("Abgebrochen.")
            return

        subject = input("Betreff: ").strip()[:40]
        if not subject:
            print("Abgebrochen.")
            return

        print("Nachricht (Enter für multiline, /END zum Beenden):")
        lines = []
        while True:
            line = input("> ")
            if line.strip() == "/END":
                break
            lines.append(line)

        body = "\n".join(lines)
        if not body:
            print("Abgebrochen.")
            return

        self._send(f"{to_addr} {subject} {body}")

    def _delete(self, args: str):
        """Nachricht löschen"""
        try:
            nr = int(args.split()[0])
        except:
            print("Usage: D <nummer>")
            return

        if not self._last_list or nr < 1 or nr > len(self._last_list):
            print("Ungültige Nummer.")
            return

        entry = self._last_list[nr - 1]
        self.db.delete_message(entry['msg_id'])
        print("Nachricht gelöscht.")

    def _forward(self, args: str):
        """Nachricht weiterleiten"""
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            print("Usage: F <nr> user@node")
            return

        try:
            nr = int(parts[0])
        except:
            print("Usage: F <nr> user@node")
            return

        if not self._last_list or nr < 1 or nr > len(self._last_list):
            print("Ungültige Nummer.")
            return

        entry = self._last_list[nr - 1]
        msg = self.db.get_message(entry['msg_id'])
        if not msg:
            print("Nachricht nicht gefunden.")
            return

        dest = parts[1]
        result = self.routing.send(
            from_addr=self.current_user,
            to_addr=dest,
            subject=f"FWD: {msg.subject}",
            body=f"--- Original von {msg.from_addr} ---\n{msg.body}",
            msg_type=MessageType.PERSONAL,
            ref_msg_id=msg.msg_id
        )

        if result:
            print(f"Weitergeleitet an {dest}")
        else:
            print("Fehler.")

    def _reply(self, args: str):
        """Auf Nachricht antworten"""
        try:
            nr = int(args.split()[0])
        except:
            print("Usage: RP <nr>")
            return

        if not self._last_list or nr < 1 or nr > len(self._last_list):
            print("Ungültige Nummer.")
            return

        entry = self._last_list[nr - 1]
        msg = self.db.get_message(entry['msg_id'])
        if not msg:
            print("Nachricht nicht gefunden.")
            return

        # Get original sender
        orig_from, orig_node = parse_address(msg.from_addr)
        reply_addr = f"{orig_from}@{orig_node}" if orig_node else orig_from

        print(f"Antworten an: {reply_addr}")
        print("Betreff:", f"RE: {msg.subject}"[:40])
        body = input("Nachricht (Enter für interaktiv): ").strip()
        if not body:
            print("Abgebrochen.")
            return

        result = self.routing.send(
            from_addr=self.current_user,
            to_addr=reply_addr,
            subject=f"RE: {msg.subject}"[:40],
            body=body,
            msg_type=MessageType.PERSONAL,
            thread_id=msg.msg_id,
            ref_msg_id=msg.msg_id
        )

        if result:
            print("Antwort gesendet.")
        else:
            print("Fehler beim Senden.")

    def _bulletin_areas(self):
        """Liste Bulletin-Bereiche"""
        boards = self.db.get_bulletin_areas()
        if not boards:
            print("Keine Bulletin-Bereiche konfiguriert.")
            return

        print("\n=== Bulletin-Bereiche ===")
        for b in boards:
            print(f"  {b.area_id:12s} | {b.area_name:20s} | {b.msg_count} msgs")
        print()

    def _bulletin_read(self, args: str):
        """Bulletin-Bereich lesen"""
        area = args.strip().upper()
        if not area:
            print("Usage: BR <area>")
            return

        addr = f"bulletin@{area}"
        msgs = self.db.get_messages_to_addr(addr, include_deleted=False)
        if not msgs:
            print(f"Bereich '{area}' ist leer.")
            return

        print(f"\n=== Bulletin {area} ({len(msgs)} msgs) ===")
        for i, m in enumerate(msgs[:20], 1):
            print(f"  {i}. {m.from_addr:20s} | {m.subject[:30]:30s}")
        print()

    def _bulletin_write(self, args: str):
        """Ins Bulletin schreiben"""
        area = args.strip().upper()
        if not area:
            print("Usage: BA <area>")
            return

        boards = self.db.get_bulletin_areas()
        if area not in [b.area_id for b in boards]:
            print(f"Bereich '{area}' existiert nicht. Nutze B für Liste.")
            return

        subject = input("Betreff: ").strip()[:40]
        if not subject:
            print("Abgebrochen.")
            return

        print("Text (Enter für multiline, /END zum Beenden):")
        lines = []
        while True:
            line = input("> ")
            if line.strip() == "/END":
                break
            lines.append(line)
        body = "\n".join(lines)
        if not body:
            print("Abgebrochen.")
            return

        result = self.routing.send(
            from_addr=self.current_user,
            to_addr=f"bulletin@{area}",
            subject=subject,
            body=body,
            msg_type=MessageType.BULLETIN
        )

        if result:
            print("Bulletin gepostet.")
        else:
            print("Fehler.")

    def _queue_status(self):
        """Zeige Forward-Queue Status"""
        pending = self.db.get_pending_queue(limit=50)
        if not pending:
            print("Queue ist leer.")
            return

        print(f"\n=== Forward-Queue ({len(pending)} Einträge) ===")
        for e in pending:
            status_map = {0: 'PENDING', 1: 'SENDING', 2: 'ACKED', 3: 'FAILED'}
            print(f"  {e.msg_id[:8]:10s} -> {e.dest_node:10s} | {status_map.get(e.status.value, '?')} | Attempts: {e.attempts}")
        print()

    def _nodes(self):
        """Zeige bekannte Nodes"""
        nodes = self.db.get_all_nodes()
        if not nodes:
            print("Keine bekannten Nodes.")
            return

        print("\n=== Nodes ===")
        for n in nodes:
            status_map = {1: 'ONLINE', 2: 'OFFLINE', 3: 'STALE'}
            last = time.strftime('%d.%m %H:%M', time.localtime(n.last_seen)) if n.last_seen else "nie"
            print(f"  {n.node_id:10s} | {status_map.get(n.status.value, '?'):8s} | {n.host}:{n.tcp_port} | Zuletzt: {last}")
        print()

    def _stats(self):
        """Zeige eigene Statistik"""
        stats = self.routing.get_stats()
        user = self.db.get_user(self.current_user)

        print("\n=== MeshMail Statistik ===")
        print(f"  Node:       {stats['node_id']}")
        print(f"  Messages:   {stats['total_messages']}")
        print(f"  Nodes:      {stats['online_nodes']}/{stats['total_nodes']} online")
        print(f"  Queue:      {stats['queue_size']} Einträge")
        if user:
            print(f"  Gesendet:   {user.msg_sent}")
            print(f"  Empfangen:  {user.msg_received}")
        print()

    def _user_info(self):
        """Zeige User-Profil"""
        user = self.db.get_user(self.current_user)
        if not user:
            print("User nicht gefunden.")
            return

        print(f"\n=== User: {user.user_id} ===")
        print(f"  Display:    {user.display_name}")
        print(f"  Sysop:      {'Ja' if user.is_sysop else 'Nein'}")
        print(f"  Geblockt:   {'Ja' if user.is_blocked else 'Nein'}")
        print(f"  Member:     {time.strftime('%d.%m.%Y', time.localtime(user.created_at))}")
        print(f"  Gesendet:   {user.msg_sent}")
        print(f"  Empfangen:  {user.msg_received}")
        print()

    def _help(self):
        """Zeige Hilfe"""
        print("\n=== MeshMail Hilfe ===")
        for cmd, desc in self.COMMANDS.items():
            print(f"  {cmd:6s} - {desc}")
        print()
        print("Adressen: user@NODEID  |  bulletin@AREA  |  sysop@NODEID")
        print()


class MailboxConfig:
    """Configuration for MeshMail"""
    def __init__(self, node_id: str, tcp_host: str = "YOUR-ESP32-IP", tcp_port: int = 5000,
                 db_path: str = "/var/lib/meshmail/meshmail.db",
                 default_user: str = "sysop"):
        self.node_id = node_id
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.db_path = db_path
        self.default_user = default_user