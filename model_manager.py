"""Ollama model profiles and on-demand model downloads."""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


OLLAMA_URL = "http://127.0.0.1:11434"
PROFILES = {
    "fast": {
        "name": "Fast assistant",
        "model": "qwen2.5:3b",
        "description": "Small general model. Best fit for this PC.",
        "warning": "",
    },
    "coding": {
        "name": "Coding assistant",
        "model": "qwen2.5-coder:3b",
        "description": "Code-focused 3B model, about 1.9 GB to download.",
        "warning": "",
    },
    "strong": {
        "name": "Higher quality (slow)",
        "model": "qwen2.5-coder:7b",
        "description": "Larger code model, about 4.7 GB to download.",
        "warning": "This model is larger than your 4 GB VRAM and may run slowly or use Windows paging with 8 GB RAM.",
    },
}
PROFILE_ALIASES = {
    "fast": "fast", "faster": "fast", "light": "fast", "lightweight": "fast", "low": "fast",
    "coding": "coding", "code": "coding",
    "strong": "strong", "high": "strong", "higher": "strong", "quality": "strong",
}
_SETTINGS_FILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OrbitDesk" / "settings.json"


def normalize_profile(profile):
    key = str(profile or "").strip().casefold()
    normalized = PROFILE_ALIASES.get(key, key)
    if normalized not in PROFILES:
        raise ValueError("Choose fast, coding, or strong model profile.")
    return normalized


def load_profile():
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        return normalize_profile(data.get("model_profile", "fast"))
    except (OSError, ValueError, TypeError):
        return "fast"


def save_profile(profile):
    profile = normalize_profile(profile)
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError, TypeError):
        previous = {}
    previous["model_profile"] = profile
    temporary = _SETTINGS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(previous, indent=2), encoding="utf-8")
    temporary.replace(_SETTINGS_FILE)
    return profile


def pull_model(model, on_progress=None):
    """Ask the already-installed local Ollama service to pull a model."""
    if model not in {entry["model"] for entry in PROFILES.values()}:
        raise ValueError("That model is not in OrbitDesk's approved model list.")
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            for raw_line in response:
                if not raw_line.strip():
                    continue
                status = json.loads(raw_line.decode("utf-8"))
                if status.get("error"):
                    raise RuntimeError(str(status["error"])[:300])
                if on_progress:
                    on_progress(status)
    except urllib.error.URLError as error:
        raise RuntimeError("Can't reach Ollama at 127.0.0.1:11434. Install and start Ollama first.") from error
    return model


def progress_text(status):
    message = status.get("status", "Downloading model…")
    total = status.get("total")
    completed = status.get("completed")
    if isinstance(total, (int, float)) and total > 0 and isinstance(completed, (int, float)):
        return f"{message}: {min(100, int(completed * 100 / total))}%"
    return str(message)


def progress_percent(status):
    total = status.get("total")
    completed = status.get("completed")
    if isinstance(total, (int, float)) and total > 0 and isinstance(completed, (int, float)):
        return min(100, max(0, int(completed * 100 / total)))
    return -1
