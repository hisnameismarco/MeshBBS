# MeshBBS v0.3 — MeshCore BBS

**Natives Mailbox-System für MeshCore BBS über LoRa/Telnet**

![MeshBBS Cover](meshmail_cover.png)

---

## Was ist MeshBBS?

MeshBBS ist ein **dezentrales Mailbox-System**, das auf einem beliebigen Server (z.B. Raspberry Pi, VPS) läuft und über einen **ESP32 mit MeshCore-Firmware** als Funk-Modem mit dem MeshCore-Netz verbunden ist. Der ESP32 dient sozusagen als **Modem** — er stellt die LoRa-Funkverbindung her, die eigentliche Mailbox-Logik läuft auf dem Server.

Das System besteht aus zwei Teilen:
- **ESP32 mit MeshCore-Firmware** — Modem-Funktion, stellt die LoRa-Funkverbindung per TCP her (Standard: Port 5000)
- **Python-BBS auf einem Server** — übernimmt Routing, Speicherung und Befehlsverarbeitung

---

## Was kann MeshBBS?

### PING — Reichweite testen
Sende `ping` auf jedem MeshCore-Kanal (Groß-/Kleinschreibung egal). Du bekommst ein PONG zurück mit:
- **Grid Square** — Maidenhead-Koordinaten (z.B. 📍XX0000)
- **Hop-Count** — wie viele Repeater zwischen euch liegen (⏱5hops)
- **Latenz** — Antwortzeit in Sekunden (7s)

```
@DEINE-NODE PONG 📍XX0000 ⏱3hops 5s
```

### TEST — Verbindung prüfen
Sende `test` → DM-Antwort "angekommen in DEINE-REGION" (privat, nur für dich sichtbar).

### BBOARD — Schwarzes Brett
Sende `bboard` → zeigt aktuelle Nachrichten auf dem Kanal (öffentlich).

### DM-Befehle (per Direktnachricht)
| Befehl | Beschreibung |
|--------|--------------|
| `!HELP` | Hilfe anzeigen |
| `!WHOAMI` | Deine Node-Info anzeigen |
| `!INBOX` | Postein- und Ausgang anzeigen |
| `!MSG <node>@<id> <text>` | Nachricht an andere Node senden |
| `!NODES` | Liste aktiver Nodes im Netz |
| `!STAT` | BBS-Statistiken |
| `!PING` | Ping an diesen BBS |
| `!ECHO <text>` | Text zurücksenden |
| `!SELFTEST` | Interne Systemdiagnose |
| `!STATUS` | Laufende Prozesse + Speicher |
| `!QUEUES` | Nachrichten-Warteschlangen |
| `!PEERS` | Verbundene Peers |
| `!LASTSYNC` | Letzter Sync-Zeitpunkt |

### DiagBot — Systemdiagnose
Vollständige Diagnose-Tools für Sysops: CPU, Speicher, Nachrichten-Queues, Netzwerk-Peers, letzte Sync-Zeiten.

### Auto-Reconnect
Wenn die TCP-Verbindung zum ESP32 abreißt, versucht MeshBBS automatisch die Verbindung wiederherzustellen (exponentieller Backoff: 10s → 120s, max 5 Versuche).

---

## Installation

### Voraussetzungen
- ESP32 mit MeshCore-Firmware (TCP-Server auf Port 5000)
- Linux-Server (z.B. Raspberry Pi, VPS)
- Python 3.10+
- meshcore Python-Bibliothek

### Schritt für Schritt

