# MeshMail — Native Mailbox-System für MeshCore

## 1. Systemübersicht

```
┌─────────────────────────────────────────────────────┐
│                    MeshMail Node                     │
│  ┌─────────┐  ┌──────────┐  ┌─────────┐  ┌────────┐ │
│  │  CLI /  │  │ Message  │  │ Routing │  │ Sync   │ │
│  │   API   │──│  Store   │──│ Engine  │──│ Engine │ │
│  └─────────┘  └──────────┘  └─────────┘  └────────┘ │
│       │            │              │            │      │
│  ┌─────────┐  ┌──────────┐  ┌─────────┐  ┌────────┐ │
│  │  User   │  │ Bulletin │  │  Node   │  │  Peer  │ │
│  │  Mgmt   │  │  Store   │  │  Table  │  │ Conn   │ │
│  └─────────┘  └──────────┘  └─────────┘  └────────┘ │
└─────────────────────────────────────────────────────┘
                          │
                    MeshCore TCP
                          │
              ┌───────────┴───────────┐
              │   Neighbor Nodes       │
              │   (store-and-forward)  │
              └─────────────────────────┘
```

**Prinzip:** Jeder Node ist eine eigenständige Mailbox. Messages werden hop-by-hop weitergeleitet.
Offline-Toleranz durch lokale Persistenz. eventual consistency, kein Echtzeit.

---

## 2. Adressierung

```
user@node-id               → Persönliche Mail
bulletin@region            → Gebiets-Bulletin  
sysop@node-id              → Sysop-Kommandos (lokal)
#area-name@node-id         → Lokales Board/Forum
```

- `node-id`: 8-char MeshCore Node-ID (z.B. `D81AE4DD`)
- Routing funktioniert über Node-IDs, nicht DNS
- Ziel `@*` als Broadcat (alle bekannten Nodes)

---

## 3. Datenmodell

### Message
```python
@dataclass
class MeshMessage:
    msg_id: str          # UUIDv4, global eindeutig
    from_addr: str       # user@node-id
    to_addr: str         # user@node-id oder bulletin@region
    msg_type: MessageType  # PERSONAL, BULLETIN, SYSTEM, ACK
    subject: str         # max 40 chars ASCII
    body: str            # max 512 chars pro chunk
    chunk: int           # 0 = keine chunks
    total_chunks: int    # 0 = kein chunking
    priority: Priority    # HIGH, NORMAL, LOW (für bulletins)
    ttl: int             # max hops (default: 7)
    hops: int            # aktuelle hops
    created_at: int      # unix timestamp
    received_at: int      # lokaler timestamp
    status: MessageStatus # QUEUED, FORWARDING, DELIVERED, FAILED, EXPIRED
    fwd_history: List[str] # node-ids die weitergeleitet haben (loop-schutz)
    signature: bytes     # optional, für auth/integrity
    thread_id: str       # für reply-tracking
    ref_msg_id: str       # original msg_id bei reply/forward
```

### User
```python
@dataclass  
class MailboxUser:
    user_id: str         # user@node-id
    password_hash: str   # argon2
    display_name: str
    is_sysop: bool
    is_blocked: bool
    created_at: int
    last_login: int
    msg_count: int       # statistik
    read_msg_ids: Set[str]  # gelesene msg-ids (für synchronisation)
```

### BulletinBoard
```python
@dataclass
class BulletinBoard:
    area_id: str         # z.B. "ALLGEMEIN", "TECHNIK", "REGION"
    area_name: str
    description: str
    is_public: bool
    replication_level: ReplicationLevel # ALL, REGION, NONE
    created_by: str
    created_at: int
```

### NodeInfo
```python
@dataclass
class PeerNode:
    node_id: str         # 8-char ID
    host: str            # IP oder hostname
    tcp_port: int
    last_seen: int
    last_poll: int
    status: NodeStatus   # ONLINE, OFFLINE, STALE
    msg_seq: int         # letzte bekannte sequenznummer
    fwd_bulletins: Set[str] # bulletin-areas die dieser node empfangen soll
```

### ForwardQueue
```python
@dataclass
class QueueEntry:
    msg_id: str
    dest_node: str
    chunk_idx: int       # 0 = gesamte nachricht
    attempts: int
    last_attempt: int
    next_retry: int      # unix timestamp
    status: QueueStatus  # PENDING, SENDING, ACKED, FAILED
```

---

## 4. Message-Typen

| Type       | Priority | TTL     | Replication               |
|------------|----------|---------|---------------------------|
| PERSONAL   | HIGH     | 7 hops  | hop-by-hop, direkt        |
| BULLETIN   | LOW      | 15 hops | epidemic (alle bulletin-nodes) |
| SYSTEM     | HIGH     | 3 hops  | nur lokale zustellung     |
| ACK        | HIGH     | 5 hops  | hop-by-hop                |
| POLL       | LOW      | 3 hops  | alle nodes               |

---

## 5. Nachrichtenformat (MeshCore-kompakt)

### Header (binary, 16 bytes)
```
[version: u8][type: u8][priority: u8][ttl: u8]
[hops: u8][flags: u8][total_chunks: u8][chunk_idx: u8]
[from_node: 4 bytes][to_node: 4 bytes]
[payload_len: u16]
```

### Payload (JSON oder Plaintext)
```json
{
  "id": "uuid",
  "from": "user@NODE",
  "to": "user@NODE",
  "subj": "...",
  "body": "...",
  "time": 1234567890,
  "thread": "original-msg-id",
  "sig": "base64..."
}
```

