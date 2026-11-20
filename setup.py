"""Interactive dependency, local-model and microphone setup."""

import shutil
import subprocess
import sys
import json
from pathlib import Path


def install_dependencies():
    print("\nInstalling voice, desktop automation, and dashboard dependencies…")
    return subprocess.call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])


def test_audio():
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        default_device = sd.default.device[0]
        print("Input device:", devices[default_device]["name"] if default_device is not None else "Windows default")
        print("Recording 3 seconds. Speak normally…")
        recording = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="int16")
        sd.wait()
        print("Microphone peak:", int(recording.max()), "minimum:", int(recording.min()))
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass
        return 0
    except Exception as error:
        print("Audio test failed:", error)
        return 1


def prepare_wake_models():
    try:
        from openwakeword.utils import download_models

        print("\nPreparing the small Hey Jarvis wake-word models…")
        download_models(model_names=["hey_jarvis"])
    except Exception as error:
        print("Wake-word model download could not finish now:", error)
        print("The first microphone start will retry; an internet connection is needed once.")


def main():
    print("\n[1] Complete Setup with Local AI (downloads the Ollama model, about 2 GB)")
    print("[2] Lightweight Setup (voice, desktop automation, cloud API options)")
    print("[3] Test Audio & Microphone")
    print("[4] Exit")
    choice = input("Choose 1–4: ").strip()
    if choice == "4":
        return 0
    if choice not in {"1", "2", "3"}:
        print("Choose one of the listed options.")
        return 1

    result = install_dependencies()
    if result:
        return result

    prepare_wake_models()

    setup_mode = "lightweight" if choice == "2" else "local" if choice == "1" else "audio-test"
    Path(".orbitdesk_setup.json").write_text(
        json.dumps({"mode": setup_mode}, indent=2), encoding="utf-8"
    )

    if choice == "1":
        ollama = shutil.which("ollama")
        if not ollama:
            print("Ollama is not installed. Install it from https://ollama.com, then run: ollama pull qwen2.5:3b")
        else:
            answer = input("Download qwen2.5:3b now? [Y/n] ").strip().casefold()
            if answer in {"", "y", "yes"}:
                result = subprocess.call([ollama, "pull", "qwen2.5:3b"])
                if result:
                    print("Model download failed; retry later with: ollama pull qwen2.5:3b")
    elif choice == "2":
        print("Lightweight setup selected. Add OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY to .env for API prompts.")
    else:
        return test_audio()

    print("\nSetup complete. Start OrbitDesk with run.bat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
