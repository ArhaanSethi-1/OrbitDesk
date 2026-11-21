"""Low duty cycle wake detection and on-demand local transcription."""

import os
import queue
import re
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
WAKE_THRESHOLD = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.55"))
COMMAND_MAX_SECONDS = 15
TRAILING_SILENCE_SECONDS = 0.9
VOICE_RMS_THRESHOLD = 110
INPUT_BLOCK_FRAMES = 1280
WAKE_PHRASE = re.compile(r"\b(?:hey\s+)?jarvis\b", re.IGNORECASE)
MIC_DEVICE = None
_transcriber = None
_transcriber_lock = threading.Lock()
_wake_detector = None
_wake_detector_lock = threading.Lock()


def _get_transcriber():
    global _transcriber
    if _transcriber is None:
        with _transcriber_lock:
            if _transcriber is None:
                from faster_whisper import WhisperModel

                model_name = os.environ.get("JARVIS_WHISPER_MODEL", "base.en")
                _transcriber = WhisperModel(model_name, device="cpu", compute_type="int8")
    return _transcriber


def _get_wake_detector():
    global _wake_detector
    if _wake_detector is None:
        with _wake_detector_lock:
            if _wake_detector is None:
                from openwakeword.model import Model

                custom_jarvis = os.environ.get(
                    "JARVIS_SINGLE_WAKE_MODEL",
                    str(Path(os.environ.get("LOCALAPPDATA", "")) / "OrbitDesk" / "models" / "jarvis.onnx"),
                )
                wake_models = ["hey_jarvis"]
                if os.path.isfile(custom_jarvis):
                    wake_models.append(custom_jarvis)
                _wake_detector = Model(wakeword_models=wake_models, inference_framework="onnx")
    return _wake_detector


def _select_input_device():
    configured = os.environ.get("JARVIS_MIC_DEVICE")
    if configured:
        try:
            return int(configured)
        except ValueError:
            pass
    try:
        devices = sd.query_devices()
        default_input = sd.default.device[0]
        if default_input is not None and int(default_input) >= 0:
            return int(default_input)
        for index, device in enumerate(devices):
            if device["max_input_channels"] > 0:
                return index
    except Exception:
        pass
    return None


MIC_DEVICE = _select_input_device()


def get_input_device_name():
    try:
        device = MIC_DEVICE if MIC_DEVICE is not None else sd.default.device[0]
        return sd.query_devices(device)["name"]
    except Exception:
        return "Windows default microphone"


def _play_chime():
    try:
        import pygame

        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
        sample_rate = pygame.mixer.get_init()[0]
        duration = 0.18
        samples = np.arange(int(sample_rate * duration), dtype=np.float32) / sample_rate
        envelope = np.minimum(1.0, np.minimum(samples * 35.0, (duration - samples) * 22.0))
        tone = (np.sin(2 * np.pi * 880 * samples) + 0.55 * np.sin(2 * np.pi * 1320 * samples))
        audio = np.clip(tone * envelope * 9000, -32767, 32767).astype(np.int16)
        pygame.mixer.Sound(buffer=audio.tobytes()).play()
    except Exception:
        pass


def _capture_command(stop_event):
    chunks = []
    started = time.monotonic()
    voice_started = False
    silent_seconds = 0.0
    done = threading.Event()

    def callback(indata, frames, _time_info, status):
        nonlocal voice_started, silent_seconds
        if stop_event.is_set():
            raise sd.CallbackStop
        samples = indata.reshape(-1).astype(np.float32)
        rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
        if rms >= VOICE_RMS_THRESHOLD:
            voice_started = True
            silent_seconds = 0.0
        elif voice_started:
            silent_seconds += frames / SAMPLE_RATE
        chunks.append(indata.copy())
        elapsed = time.monotonic() - started
        if (elapsed >= COMMAND_MAX_SECONDS
                or (not voice_started and elapsed >= 3.5)
                or (voice_started and silent_seconds >= TRAILING_SILENCE_SECONDS)):
            done.set()
            raise sd.CallbackStop

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        device=MIC_DEVICE, blocksize=INPUT_BLOCK_FRAMES,
                        callback=callback) as stream:
        try:
            while not done.wait(0.08) and not stop_event.is_set():
                pass
        finally:
            try:
                stream.stop()
            except Exception:
                pass
    if stop_event.is_set() or not chunks:
        return None
    return np.concatenate(chunks, axis=0).reshape(-1).astype(np.float32) / 32768.0


def _transcribe(audio):
    if audio is None or not len(audio):
        return ""
    segments, _info = _get_transcriber().transcribe(
        audio, language="en", beam_size=1, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


def _remove_wake_phrase(text):
    return WAKE_PHRASE.sub("", text, count=1).strip(" \t\r\n,.:;!?-")


def listen_for_wake_command(stop_event, on_wake=None, on_activity=None, on_ready=None,
                            is_speaking=None, on_interrupt=None):
    """Wait for an openWakeWord trigger, then capture one command."""
    try:
        detector = _get_wake_detector()
    except ImportError as error:
        raise RuntimeError("Wake word support is missing. Run setup.bat to install it.") from error
    audio_queue = queue.Queue(maxsize=12)
    interruption_audio = deque(maxlen=16)
    interruption_frames = 0

    def callback(indata, frames, _time_info, status):
        if stop_event.is_set():
            raise sd.CallbackStop
        try:
            audio_queue.put_nowait(indata.copy().reshape(-1))
        except queue.Full:
            try:
                audio_queue.get_nowait()
                audio_queue.put_nowait(indata.copy().reshape(-1))
            except queue.Empty:
                pass

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        device=MIC_DEVICE, blocksize=1280, callback=callback) as stream:
        if on_ready:
            on_ready()
        try:
            while not stop_event.is_set():
                try:
                    chunk = audio_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                if is_speaking is not None and is_speaking():
                    interruption_audio.append(chunk)
                    interruption_frames += len(chunk)
                    if interruption_frames >= int(SAMPLE_RATE * 1.2):
                        interruption_frames = 0
                        clip = np.concatenate(tuple(interruption_audio)).astype(np.float32) / 32768.0
                        interruption_audio.clear()
                        rms = float(np.sqrt(np.mean(clip * clip))) if len(clip) else 0.0
                        if rms > VOICE_RMS_THRESHOLD / 32768.0:
                            words = _transcribe(clip).casefold().strip(" .,!?").split()
                            if words and len(words) <= 4 and words[0] in {"stop", "cancel"}:
                                if on_interrupt:
                                    on_interrupt()
                                detector.reset()
                                continue
                else:
                    interruption_audio.clear()
                    interruption_frames = 0
                scores = detector.predict(chunk)
                score = max((float(value) for value in scores.values()), default=0.0)
                if on_activity:
                    level = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2))) / 32768.0
                    on_activity(level, "")
                if score < WAKE_THRESHOLD:
                    continue
                detector.reset()
                if on_wake:
                    on_wake()
                _play_chime()
                try:
                    stream.stop()
                except Exception:
                    pass
                break
        finally:
            try:
                stream.stop()
            except Exception:
                pass

    if stop_event.is_set():
        return None
    command = _transcribe(_capture_command(stop_event))
    if stop_event.is_set():
        return None
    return _remove_wake_phrase(command) or command


def listen():
    """Record one utterance for the standalone audio check utility."""
    stop = threading.Event()
    audio = _capture_command(stop)
    return _transcribe(audio)
