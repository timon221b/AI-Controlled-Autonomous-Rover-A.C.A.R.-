import os
import re
import json
import logging
import requests

log = logging.getLogger(__name__)

OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_CHAT_URL = os.getenv("OLLAMA_CHAT_URL", "http://localhost:11434/api/chat")
INTENT_MODEL = "qwen3:1.7b"

# ═════════════════════════════════════════════════════════════════
#  Rule-based fast classifier
#  FIX: Classifies motion/stop/query commands locally in <1ms,
#  completely bypassing the 2.8s Ollama round-trip for these cases.
#  Only truly ambiguous / conversation inputs hit the LLM.
# ═════════════════════════════════════════════════════════════════
_MOTION_KEYWORDS = [
    "forward", "backward", "back", "reverse", "ahead", "straight",
    "turn left", "turn right", "spin", "rotate", "stop", "halt",
    "freeze", "abort", "pause", "wait", "hold", "move", "go",
    "clockwise", "counter", "ccw", "cw",
]

_QUERY_KEYWORDS = [
    "battery", "status", "obstacle", "distance", "sensor",
    "how far", "what is the", "telemetry", "temperature",
]

_VISION_KEYWORDS = [
    "what do you see", "what's in front", "describe", "camera",
    "look", "detect", "find", "spot", "identify",
]

def _rule_classify(text: str) -> dict | None:
    """
    Returns a high-confidence intent dict if the text clearly matches
    a known category, or None if the LLM should decide.
    """
    t = text.lower().strip()

    for kw in _MOTION_KEYWORDS:
        if kw in t:
            return {
                "category": "HARDWARE_MOTION",
                "confidence": 0.97,
                "requires_confirmation": False,
                "source": "rule"
            }

    for kw in _QUERY_KEYWORDS:
        if kw in t:
            return {
                "category": "HARDWARE_QUERY",
                "confidence": 0.95,
                "requires_confirmation": False,
                "source": "rule"
            }

    for kw in _VISION_KEYWORDS:
        if kw in t:
            return {
                "category": "VISION_QUERY",
                "confidence": 0.93,
                "requires_confirmation": False,
                "source": "rule"
            }

    return None   # fall through to LLM


# ═════════════════════════════════════════════════════════════════
#  LLM fallback — only fires for genuine ambiguous / chat input
# ═════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are an intent classifier for a robot rover.
Classify the user command into EXACTLY one category.

Categories:
  HARDWARE_MOTION : any movement, driving, spinning, turning, stopping
  HARDWARE_QUERY  : ask about sensors, battery, rover status
  VISION_QUERY    : ask what camera sees, describe scene, find objects
  CONVERSATION    : general chat, questions unrelated to rover hardware

Rules:
- If command contains ANY motion words -> HARDWARE_MOTION
- Output ONLY valid JSON, no explanation, no markdown, no thinking
- If confidence below 0.75 set requires_confirmation to true

Output format (strict):
{
  "category": "HARDWARE_MOTION",
  "confidence": 0.97,
  "requires_confirmation": false
}"""

# FIX: Correct endpoint and payload format for multi-turn chat.
# Original code sent "messages" to /api/generate which ignores them —
# it only reads "prompt". Multi-turn history requires /api/chat.
CHAT_SYSTEM = ("You are a friendly assistant built into a rover robot. "
               "Keep replies short — 1 to 2 sentences max.")

_history: list[dict] = []


def _strip_fences(text: str) -> str:
    # Strip <think>...</think> blocks that qwen3 emits even with think:false
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        return m.group(1)
    m2 = re.search(r'\{.*\}', text, re.DOTALL)
    return m2.group(0) if m2 else text


def _post(url: str, payload: dict, timeout: float) -> requests.Response:
    try:
        return requests.post(url, json=payload, timeout=timeout)
    except requests.exceptions.Timeout:
        log.warning("Ollama timeout — retrying (model loading?)")
        return requests.post(url, json=payload, timeout=60.0)


def classify_intent(text: str) -> dict:
    # ── Fast path: rule-based (< 1ms) ────────────────────────────
    fast = _rule_classify(text)
    if fast:
        log.debug(f"Rule classified: {fast}")
        return fast

    # ── Slow path: LLM (only for ambiguous input) ─────────────────
    log.debug("Falling through to LLM classifier")
    payload = {
        "model":  INTENT_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": text,
        "stream": False,
        "think":  False,
        "options": {
            "temperature": 0.0,
            "num_predict": 80,   # FIX: reduced from 150, JSON is ~60 tokens
        }
    }
    raw = ""
    try:
        resp = _post(OLLAMA_URL, payload, timeout=15.0)
        resp.raise_for_status()
        full = resp.json()
        log.debug(f"Ollama raw response: {full}")
        raw   = full.get("response", "").strip()
        clean = _strip_fences(raw)
        intent = json.loads(clean)
        assert "category" in intent and "confidence" in intent
        intent["source"] = "llm"
        return intent

    except requests.exceptions.ConnectionError:
        log.error("Ollama not running. Start with: ollama serve")
        return {"category": "CONVERSATION", "confidence": 0.0,
                "requires_confirmation": True, "error": "ollama_offline"}

    except requests.exceptions.Timeout:
        log.error("Ollama timed out even after retry")
        return {"category": "CONVERSATION", "confidence": 0.0,
                "requires_confirmation": True, "error": "ollama_timeout"}

    except (json.JSONDecodeError, AssertionError, KeyError) as e:
        log.error(f"Bad intent JSON: {e} | raw={raw!r}")
        return {"category": "CONVERSATION", "confidence": 0.0,
                "requires_confirmation": True, "error": "parse_failed"}


def generate_chat_reply(text: str, max_history: int = 6) -> str:
    _history.append({"role": "user", "content": text})

    # FIX: use /api/chat endpoint — /api/generate ignores "messages" entirely.
    # Also inject system role as first message since qwen3 expects it inline.
    messages = [{"role": "system", "content": CHAT_SYSTEM}]
    messages += _history[-max_history:]

    payload = {
        "model":    INTENT_MODEL,
        "messages": messages,
        "stream":   False,
        "think":    False,
        "options":  {"temperature": 0.7, "num_predict": 120}
    }
    try:
        resp = _post(OLLAMA_CHAT_URL, payload, timeout=15.0)
        resp.raise_for_status()
        # /api/chat returns message.content not response
        reply = resp.json().get("message", {}).get("content", "").strip()
        # Strip any stray <think> blocks from reply
        reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL).strip()
        if not reply:
            reply = "Sorry, I couldn't generate a reply right now."
        _history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        log.error(f"Chat reply failed: {e}")
        return "Sorry, I couldn't generate a reply right now."