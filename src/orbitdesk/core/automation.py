"""Permission-aware helpers for typing into the desktop and AI web apps."""

import json
import os
import shlex
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_AI_TARGETS = {
    "chatgpt": ("ChatGPT", "https://chatgpt.com/"),
    "claude": ("Claude", "https://claude.ai/"),
}

PROJECT_DIR = Path(__file__).resolve().parents[3]
USER_CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OrbitDesk"


def load_ai_targets():
    """Return built-in AI sites extended by valid user-local entries."""
    targets = dict(DEFAULT_AI_TARGETS)
    try:
        configured = json.loads((USER_CONFIG_DIR / "ai_targets.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return targets
    if not isinstance(configured, dict):
        return targets
    for key, value in configured.items():
        if not isinstance(value, dict):
            continue
        title, url = value.get("name"), value.get("url")
        try:
            parsed = urllib.parse.urlparse(str(url))
        except ValueError:
            continue
        if (isinstance(title, str) and title.strip() and parsed.scheme == "https"
                and parsed.hostname and not parsed.username and not parsed.password):
            targets[str(key).casefold()] = (title.strip()[:60], url)
    return targets


class AutomationError(RuntimeError):
    pass


def run_approved_command(command):
    """Run a small allowlist of read-only diagnostic commands without a shell."""
    try:
        parts = shlex.split(command, posix=False)
    except ValueError as error:
        raise AutomationError(f"Could not parse command: {error}") from error
    if not parts:
        raise AutomationError("Enter a command to run.")
    executable = Path(parts[0].strip('"')).name.casefold()
    args = parts[1:]
    allowed = {
        "whoami": {"whoami.exe"},
        "hostname": {"hostname.exe"},
        "ipconfig": {"ipconfig.exe"},
        "tasklist": {"tasklist.exe"},
        "systeminfo": {"systeminfo.exe"},
        "python": {"python.exe"},
        "python.exe": {"python.exe"},
        "git": {"git.exe"},
        "git.exe": {"git.exe"},
    }
    allowed_names = allowed.get(executable)
    if not allowed_names:
        raise AutomationError("For safety, only approved read-only diagnostic commands can run.")
    if executable in {"python", "python.exe"} and args not in (["--version"], ["-V"]):
        raise AutomationError("Python is allowed here only for --version.")
    if executable in {"git", "git.exe"}:
        safe_git_args = {
            ("status",), ("status", "--short"),
            ("log", "--oneline", "-5"), ("log", "-5", "--oneline"),
            ("diff",), ("diff", "--stat"),
        }
        if tuple(arg.casefold() for arg in args) not in safe_git_args:
            raise AutomationError("Git is allowed here only for status, short status, a five-line summary, or a diff summary.")
    if executable in {"whoami", "whoami.exe", "hostname", "hostname.exe", "ipconfig", "ipconfig.exe", "tasklist", "tasklist.exe", "systeminfo", "systeminfo.exe"} and args:
        raise AutomationError("Extra arguments are not allowed for this diagnostic command.")
    command_path = shutil.which(allowed_names.pop())
    if command_path is None:
        raise AutomationError(f"{executable} is not available on PATH.")
    try:
        result = subprocess.run(
            [command_path, *args], cwd=PROJECT_DIR,
            capture_output=True, text=True, timeout=15, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AutomationError(f"Command failed: {error}") from error
    output = (result.stdout or result.stderr or "Command completed without output.").strip()
    return output[:3000]


def _desktop_modules():
    try:
        import pyautogui
        import pygetwindow
        import pyperclip
    except ImportError as error:
        raise AutomationError("Desktop automation dependencies are not installed.") from error
    return pyautogui, pygetwindow, pyperclip


def get_active_window():
    """Return the current desktop foreground window before a permission prompt changes focus."""
    _pyautogui, pygetwindow, _pyperclip = _desktop_modules()
    return pygetwindow.getActiveWindow()


def type_text(text, target_window=None):
    """Paste text into the captured foreground window, preserving Unicode."""
    pyautogui, _pygetwindow, pyperclip = _desktop_modules()
    if target_window is not None:
        try:
            if target_window.isMinimized:
                target_window.restore()
            target_window.activate()
            time.sleep(0.2)
        except Exception as error:
            raise AutomationError(f"Could not restore the target window: {error}") from error
    pyperclip.copy(str(text))
    pyautogui.hotkey("ctrl", "v")


def _find_window(title_fragment):
    _pyautogui, pygetwindow, _pyperclip = _desktop_modules()
    titles = pygetwindow.getAllTitles()
    matches = [title for title in titles if title_fragment.casefold() in title.casefold()]
    for title in matches:
        windows = pygetwindow.getWindowsWithTitle(title)
        for window in windows:
            if window:
                return window
    return None


def _load_env():
    """Load simple KEY=value entries without overriding process environment."""
    env_file = PROJECT_DIR / ".env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    except OSError:
        pass


def _api_fallback(target, prompt):
    _load_env()
    try:
        if target == "chatgpt" and os.environ.get("OPENAI_API_KEY"):
            body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
            request = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read()).get("choices", [{}])[0].get("message", {}).get("content", "")
        if target == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
            body = {"model": "claude-haiku-4-5-20251001", "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]}
            request = urllib.request.Request(
                "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                content = json.loads(response.read()).get("content", [])
                return "\n".join(part.get("text", "") for part in content)
    except Exception as error:
        # Avoid echoing request details in UI/log output; authentication headers and
        # provider responses can contain sensitive information.
        detail = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else "network or response error"
        raise AutomationError(f"Configured {target} API request failed ({detail}).") from None
    return None


def cloud_mode_enabled():
    try:
        config = json.loads((PROJECT_DIR / ".orbitdesk_setup.json").read_text(encoding="utf-8"))
        return config.get("mode") == "lightweight"
    except (OSError, ValueError, TypeError):
        return os.environ.get("JARVIS_CLOUD_MODE", "").casefold() in {"1", "true", "yes"}


def ask_cloud(prompt):
    _load_env()
    for target, key in (("chatgpt", "OPENAI_API_KEY"), ("claude", "ANTHROPIC_API_KEY")):
        if os.environ.get(key):
            return _api_fallback(target, prompt)
    return None


def prompt_ai(target, prompt):
    """Focus a running AI window, or open its site and stage the prompt."""
    ai_targets = load_ai_targets()
    if target not in ai_targets:
        raise AutomationError("That AI target is not supported.")
    title, url = ai_targets[target]
    window = _find_window(title)
    if window is None:
        webbrowser.open(url)
        for _attempt in range(20):
            time.sleep(0.5)
            window = _find_window(title)
            if window is not None:
                break
    if window is None:
        result = _api_fallback(target, prompt)
        if result:
            return result
        raise AutomationError(f"Opened {title}, but could not find its browser window to stage the prompt. Configure the matching API key for direct API fallback.")
    try:
        if window.isMinimized:
            window.restore()
        window.activate()
        time.sleep(0.35)
        pyautogui, _pygetwindow, _pyperclip = _desktop_modules()
        click_x = int(window.left + window.width / 2)
        click_y = int(window.top + window.height - 90)
        pyautogui.click(click_x, click_y)
        type_text(prompt)
        pyautogui.press("enter")
        return f"Sent your prompt to {title}."
    except Exception as error:
        raise AutomationError(f"Could not send the prompt to {title}: {error}") from error


def find_start_menu_program(query):
    """Find a Start Menu shortcut by name; callers must request permission before launch."""
    roots = (
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    )
    query = query.strip().casefold()
    if not query or len(query) > 100:
        return None
    for root in roots:
        try:
            for shortcut in root.rglob("*.lnk"):
                if query in shortcut.stem.casefold():
                    return shortcut
        except OSError:
            continue
    return None


def launch_program(query):
    shortcut = find_start_menu_program(query)
    if shortcut is None:
        raise AutomationError(f"Could not find an installed program named {query} in the Start Menu.")
    os.startfile(str(shortcut))
    return f"Launched {shortcut.stem}."


def system_action(action, value=None):
    """Perform an explicitly selected Windows media, audio, brightness, or window action."""
    pyautogui, pygetwindow, _pyperclip = _desktop_modules()
    if action == "media_play_pause":
        pyautogui.press("playpause")
    elif action == "media_next":
        pyautogui.press("nexttrack")
    elif action == "media_previous":
        pyautogui.press("prevtrack")
    elif action in {"volume_mute", "volume_set"}:
        try:
            from pycaw.pycaw import AudioUtilities
            device = AudioUtilities.GetSpeakers()
            volume = device.EndpointVolume
            if action == "volume_mute":
                volume.SetMute(not volume.GetMute(), None)
            else:
                level = max(0, min(100, int(value))) / 100
                volume.SetMasterVolumeLevelScalar(level, None)
        except Exception as error:
            raise AutomationError(f"Windows audio control failed: {error}") from error
    elif action == "brightness_set":
        try:
            import screen_brightness_control as sbc
            sbc.set_brightness(max(0, min(100, int(value))))
        except Exception as error:
            raise AutomationError(f"Brightness control failed: {error}") from error
    elif action == "lock_pc":
        import ctypes
        if not ctypes.windll.user32.LockWorkStation():
            raise AutomationError("Windows could not lock this session.")
    elif action in {"minimize_window", "maximize_window"}:
        window = pygetwindow.getActiveWindow()
        if window is None:
            raise AutomationError("No active window was found.")
        window.minimize() if action == "minimize_window" else window.maximize()
    else:
        raise AutomationError("Unknown system action.")
    return "Done."
