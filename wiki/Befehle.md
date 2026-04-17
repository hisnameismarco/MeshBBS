# Befehle

Alle Befehle beginnen mit `!` für DMs. Öffentliche Befehle auf Kanälen beginnen mit `#`.

## Direktnachrichten (DM)

### `!HELP`
Zeigt alle verfügbaren Befehle.

### `!STAT`
Zeigt BBS-Status:
- Nachrichten-Anzahl
- Online/gesamte Nodes
- Queue-Größe

### `!INBOX`
Zeigt empfangene Nachrichten (max 10).

### `!MSG @username <betreff> [text>`
Sendet eine Nachricht an einen anderen User.
Erweiterte Syntax: `!MSG @username[@node] | <betreff> | [text]`

### `!DELETE <nummer>`
Löscht eine Nachricht aus der Inbox (Nummern aus `!INBOX`).

### `!WHOAMI`
Zeigt deine eigene Adresse.

### `!NODES`
Liste aller bekannten Nodes.

### `!PING`
Ping-Antwort mit Uptime und Queue-Status.

### `!ECHO <text>`
Gibt den Text unverändert zurück.

### `!SELFTEST`
Systemdiagnose.

### `!STATUS`
Detaillierter Systemstatus.

### `!QUEUES`
Zeigt Queue-Statistiken.

### `!PEERS`
Zeigt verbundene Peers.

### `!LASTSYNC`
Zeigt Zeit seit letzter Synchronisation.

## Öffentliche Kanal-Befehle

Diese Befehle werden auf öffentlichen Kanälen (ohne `!`) erkannt.

### `#PING` oder `PING` oder `#ping`
PONG-Antwort mit:
- Maidenhead-Grid (📍)
- Hop-Count (⏱)
- Latenz (Sekunden)

### `#TEST` oder `TEST`
Antwort: `Test erfolgreich angekommen in <ORT>`

### `#BBOARD` oder `BBOARD`
Zeigt Bulletin-Board (öffentliche Nachrichten).

## Case-Insensitive

Alle Befehle sind case-insensitive:
- `#PING`, `#ping`, `#Ping` → gleiches Ergebnis
