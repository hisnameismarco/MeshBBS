# Changelog

Alle Änderungen werden hier dokumentiert.

## [0.3.1] — 2026-04-14

### Fix
- **Auto-Reconnect**: TCP-Verbindung zum ESP32 wird jetzt automatisch wiederhergestellt
  - Heartbeat-Check alle 15 Sekunden
  - Bis zu 5 Reconnect-Versuche mit exponential backoff (10s → 120s)
  - Saubere Bereinigung des alten Verbindungs-State vor reconnect

## [0.3.0] — 2026-04-13

### Feature
- **PING/PONG**: Auf jedem Kanal (case-insensitive) → PONG mit Maidenhead-Grid (📍XX0000), Hop-Count (⏱Nh), Latenz (Ns)
- **TEST**: DM-Antwort "angekommen in DEINE-REGION"
- **BBOARD**: Bulletin-Board auf jedem Kanal
- **DM-Commands**: `!HELP`, `!STAT`, `!INBOX`, `!MSG`, `!WHOAMI`, `!NODES`, `!PING`, `!ECHO`, `!SELFTEST`, `!STATUS`, `!QUEUES`, `!PEERS`, `!LASTSYNC`, `!TEST`
- **DiagBot**: Vollständige Systemdiagnose integriert
- **Kanal-Handling**: Groß-/Kleinschreibung ignorieren
- **Rate-Limiting**: 10 Befehle/Min pro Absender

### Technisch
- MeshCore TCP-Bridge mit `MeshCore.create_tcp()` (meshcore 2.3.6)
- Node-ID: YOUR-NODE-ID
- Grid Square: XX0000 (deine-lat, deine-lon)
- ESP32 PubKey: your-pubkey-here
- DM + Kanal-Broadcast für alle Command-Antworten
- PID 22953+ | /var/lib/meshmail/meshmail.db
