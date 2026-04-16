# Architektur

## Überblick

```
┌─────────────┐     TCP      ┌──────────────┐
│  MeshCore   │──────────────│ MeshBBS      │
│  Network    │              │ Server       │
│  (ESP32)    │◄─────────────│              │
└─────────────┘   LoRa        │  ┌────────┐ │
                             │  │ Database│ │
                             │  └────────┘ │
                             └─────────────┘
```

## Komponenten

### main.py
Zentrale Steuerung:
- MeshCore TCP Bridge
- BBS Command Registry
- Channel Handler
- Routing Engine
- Sync Engine

### meshcore_if.py
TCP-Interface zu MeshCore:
- TCP-Verbindung mit Auto-Reconnect
- Heartbeat alle 15s
- DM und Kanal-Nachrichten

### store.py
SQLite-Datenbank:
- Messages-Tabelle
- Inbox-Tabelle
- Forward-Queue

### routing.py
Routing-Engine:
- Node-Tracking
- Nachrichten-Weiterleitung
- Path-Finding

### diagbot.py
Diagnose-Bot:
- Systemstatus
- Queue-Statistiken
- Peer-Info

## Datenbank-Schema

```sql
messages (
  msg_id TEXT PRIMARY KEY,
  from_addr TEXT,
  to_addr TEXT,
  subject TEXT,
  body TEXT,
  created_at INTEGER,
  status INTEGER
)

inbox (
  msg_id TEXT,
  to_addr TEXT,
  subject TEXT,
  created_at INTEGER,
  is_read INTEGER,
  is_deleted INTEGER
)

forward_queue (
  msg_id TEXT,
  next_hop TEXT,
  status INTEGER,
  next_retry INTEGER,
  attempts INTEGER
)
```