### Chunking
- Max 256 bytes payload per MeshCore-paket
- Große nachrichten in chunks aufgeteilt
- Empfänger sammelt chunks und assembliert

---

## 6. Routing & Synchronisation

### Persönliche Mail
1. Node A erstellt message, speichert lokal
2. Prüft ob Empfänger auf eigenem Node → direkt in inbox
3. Sonst: Forward-Queue erstellen
4. Bei verbindung zu Node B: Queue-Einträge senden
5. ACK zurück bei erfolgreicher empfang
6. Queue-Status aktualisieren

### Bulletin-Replication (Epidemic)
1. Bei neuer bulletin-message: an alle bekannten bulletin-nodes forwarden
2. Jeder node merkt sich `already_seen_msg_ids`
3. Bei Flood-Routing: `msg_id` in fwd_history prüfen
4. TTL decrement, bei 0 stoppen

### Loop-Schutz
- `fwd_history` sammelt alle durchlaufenen node-ids
- Wenn `node_id` bereits in `fwd_history` → nicht mehr forwarden
- Max history length = TTL (防止内存爆炸)

### Duplikatserkennung
- Message-ID + Empfänger als deduplication-key
- Empfänger-node prüft: `seen_msg_ids` set
- Bei dup: ACK senden, nicht nochmal speichern

---

## 7. CLI Kommandos

| Cmd | Beschreibung                                      |
|-----|---------------------------------------------------|
| `L` | Liste ungelesene Nachrichten                       |
| `LA` | Liste alle Nachrichten (nach datum)                |
| `R n`| Nachricht nr. n lesen                           |
| `S user@node` | Nachricht an user/node senden             |
| `SA` | Nachricht an alle lokalen user senden            |
| `D n`| Nachricht nr. n löschen                          |
| `F n user@node` | Nachricht nr. n weiterleiten           |
| `RP n` | Auf Nachricht nr. n antworten                  |
| `B` | Bulletin-Liste anzeigen                           |
| `BR area` | Bulletin-Bereich lesen                      |
| `BA area` | Ins Bulletin schreiben                       |
| `N` | Neue Nachricht schreiben (interaktiv)             |
| `H` | Hilfe anzeigen                                   |
| `Q` | Queue-Status anzeigen                            |
| `NODES` | Bekannte Nodes + Status                  |
| `STAT` | Eigene Mailbox-Statistik                     |
| `SYNC` | Synchronisation mit Neighbor erzwingen       |
| `U` | User-Info und Profil                            |
| `LOGOUT` | Verbindung beenden                       |

---

## 8. Zustellalgorithmen

### Senden (lokal → remote)
```
1. message local speichern
2. routing-tabelle konsultieren
3. nächste hops ermitteln
4. forward-queue eintrag erstellen
5. bei verbindung: sofort senden
6. retry bei fehler mit exponential backoff
```

### Empfangen
```
1. nachricht empfangen
2. header validieren
3. msg_id in seen_ids prüfen
4. wenn dup → ACK und verwerfen
5. wenn neu → speichern, seen_ids merken
6. wenn für lokal → in inbox
7. wenn nicht für lokal und ttl > 0 → forward
8. wenn ttl == 0 → verwerfen
```

---

## 9. Fehlerszenarien

| Problem | Lösung |
|---------|--------|
| Verbindung verloren beim Senden | Queue behält chunk-index, resume möglich |
| Doppelte Zustellung | Dedup via seen_msg_ids |
| Loop | fwd_history mit node-ids |
| Node offline | Messages in Queue, retry nach timeout |
| Speicher voll | LRU-Algorithmus für alte bulletis, max queue size |
| Ungültiger Empfänger | NDR (No Delivery Report) an absender |

---

## 10. MVP (Kern: nur persönliche Mail)

### Im Lieferumfang:
- Nachrichten senden/lesen/löschen
- User-Auth (local)
- Store-and-forward zu direkt verbundenen Nodes
- Peer-Discovery
- Einfache Queue mit Retry
- Duplikatschutz

### Nicht in MVP:
- Bulletins
- Sysop-Admin
- Dateianhänge
- Multi-hop routing
- Ende-zu-Ende-Verschlüsselung

---

## 11. Erweiterte Version

### Bulletin-System
- Areas: ALLGEMEIN, TECHNIK, REGION, SYSOP, CUSTOM
- Epidemic dissemination mit TTL
- Selektive Replikation pro area

### Routing
- Multi-hop mit Routenplanung
- Meta-info pro node (capabilities, bulletin-subs)
- Bidirectional sync

### Admin/Sysop
- Benutzer sperren/freischalten
- Bulletin-Areas erstellen/löschen
- Queue-Status einsehen
- Routing-Parameter
- Logging

---

## 12. Implementierungshinweise

- Async/await für alle I/O
- SQLite für Persistenz (kein externes DB)
- MeshCore-TCP-Interface für Verbindung
- Modulare Architektur: austauschbare Routing-Algorithmen
- Chunked transfer für große Messages
- LRU-Cache für seen_msg_ids (begrenzte größe)

## Dateistruktur

```
meshmail/
├── __init__.py
├── config.py          # Konfiguration
├── models.py          # Dataclasses
├── store.py           # Message/User persistence
├── routing.py         # Routing engine
├── sync.py            # Sync protocol
├── cli.py             # CLI interface
├── api.py             # HTTP API
└── meshcore_if.py     # MeshCore adapter
```