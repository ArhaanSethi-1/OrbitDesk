"""Speech synthesis with in-memory online playback and an offline SAPI fallback."""

import asyncio
import io
import threading

import edge_tts
import pygame

VOICE = "en-GB-RyanNeural"
_mixer_lock = threading.Lock()
_mixer_initialized = False


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
    import pyttsx3

    engine = pyttsx3.init("sapi5")
    try:
        engine.say(text)
        engine.runAndWait()
    finally:
        engine.stop()


def speak(text):
    try:
        audio = asyncio.run(generate_speech(text))
        _ensure_mixer()
        pygame.mixer.music.load(io.BytesIO(audio), "speech.mp3")
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            threading.Event().wait(0.1)
    except Exception as online_error:
        try:
            _speak_offline(text)
        except Exception as offline_error:
            print(f"[Voice error] Online speech failed ({online_error}); SAPI failed ({offline_error})")
