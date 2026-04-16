# MeshCore-Integration

## Verbindung

MeshBBS verbindet sich per TCP zu MeshCore:

```
MeshBBS → TCP:192.168.2.30:5000 → MeshCore → LoRa → Andere Nodes
```

## Auto-Reconnect

Bei Verbindungsabbruch:
- Heartbeat alle 15 Sekunden
- Max 5 Reconnect-Versuche
- Exponential Backoff (10s → 120s)

## Nachrichten-Format

### DM senden
```
MC->] DM <pubkey>: <text>
```

### DM empfangen
```
[MC<-] DM <von_pubkey>: <text>
```

### Kanal senden
```
[MC->] CHAN #<nr>: <text>
```

### Kanal empfangen
```
[MC<-] CHAN #<nr> text=<sender>: <text> SNR=<snr> RSSI=<rssi>
```

## PubKey

Dein MeshCore Public Key wird für DM-Antworten verwendet:
- DM-Antworten gehen an `from_pubkey`
- Kanal-Antworten auf dem gleichen Kanal

## Kanäle

| Kanal | Verwendung |
|-------|-----------|
| 0 | Sonstige |
| 1 | PING/PONG |
| 2 | TEST/BBOARD |
