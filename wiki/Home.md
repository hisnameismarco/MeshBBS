# MeshBBS Wiki

Willkommen beim MeshBBS Wiki — der deutschsprachigen Dokumentation für das MeshCore BBS-System.

## Übersicht

MeshBBS ist ein natives Mailbox-System für MeshCore-Netzwerke über LoRa-Funk. Es ermöglicht:

- **Direktnachrichten (DM)** zwischen Nodes
- **Öffentliche Kanal-Nachrichten** auf definierten Kanälen
- **Bulletin-Board** für Nachrichten an alle
- **MeshCore-Integration** per TCP-Bridge

## Erste Schritte

1. [Installation](./Installation.md) — Setup auf dem ESP32
2. [Konfiguration](./Konfiguration.md) — config.env richtig einstellen
3. [Befehle](./Befehle.md) — Alle verfügbaren Kommandos

## Befehlsübersicht

| Befehl | Beschreibung |
|---------|-------------|
| `!HELP` | Hilfe anzeigen |
| `!STAT` | BBS-Status |
| `!INBOX` | Nachrichten empfangen |
| `!MSG @user <text>` | Nachricht senden |
| `!DELETE <nr>` | Nachricht löschen |
| `!PING` | Ping-Antwort |
| `#PING` | Öffentlicher Ping (Kanal) |
| `#TEST` | Verbindungstest |

## Kanäle

- **Kanal 1**: PING/PONG
- **Kanal 2**: TEST/BBOARD
- **Kanal 0**: Sonstige Nachrichten

## Weiterführendes

- [Architektur](./Architektur.md)
- [MeshCore-Integration](./MeshCore-Integration.md)
- [Changelog](../CHANGELOG.md)
