"""
MeshBBS AI Bridge - KI-Antworten auf LoRa
Nur für MeshCore-Messages ohne '!' Prefix.
"""
import os
import time
import logging
from typing import Optional

log = logging.getLogger("MeshBBS.ai")

# ─── Config ───────────────────────────────────────────────
AI_MODEL = os.environ.get("MESHMAIL_AI_MODEL", "MiniMax-M2.7")
AI_API_KEY = os.environ.get("MESHMAIL_AI_API_KEY", "")
AI_API_URL = os.environ.get("MESHMAIL_AI_API_URL", "https://api.minimax.io/anthropic/v1/messages")
AI_MAX_TOKENS = int(os.environ.get("MESHMAIL_AI_MAX_TOKENS", "300"))
AI_RATE_LIMIT = 30  # Sekunden zwischen Anfragen pro User

# ─── Rate Limiting ──────────────────────────────────────
_ai_last_request: dict[str, float] = {}

def _can_query_ai(from_pubkey: str) -> bool:
    """Prüft ob User eine KI-Anfrage machen darf (Rate Limit)."""
    now = time.time()
    last = _ai_last_request.get(from_pubkey, 0)
    if now - last < AI_RATE_LIMIT:
        return False
    _ai_last_request[from_pubkey] = now
    return True

def _ai_query(user_message: str, node_id: str = "MeshBBS") -> Optional[str]:
    """Fragt die KI (MiniMax/OpenAI-compatible) und gibt Antwort zurück."""
    if not AI_API_KEY:
        return None
    
    try:
        import urllib.request
        import json
        
        headers = {
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
            "X-API-Key": AI_API_KEY,
            "anthropic-version": "2023-06-01"
        }
        
        system_prompt = (
            f"You are {node_id}, a helpful AI assistant on a MeshCore LoRa mesh network. "
            "Keep answers very short (under 300 tokens). Be concise and friendly."
        )
        
        payload = {
            "model": AI_MODEL,
            "max_tokens": AI_MAX_TOKENS,
            "temperature": 0.7,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message}
            ]
        }
        
        req = urllib.request.Request(
            AI_API_URL,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            # MiniMax/OpenAI compatible response
            if "content" in data:
                return data["content"][0]["text"].strip()
            elif hasattr(data, "choices"):
                return data.choices[0].message.content.strip()
    
    except Exception as e:
        log.error(f"AI query failed: {e}")
        return None
    
    return None


def handle_ai_message(from_pubkey: str, text: str, node_id: str = "MeshBBS") -> Optional[str]:
    """
    Hauptfunktion: wenn Rate Limit OK und KI konfiguriert, Anfrage stellen.
    Gibt KI-Antwort zurück oder None.
    """
    if not AI_API_KEY:
        return None
    
    if not _can_query_ai(from_pubkey):
        elapsed = AI_RATE_LIMIT - int(time.time() - _ai_last_request.get(from_pubkey, 0))
        return f"KI-Cooldown: noch {max(1, elapsed)}s warten."
    
    response = _ai_query(text, node_id)
    if response:
        return response
    return None
