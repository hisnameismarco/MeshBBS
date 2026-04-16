# Installation

## Voraussetzungen

- ESP32 mit MeshCore-Firmware
- Python 3.10+
- TCP-Verbindung zum MeshCore-Netzwerk

## Schritte

### 1. Abhängigkeiten installieren

```bash
pip install meshcore aiosqlite
```

### 2. Repository klonen

```bash
git clone https://github.com/hisnameismarco/MeshBBS.git
cd MeshBBS
```

### 3. Konfiguration erstellen

```bash
cp config.env.example config.env
# Editiere config.env mit deinen Werten
```

### 4. Service starten

```bash
# Als systemd Service (empfohlen)
sudo cp meshmail.service /etc/systemd/system/
sudo systemctl enable meshmail
sudo systemctl start meshmail
```

##验证

```bash
sudo systemctl status meshmail
```

Erwartete Ausgabe: `active (running)`
