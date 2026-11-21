# OrbitDesk

A lightweight Windows voice assistant built with PySide6, on-device wake-word detection, local Faster-Whisper transcription, and local LLM orchestration via Ollama.

## Architecture & Features

- **Wake Word**: OpenWakeWord for "Hey Jarvis" (idle runs on ONNX without activating Whisper). Optional custom `jarvis.onnx` can be placed at `%LOCALAPPDATA%\OrbitDesk\models\jarvis.onnx` or configured with `JARVIS_SINGLE_WAKE_MODEL`.
- **Speech-to-Text**: Local CPU transcription using Faster-Whisper (`base.en`) with voice activity detection (VAD).
- **Text-to-Speech**: Edge TTS streaming audio playback with Windows SAPI5 offline fallback. Configurable via `JARVIS_VOICE`.
- **Desktop UI**: PySide6 dashboard, system tray integration, audio waveform monitor, and floating status orb.
- **Local & Cloud LLMs**: Local Ollama backend (`qwen2.5:3b`, `qwen2.5-coder:3b`, `qwen2.5-coder:7b`) with optional OpenAI / Anthropic API fallbacks.
- **Security & Permissions**: Action allowlists for launching applications, shell diagnostics, media/volume controls, and interactive permission prompts for write operations.

## Quick Start

1. Run the setup script:
   ```cmd
   setup.bat
   ```
2. Launch the assistant:
   ```cmd
   run.bat
   ```

OrbitDesk starts minimized to the system tray. Use the hotword **"Hey Jarvis"** or interact directly through the dashboard.

### Local Model Profiles

Under **Settings → AI assistance model**, you can switch between preconfigured profiles:
- **Fast**: `qwen2.5:3b`
- **Coding**: `qwen2.5-coder:3b`
- **Quality**: `qwen2.5-coder:7b`

You can also trigger downloads and switches by voice (e.g., *"switch to coding model"*). Ollama must be running locally.

## Configuration (.env)

Create a `.env` file in the project root for optional cloud keys and voice settings:

```dotenv
OPENAI_API_KEY=your_openai_key
ANTHROPIC_API_KEY=your_anthropic_key
JARVIS_VOICE=en-GB-RyanNeural
```

## Running Tests

Run the test suite using pytest or unittest:

```powershell
python -m unittest discover tests
```

To install development dependencies:
```powershell
pip install -e ".[dev]"
```
