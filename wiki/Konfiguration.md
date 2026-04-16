# Konfiguration

Die Konfiguration erfolgt über `config.env` oder Umgebungsvariablen.

## Umgebungsvariablen

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `MESHMAIL_NODE_ID` | DE-ST-COSWIG-MARCO | Deine Node-ID |
| `MESHMAIL_TCP_HOST` | 192.168.2.30 | MeshCore IP |
| `MESHMAIL_TCP_PORT` | 5000 | MeshCore Port |
| `MESHMAIL_DB_PATH` | /var/lib/meshmail/meshmail.db | Datenbank-Pfad |
| `MESHMAIL_LISTEN_PORT` | 7800 | Lokaler Port |
| `MESHMAIL_SYNC_INTERVAL` | 300 | Sync-Intervall (Sekunden) |
| `MESHMAIL_LOCATION` | angekommen in Coswig-Anhalt | Standort-String |

## Node-ID Format

Format: `DE-ST-COSWIG-MARCO`

- `DE`: Ländercode
- `ST`: Bundesland
- `COSWIG`: Stadt
- `MARCO`: Dein Rufzeichen/Name

## Datenbank

Standard-Pfad: `/var/lib/meshmail/meshmail.db`

Die Datenbank enthält:
- `messages`: Alle Nachrichten
- `inbox`: Posteingang je Node
- `forward_queue`: Weiterleitungs-Warteschlange
