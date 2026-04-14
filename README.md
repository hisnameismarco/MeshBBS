# MeshMail v0.3 — MeshCore BBS

**Natives Mailbox-System für MeshCore BBS über LoRa/Telnet**

## Funktionen

- **PING-Befehl** auf jedem MeshCore-Kanal → PONG-Antwort mit Maidenhead-Grid (📍KJ2247), Hop-Count (⏱Nh) und Latenz (Ns)
- **TEST-Befehl** → DM-Antwort "angekommen in Coswig-Anhalt"
- **DM-Befehle**: `!HELP`, `!STAT`, `!INBOX`, `!MSG`, `!WHOAMI`, `!NODES`, `!PING`, `!ECHO`, `!SELFTEST` usw.
- **DiagBot**: Systemdiagnose (PING, ECHO, SELFTEST, STATUS, QUEUES, PEERS, LASTSYNC)
- **Rate-Limiting**: 10 Befehle/Min pro Absender
- **Kanal-Handling**: Groß-/Kleinschreibung ignorieren (ping/PING/Ping funktionieren alle)

## Installation

```bash
# Auf dem MeshCore-Node (ESP32):
# Klone nach /opt/meshmail/
git clone https://github.com/hisnameismarco/meshmail.git /opt/meshmail

# Venv mit meshcore-Bibliothek erstellen
python3 -m venv /opt/meshmail-venv
source /opt/meshmail-venv/bin/activate
pip install meshcore

# Starten
python3 /opt/meshmail/main.py
```

Oder als systemd-Service:
```bash
cp meshmail/meshmail.service /etc/systemd/system/
systemctl enable meshmail
systemctl start meshmail
```

## Konfiguration

Umgebungsvariablen in `config.env` setzen:
- `MESHMAIL_NODE_ID` — Deine MeshCore-Node-ID (Standard: DE-ST-COSWIG-MARCO)
- `MESHMAIL_TCP_HOST` — ESP32 MeshCore IP (Standard: 192.168.2.30)
- `MESHMAIL_TCP_PORT` — ESP32 MeshCore TCP-Port (Standard: 5000)

## PONG-Antwortformat

```
@DE-ST-COSWIG-MOBIL PONG 📍KJ2247 ⏱1hops 7s
```

- `@<sender>` — Erwähnung des Absenders
- `PONG` — Antwort-Kennung
- `📍KJ2247` — Maidenhead Grid Square dieses Nodes
- `⏱1hops` — Hop-Anzahl vom Absender zu diesem Node
- `7s` — Latenz in Sekunden

## MeshCore-Verbindung

MeshMail verbindet sich zum ESP32 MeshCore-Knoten via TCP (MeshCore Bridge). Die Bridge kümmert sich um:
- DM-Routing (contact_msg)
- Kanal-Nachrichten-Routing (channel_msg_recv)
- Automatische Wieder Verbindung
- Nachrichten-Warteschlange mit exponentieller Wartezeit

## Architektur

Siehe [ARCHITECTURE.md](ARCHITECTURE.md) für die vollständige Design-Dokumentation.

## Tech-Stack

- Python 3.12
- meshcore 2.3.6 (MeshCore LoRa-Bibliothek)
- SQLite (für persistente Speicherung)
- systemd (für Service-Management)

## Lizenz

MIT