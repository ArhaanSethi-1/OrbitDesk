# OrbitDesk Jarvis

OrbitDesk is a Windows voice assistant with a PySide6 dashboard, tray controls, a floating Arc Reactor orb, on-device wake detection, local speech transcription, and guarded desktop automation.

## Features

- OpenWakeWord detection for **Hey Jarvis**, with no Whisper inference during idle listening.
- Optional custom `jarvis.onnx` wake model for the standalone **Jarvis** phrase. Place it at `%LOCALAPPDATA%\OrbitDesk\models\jarvis.onnx`, or set `JARVIS_SINGLE_WAKE_MODEL` to a model path.
- Faster Whisper `base.en` transcription on CPU with VAD and silence-based command capture.
- Edge TTS with an optional SAPI5 offline fallback and interruptible audio playback. Set `JARVIS_VOICE` to select a different Edge voice.
- PySide6-only dashboard, tray-first startup, visual waveform, animated orb, action audit, and permission dialogs.
- Dictation into the focused window and explicit prompts to ChatGPT, Claude, or Antigravity.
- Permission-gated controls for audio, media, brightness, window state, launching Start Menu programs, external AI prompts, and a small read-only terminal diagnostic allowlist.
- Optional API-key fallback for ChatGPT, Claude, and Gemini-compatible prompts.

## Install and run

Run `setup.bat`. Select one of the setup choices:

1. **Complete Setup with Local AI** installs dependencies and offers to pull `qwen2.5:3b` through an installed Ollama. The model download is opt-in.
2. **Lightweight Setup** installs voice and desktop capabilities without downloading a local AI model. Optional cloud keys make general questions use the configured API provider.
3. **Test Audio & Microphone** installs dependencies and records a short microphone check.
4. **Exit**.

Python 3.10, 3.11, or 3.12 is supported. Once setup completes, run `run.bat`. The application starts minimized to the tray; say “Hey Jarvis” to activate an interaction, or open the dashboard from the tray.

### Switching local models

Open **Settings → AI assistance model**, choose a profile, then select **Download and switch model**. The model is pulled from the already-installed local Ollama service and the choice is saved under `%LOCALAPPDATA%\OrbitDesk\settings.json`. You can also say “Hey Jarvis, switch to coding model”, “switch to fast model”, or “switch to strong model”. Voice requests start the download automatically. OrbitDesk does not install Ollama itself.

The profiles are Fast (`qwen2.5:3b`), Coding (`qwen2.5-coder:3b`), and Higher quality (`qwen2.5-coder:7b`). The 7B profile may be slow or memory constrained on an 8 GB RAM / 4 GB VRAM PC. It is downloaded only when selected.

## Optional API keys

Create a local `.env` file in the project directory. Do not commit it:

```dotenv
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
JARVIS_VOICE=en-GB-RyanNeural
```

The assistant uses these keys only for explicitly requested AI prompts, or for general questions when Lightweight Setup is selected. Sending a prompt to an external AI service requires permission unless that provider was explicitly set to Always Allow.

## Command examples

- “Jarvis, type I’ll be there at 3.”
- “Ask ChatGPT to summarise this paragraph: …”
- “Prompt Claude with …”
- “Send to Antigravity …”
- “Set volume to 30 percent”, “mute volume”, “skip track”, or “set brightness to 60 percent”.
- “Launch Notepad” or “run command whoami”.
- “Search the web for …” or “open website example.com”.

Permission-sensitive actions show an Allow Once / Always Allow / Deny prompt and accept a spoken “yes, proceed” or “deny”. Terminal requests are confined to read-only diagnostics and do not invoke a shell.

## Diagnostics and tests

Application diagnostics use a rotating log at `%LOCALAPPDATA%\OrbitDesk\logs\orbitdesk.log`.

Run the tests with:

```powershell
python -m unittest discover -v
```
