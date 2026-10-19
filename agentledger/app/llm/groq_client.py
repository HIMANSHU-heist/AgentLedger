"""Groq wrapper: model routing, disk cache, rate limiting, mock fallback."""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Verify model names in Groq docs once; they change over time. Override via .env if needed.
MODELS = {
    "fast": os.getenv("GROQ_FAST_MODEL", "openai/gpt-oss-20b"),
    "strong": os.getenv("GROQ_STRONG_MODEL", "openai/gpt-oss-120b"),
}
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "llm_cache.json"
MIN_INTERVAL = 2.5      # seconds between live calls (free-tier friendly)
FORCE_MOCK = False      # tests / benchmark can set True

_lock = threading.Lock()
_last_call = 0.0
_cache = None
stats = {"groq": 0, "cache": 0, "mock": 0, "errors": 0}


def llm_available():
    return bool(os.getenv("GROQ_API_KEY")) and not FORCE_MOCK


def route_tier(outcome):
    """Cheap model for routine cases, strong model for risky decisions."""
    return "strong" if outcome in ("HUMAN_APPROVAL_REQUIRED", "BLOCKED", "ESCALATE") else "fast"


def _load_cache():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text())
        except Exception:
            _cache = {}
    return _cache


def _save_cache():
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False, indent=1))
    except Exception:
        pass


def _key(model, system, prompt, json_mode):
    raw = json.dumps([model, system, prompt, json_mode], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def complete(prompt, system="", tier="fast", json_mode=False, temperature=0.0):
    """Returns {"text", "source": groq|cache|mock|error, "model"}. text is None on mock/error."""
    global _last_call
    model = MODELS[tier]
    k = _key(model, system, prompt, json_mode)

    with _lock:
        cache = _load_cache()
        if k in cache:
            stats["cache"] += 1
            return {"text": cache[k], "source": "cache", "model": model}

    if not llm_available():
        stats["mock"] += 1
        return {"text": None, "source": "mock", "model": None}

    try:
        from groq import Groq
        with _lock:
            wait = MIN_INTERVAL - (time.time() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.time()
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        kwargs = dict(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=1024,
            extra_body={"reasoning_effort": "low"},
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content
        with _lock:
            _load_cache()[k] = text
            _save_cache()
            stats["groq"] += 1
        return {"text": text, "source": "groq", "model": model}
    except Exception as e:
        stats["errors"] += 1
        return {"text": None, "source": "error", "model": model, "error": str(e)[:200]}
