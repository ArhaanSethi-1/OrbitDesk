"""Speech synthesis with in-memory online playback and an offline SAPI fallback."""

import asyncio
import io
import os
import threading
from pathlib import Path

import edge_tts
import pygame

try:
    env_file = Path(__file__).resolve().parents[3] / ".env"
    for _line in env_file.read_text(encoding="utf-8").splitlines():
        if _line.strip().startswith("JARVIS_VOICE="):
            os.environ.setdefault("JARVIS_VOICE", _line.split("=", 1)[1].strip().strip("\"'"))
except OSError:
    pass

VOICE = os.environ.get("JARVIS_VOICE", "en-GB-RyanNeural")
_mixer_lock = threading.Lock()
_mixer_initialized = False
_playback_cancel = threading.Event()
_sapi_engine = None
_sapi_lock = threading.Lock()
_speaking_event = threading.Event()


async def generate_speech(text, filename=None):
    """Return generated MP3 bytes. ``filename`` remains accepted for compatibility."""
    communicator = edge_tts.Communicate(text, VOICE, rate="+10%", pitch="-5Hz")
    audio = bytearray()
    async for chunk in communicator.stream():
        if chunk.get("type") == "audio":
            audio.extend(chunk["data"])
    if not audio:
        raise RuntimeError("Speech service returned no audio")
    if filename:
        with open(filename, "wb") as output:
            output.write(audio)
    return bytes(audio)


def _ensure_mixer():
    global _mixer_initialized
    with _mixer_lock:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        _mixer_initialized = True


def _speak_offline(text):
    global _sapi_engine
    import pyttsx3

    engine = pyttsx3.init("sapi5")
    with _sapi_lock:
        _sapi_engine = engine
    try:
        engine.say(text)
        engine.runAndWait()
    finally:
        engine.stop()
        with _sapi_lock:
            _sapi_engine = None


def speak(text):
    _playback_cancel.clear()
    _speaking_event.set()
    try:
        audio = asyncio.run(generate_speech(text))
        if _playback_cancel.is_set():
            return
        _ensure_mixer()
        pygame.mixer.music.load(io.BytesIO(audio), "speech.mp3")
        if _playback_cancel.is_set():
            return
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy() and not _playback_cancel.wait(0.08):
            threading.Event().wait(0.1)
    except Exception as online_error:
        if _playback_cancel.is_set():
            return
        try:
            _speak_offline(text)
        except Exception as offline_error:
            print(f"[Voice error] Online speech failed ({online_error}); SAPI failed ({offline_error})")
    finally:
        _speaking_event.clear()


def is_speaking():
    return _speaking_event.is_set()


def stop_speaking():
    """Stop the current output immediately when supported by the active backend."""
    _playback_cancel.set()
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception:
        pass
    with _sapi_lock:
        if _sapi_engine is not None:
            try:
                _sapi_engine.stop()
            except Exception:
                pass
