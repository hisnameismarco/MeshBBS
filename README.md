# MeshMail v0.3 — MeshCore BBS

**Native Mailbox System for MeshCore BBS over LoRa/Telnet**

## Features

- **PING command** on any MeshCore channel → PONG response with Maidenhead grid (📍KJ2247), hop count (⏱Nh), and latency (Ns)
- **TEST command** → DM reply "angekommen in Coswig-Anhalt"
- **DM commands**: `!HELP`, `!STAT`, `!INBOX`, `!MSG`, `!WHOAMI`, `!NODES`, `!PING`, `!ECHO`, `!SELFTEST`, etc.
- **DiagBot**: System diagnostics (PING, ECHO, SELFTEST, STATUS, QUEUES, PEERS, LASTSYNC)
- **Rate limiting**: 10 commands/min per sender
- **Channel handling**: Case-insensitive (ping/PING/Ping all work)

## Installation

```bash
# On your MeshCore node (ESP32):
# Clone to /opt/meshmail/
git clone https://github.com/hisnameismarco/meshmail.git /opt/meshmail

# Create venv with meshcore library
python3 -m venv /opt/meshmail-venv
source /opt/meshmail-venv/bin/activate
pip install meshcore

# Run
python3 /opt/meshmail/main.py
```

Or use the systemd service:
```bash
cp meshmail/meshmail.service /etc/systemd/system/
systemctl enable meshmail
systemctl start meshmail
```

## Configuration

Set environment variables or edit `config.env`:
- `MESHMAIL_NODE_ID` — Your MeshCore node ID (default: DE-ST-COSWIG-MARCO)
- `MESHMAIL_TCP_HOST` — ESP32 MeshCore IP (default: 192.168.2.30)
- `MESHMAIL_TCP_PORT` — ESP32 MeshCore TCP port (default: 5000)

## PONG Response Format

```
@DE-ST-COSWIG-MOBIL PONG 📍KJ2247 ⏱1hops 7s
```

- `@<sender>` — Mention of who sent PING
- `PONG` — Reply identifier
- `📍KJ2247` — Maidenhead grid square of this node
- `⏱1hops` — Hop count from sender to this node
- `7s` — Latency in seconds

## MeshCore Connection

MeshMail connects to the ESP32 MeshCore node via TCP (MeshCore Bridge). The bridge handles:
- DM routing (contact_msg)
- Channel message routing (channel_msg_recv)
- Automatic reconnection
- Message queue with exponential backoff

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for full design documentation.

## Tech Stack

- Python 3.12
- meshcore 2.3.6 (MeshCore LoRa library)
- SQLite (for persistent storage)
- systemd (for service management)

## License

MIT