```bash
# 1. Repo klonen
git clone https://github.com/hisnameismarco/MeshBBS.git /opt/meshmail
cd /opt/meshmail

# 2. Virtuelle Umgebung erstellen
python3 -m venv /opt/meshmail-venv
source /opt/meshmail-venv/bin/activate
pip install meshcore

# 3. Konfiguration anpassen
cp config.env.example config.env
# Bearbeite config.env mit deinen Werten:
#   MESHMAIL_NODE_ID = DEINE-NODE-ID
#   MESHMAIL_TCP_HOST = IP-DES-ESP32
#   MESHMAIL_TCP_PORT = 5000

# 4. Datenverzeichnis erstellen
mkdir -p /var/lib/meshmail
chown meshmail:meshmail /var/lib/meshmail

# 5. Als Service installieren (systemd)
cp meshmail.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable MeshBBS
systemctl start MeshBBS

# 6. Status prüfen
systemctl status MeshBBS
ss -tnp state established | grep python3
```

### Oder manuell starten (ohne Service)
```bash
source /opt/meshmail-venv/bin/activate
python3 /opt/meshmail/main.py
```

---

## Konfiguration

Alle Einstellungen in `config.env`:

| Variable | Standard | Beschreibung |
|----------|---------|--------------|
| `MESHMAIL_NODE_ID` | YOUR-NODE-ID | Deine MeshCore-Node-ID |
| `MESHMAIL_LOCATION` | angekommen in DEINE-REGION | Text für TEST-Befehl |
| `MESHMAIL_TCP_HOST` | YOUR-ESP32-IP | IP-Adresse des ESP32 mit MeshCore |
| `MESHMAIL_TCP_PORT` | 5000 | TCP-Port des ESP32 |
| `MESHMAIL_DB_PATH` | /var/lib/meshmail/MeshBBS.db | Pfad zur SQLite-Datenbank |

---

## Architektur

```
┌──────────────────────────────────────────────────────────────┐
│                     MeshCore LoRa Netz                       │
│  [Node A]  ────  [Repeater]  ────  [ESP32 Gateway]         │
└────────────────────────┬─────────────────────────────────────┘
                         │ TCP (Port 5000)
                         ▼
┌──────────────────────────────────────────────────────────────┐
│               MeshBBS BBS Server (dein Server)              │
│                                                              │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   │
│  │ meshcore_if  │──▶│  routing     │──▶│   store      │   │
│  │ (TCP Bridge) │   │  (DM/Chan)   │   │  (SQLite)    │   │
│  └──────────────┘   └──────────────┘   └──────────────┘   │
│         │                  │                                │
│         ▼                  ▼                                │
│  ┌──────────────┐   ┌──────────────┐                       │
│  │  diagbot     │   │   main       │                       │
│  │  (Diagnose)  │   │   (BBS)      │                       │
│  └──────────────┘   └──────────────┘                       │
└──────────────────────────────────────────────────────────────┘
```

Siehe [ARCHITECTURE.md](ARCHITECTURE.md) für die vollständige Design-Dokumentation.

---

## Tech-Stack

- **Python 3.12** — Hauptprogramm
- **meshcore 2.3.6** —offizielle MeshCore Python-Bibliothek
- **SQLite** — Persistente Datenspeicherung
- **systemd** — Service-Management und Auto-Start
- **asyncio** — Asynchrone Kommunikation mit dem ESP32

---

## Dateien

| Datei | Beschreibung |
|-------|--------------|
| `main.py` | Hauptprogramm — BBS-Loop, Befehlsverarbeitung |
| `meshcore_if.py` | TCP-Bridge zu MeshCore, Auto-Reconnect |
| `diagbot.py` | Diagnose-Bot — PING, ECHO, SELFTEST, STATUS |
| `routing.py` | Routing-Engine für DM und Kanal-Nachrichten |
| `store.py` | SQLite-Interface — Nachrichten, Nodes, Statistik |
| `models.py` | Datenmodelle |
| `sync.py` | Synchronisation zwischen Nodes |
| `config.py` | Konfigurationsladung |
| `cli.py` | Telnet-CLI für Debugging |
| `meshmail.service` | systemd Service-Datei |

---

## Lizenz

MIT — frei nutzbar, anpassbar, erweiterbar.

---

## Autor

MeshBBS ist ein Open-Source-Projekt für die MeshCore-Community.
