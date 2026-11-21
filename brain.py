"""
OrbitDesk brain: routes commands to built-in tools or to the local Qwen model.

Every action that touches the computer goes through safeguards.authorize().
OrbitDesk never runs arbitrary commands, deletes files, or closes programs.
"""

import ast
import datetime
import json
import math
import operator
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from pathlib import Path

from safeguards import authorize, Decision
import automation
import safeguards
import model_manager

try:
    import psutil
except ImportError:
    psutil = None


# ---------- CONFIGURATION ----------

PROJECT_DIR = Path(__file__).resolve().parent

OLLAMA_URL = "http://127.0.0.1:11434"
MODEL = model_manager.PROFILES[model_manager.load_profile()]["model"]
KEEP_ALIVE = "15m"        # keep Qwen loaded in memory between questions
MAX_REPLY_TOKENS = 400    # shorter replies come back faster
HISTORY_TURNS = 8         # question/answer pairs remembered
MAX_INPUT_CHARS = 4000
MAX_TIMER_SECONDS = 12 * 3600

SYSTEM_PROMPT = (
    "You are OrbitDesk, a personal assistant running locally on the user's laptop. "
    "Reply in concise, polished British English. "
    "Address the user as sir now and then, not in every line. "
    "Keep answers short unless asked for detail. "
    "You can only use the narrowly-scoped built-in tools OrbitDesk provides. "
    "Never claim you performed an action unless a tool confirms it."
)

# A courtesy filter that gives a clear refusal. It is NOT the real protection:
# OrbitDesk has no tool that can run commands or delete files in the first place.
BLOCKED_PHRASES = (
    "run this command",
    "execute this command",
    "run powershell",
    "run cmd",
    "execute shell",
    "modify safeguards",
    "grant yourself permission",
    "modify your own code",
    "delete my files",
    "delete all my files",
    "delete all files",
    "format my drive",
    "disable windows defender",
    "disable antivirus",
)

# Commands that carry pasted content (error messages, text to translate...) are
# exempt from the phrase filter, otherwise "explain error: failed to run
# powershell script" would be refused.
CONTENT_TASK = re.compile(r"^(?:summari[sz]e|explain|translate|rewrite|make)\b", re.IGNORECASE)

BRAVE_OPENED = "Opening your default browser, sir."
BROWSER_NAMES = ("brave", "brave browser", "browser", "web browser")

WEBSITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "reddit": "https://www.reddit.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "antigravity": "https://antigravity.google",
    "wikipedia": "https://www.wikipedia.org",
    "spotify": "https://open.spotify.com",
    "netflix": "https://www.netflix.com",
    "instagram": "https://www.instagram.com",
    "discord": "https://discord.com/app",
    "whatsapp": "https://web.whatsapp.com",
    "amazon": "https://www.amazon.com",
    "google drive": "https://drive.google.com",
    "google maps": "https://maps.google.com",
    "stackoverflow": "https://stackoverflow.com",
    "stack overflow": "https://stackoverflow.com",
    "twitch": "https://www.twitch.tv",
}

# Desktop apps: ("exe", program) runs a program, ("uri", address) uses Windows
# URI handlers, ("cmd", name) finds a command on PATH such as VS Code's "code".
APPS = {
    "notepad": ("exe", "notepad.exe"),
    "calculator": ("exe", "calc.exe"),
    "paint": ("exe", "mspaint.exe"),
    "task manager": ("exe", "taskmgr.exe"),
    "file explorer": ("exe", "explorer.exe"),
    "explorer": ("exe", "explorer.exe"),
    "control panel": ("exe", "control.exe"),
    "snipping tool": ("uri", "ms-screenclip:"),
    "settings": ("uri", "ms-settings:"),
    "vs code": ("cmd", "code"),
    "vscode": ("cmd", "code"),
}

FOLDERS = {
    "desktop": "Desktop",
    "downloads": "Downloads",
    "documents": "Documents",
    "pictures": "Pictures",
    "music": "Music",
    "videos": "Videos",
    "project": None,  # OrbitDesk's own project folder
}

# Windows "known folder" IDs, so folders redirected to OneDrive are found
# where Windows really keeps them.
FOLDER_GUIDS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}

TIMER_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
}

UNIT_ALIASES = {
    "km": "km", "kilometre": "km", "kilometres": "km",
    "kilometer": "km", "kilometers": "km",
    "mi": "miles", "mile": "miles", "miles": "miles",
    "kg": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "c": "c", "celsius": "c",
    "f": "f", "fahrenheit": "f",
    "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "ft": "ft", "feet": "ft", "foot": "ft",
}

CONVERSIONS = {
    ("km", "miles"): lambda v: v * 0.621371,
    ("miles", "km"): lambda v: v / 0.621371,
    ("kg", "lb"): lambda v: v * 2.20462,
    ("lb", "kg"): lambda v: v / 2.20462,
    ("c", "f"): lambda v: v * 9 / 5 + 32,
    ("f", "c"): lambda v: (v - 32) * 5 / 9,
    ("m", "ft"): lambda v: v * 3.28084,
    ("ft", "m"): lambda v: v / 3.28084,
}

# Words that look like domains but are really file names (e.g. notes.txt).
FILE_EXTENSIONS = {
    "txt", "pdf", "exe", "py", "docx", "xlsx", "pptx", "zip", "png",
    "jpg", "jpeg", "mp3", "mp4", "bat", "ps1", "csv", "json", "md",
}

TASK_TEMPLATES = {
    "summarise": "Summarise the following text in three short bullet points:\n\n{text}",
    "formal": (
        "Rewrite the following text in a polished, formal tone. "
        "Return only the rewritten text:\n\n{text}"
    ),
    "explain_error": (
        "Explain this error message in simple terms and suggest the most "
        "likely fix:\n\n{text}"
    ),
    "translate": (
        "Translate the following text into {language}. "
        "Return only the translation:\n\n{text}"
    ),
}

ALLOWED_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "abs": abs, "round": round,
}
ALLOWED_NAMES = {"pi": math.pi, "e": math.e}
BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}


# ---------- HELPERS ----------

def _checked_pow(left, right):
    """Refuse powers that would take huge amounts of memory or time."""
    if abs(right) > 1000:
        raise ValueError("Exponent too large")
    if isinstance(left, int) and isinstance(right, int) and right > 0 and abs(left) > 1:
        # Estimate the result size from bit length before computing it.
        if abs(left).bit_length() * right > 100_000:
            raise ValueError("Result too large")
    return left ** right


def safe_eval(expression):
    """Evaluate arithmetic and a few maths functions. No names, no attributes."""
    if len(expression) > 120:
        raise ValueError("Expression too long")

    tree = ast.parse(expression.replace("^", "**"), mode="eval")

    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name) and node.id in ALLOWED_NAMES:
            return ALLOWED_NAMES[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in BIN_OPS:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow):
                return _checked_pow(left, right)
            return BIN_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY_OPS:
            return UNARY_OPS[type(node.op)](evaluate(node.operand))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ALLOWED_FUNCS
            and not node.keywords
        ):
            return ALLOWED_FUNCS[node.func.id](*[evaluate(a) for a in node.args])
        raise ValueError("Unsupported expression")

    result = evaluate(tree.body)
    if isinstance(result, complex):
        raise ValueError("Result is not a real number")
    if isinstance(result, float) and (math.isnan(result) or math.isinf(result)):
        raise ValueError("Result is not a finite number")
    return result


def _is_safe_https(url):
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
    )


def _describe_seconds(seconds):
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs or not parts:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return ", ".join(parts)


def _normalise_command(command):
    """
    Tidy a typed or spoken command before routing.

    Whisper adds capital letters, commas and full stops ("Hello, OrbitDesk.",
    "Set timer for 10 minutes."), which would otherwise miss the patterns below.
    """
    text = (command or "").strip().replace("\u2019", "'")
    # Drop a leading wake word: "Hey Jarvis, open calculator" -> "open calculator".
    text = re.sub(r"^(?:(?:hey|ok|okay|hi|hello)\s+)?(?:orbitdesk|jarvis)\s*[,.:!]?\s+", "", text, flags=re.IGNORECASE)
    # Drop trailing punctuation.
    text = re.sub(r"[\s.!?,;]+$", "", text)
    return text


def _known_folder(name):
    """Ask Windows where a standard folder really lives. Returns None on any failure."""
    guid_text = FOLDER_GUIDS.get(name)
    if guid_text is None:
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        guid = GUID.from_buffer_copy(uuid.UUID(guid_text).bytes_le)
        path_ptr = ctypes.c_wchar_p()
        status = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
        )
        if status != 0:
            return None
        try:
            return Path(path_ptr.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
    except Exception:
        return None


# ---------- LOCAL AI (QWEN VIA OLLAMA) ----------

class LocalAI:
    def __init__(self, base_url=OLLAMA_URL, model=MODEL):
        self.base_url = base_url
        self.model = model
        self._lock = threading.Lock()
        self._history = deque(maxlen=HISTORY_TURNS * 2)

    def set_model(self, model):
        if model not in {entry["model"] for entry in model_manager.PROFILES.values()}:
            raise ValueError("That model is not in OrbitDesk's approved model list.")
        with self._lock:
            self.model = model
            self._history.clear()
        return model

    def _post(self, path, payload, timeout):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def warm_up(self):
        """Load the model into memory in the background so the first reply is quicker."""
        try:
            with self._lock:
                model = self.model
            self._post("/api/generate", {"model": model, "keep_alive": KEEP_ALIVE}, timeout=300)
        except Exception:
            pass  # OrbitDesk still works; the first reply will just take longer.

    def clear(self):
        with self._lock:
            self._history.clear()

    def ask(self, text, remember=True):
        # The lock is held only while reading or writing history, never during the HTTP call.
        with self._lock:
            history = list(self._history)
            model = self.model

        # The model has no clock, so tell it the date and time on every request.
        now = datetime.datetime.now().strftime("%A %d %B %Y, %I:%M %p")
        system_prompt = f"{SYSTEM_PROMPT} The current date and time is {now}."

        messages = [{"role": "system", "content": system_prompt}]
        messages += history
        messages.append({"role": "user", "content": text})

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            "options": {
                "temperature": 0.6,
                "num_ctx": 4096,
                "num_predict": MAX_REPLY_TOKENS,
            },
        }

        try:
            data = self._post("/api/chat", payload, timeout=120)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                cloud = self._cloud_fallback(text, remember)
                if cloud:
                    return cloud
                return (
                    f"The model {model} isn't installed yet, sir. "
                    f"Run: ollama pull {model}"
                )
            return f"My local AI returned error {error.code}, sir."
        except urllib.error.URLError as error:
            cloud = self._cloud_fallback(text, remember)
            if cloud:
                return cloud
            if isinstance(error.reason, TimeoutError):
                return "My response timed out, sir. Please try again."
            return "I can't reach my local AI, sir. Please make sure Ollama is running."
        except TimeoutError:
            cloud = self._cloud_fallback(text, remember)
            if cloud:
                return cloud
            return "My response timed out, sir. Please try again."
        except ValueError:
            return "My local AI sent a reply I couldn't read, sir."
        except Exception as error:
            print(f"[AI error] {error}")
            return "Something went wrong while I was thinking, sir."

        answer = (data.get("message") or {}).get("content", "").strip()
        if not answer:
            return "I couldn't generate a response, sir."

        if remember:
            with self._lock:
                if self.model == model:
                    self._history.append({"role": "user", "content": text})
                    self._history.append({"role": "assistant", "content": answer})

        return answer

    def _cloud_fallback(self, text, remember):
        if not automation.cloud_mode_enabled():
            return None
        try:
            answer = automation.ask_cloud(text)
        except automation.AutomationError as error:
            return str(error)
        if answer and remember:
            with self._lock:
                self._history.append({"role": "user", "content": text})
                self._history.append({"role": "assistant", "content": answer})
        return answer


# ---------- BRAIN ----------

class OrbitDeskBrain:
    def __init__(self, warm_up=True, permission_request=None):
        self.running = True
        self.notify = self._print_notification  # the GUI replaces this
        self.permission_request = permission_request
        self.model_profile_request = None
        self.audit_callback = None
        self.ai = LocalAI()

        self._timers = {}
        self._timer_counter = 0
        self._timer_lock = threading.Lock()
        self._notes = []
        self._notes_lock = threading.Lock()

        self.routes = self._compile_routes([
            (r"^(exit|quit|shutdown orbitdesk|go offline)$", self._shutdown),
            (r"^(hello|hi|hey),?( orbitdesk)?$", self._greet),
            (r"^(?:(?:what(?:'s| is)|tell me) your name|your name|who are you)$", self._identity),
            (r"^(?:what(?:'s| is) the time(?: now)?|what time is it(?: now)?|(?:the )?current time"
             r"|tell me the time|time)$", self._time),
            (r"^(?:what(?:'s| is) (?:the |today'?s )?date(?: today)?|(?:today'?s )?date"
             r"|what day is it(?: today)?|what is today)$", self._date),
            (r"^(help|what can you do|show commands|list commands)$", self._help),
            (r"^(?:switch|change|use|select)(?:\s+to)?(?:\s+the)?\s+(?P<profile>fast|faster|light|lightweight|low|coding|code|strong|high|higher|quality)(?:\s+(?:ai\s+)?(?:assistance|model|profile))?$", self._switch_model),
            (r"^(?:switch|change|use|select)(?:\s+to)?(?:\s+the)?\s+(?P<profile>fast|faster|light|lightweight|low|coding|code|strong|high|higher|quality)(?:\s+(?:ai\s+)?(?:assistance|model|profile))?$", self._switch_model),
            (r"^(system (?:status|info)|battery|cpu|ram|memory usage|how is my (?:pc|laptop|computer))$",
             self._system_status),
            (r"^(?:calculate|calc|compute)\s+(?P<expression>.+)$", self._calculate),
            (r"^(?=.*\b(?:plus|minus|times|over|divided by|multiplied by)\b)(?P<expression>\d[\d\w\s().,+\-*/%^]*)$", self._calculate),
            (r"^what is (?P<expression>.+)$", self._calculate),
            (r"^(?:type|dictate)\s+(?P<text>.+)$", self._type_text),
            (r"^ask chatgpt\s+(?P<prompt>.+)$", self._prompt_chatgpt),
            (r"^prompt claude with\s+(?P<prompt>.+)$", self._prompt_claude),
            (r"^send to antigravity\s+(?P<prompt>.+)$", self._prompt_antigravity),
            (r"^(?:run|execute) command\s+(?P<command>.+)$", self._run_command),
            (r"^(?:mute|unmute) (?:the )?volume$", self._volume_mute),
            (r"^(?:set )?volume(?: to)? (?P<level>\d{1,3})(?: percent)?$", self._volume_set),
            (r"^(?:play|pause|toggle) music$", self._media_toggle),
            (r"^(?:skip|next)(?: track| song)?$", self._media_next),
            (r"^(?:previous|last)(?: track| song)$", self._media_previous),
            (r"^(?:set )?brightness(?: to)? (?P<level>\d{1,3})(?: percent)?$", self._brightness_set),
            (r"^lock (?:the )?(?:pc|computer|screen)$", self._lock_pc),
            (r"^minimi[sz]e (?:the )?(?:active )?window$", self._minimize_window),
            (r"^maximi[sz]e (?:the )?(?:active )?window$", self._maximize_window),
            (r"^convert\s+(?P<amount>-?[\d.]+)\s+(?P<unit_from>[a-z]+)\s+(?:to|in)\s+(?P<unit_to>[a-z]+)$",
             self._convert),
            (r"^(?:set\s+)?(?:a\s+)?timer\s+(?:for\s+)?(?P<amount>[\d.]+)\s+(?P<unit>[a-z]+)$",
             self._set_timer),
            (r"^remind me (?:in|after)\s+(?P<amount>[\d.]+)\s+(?P<unit>[a-z]+)(?:\s+to\s+(?P<label>.+))?$",
             self._set_timer),
            (r"^(?:list|show) timers$", self._list_timers),
            (r"^cancel (?:all |the |my )?timers?$", self._cancel_timers),
            (r"^note[:\s]+(?P<text>.+)$", self._add_note),
            (r"^(?:show|list) notes$", self._show_notes),
            (r"^clear notes$", self._clear_notes),
            (r"^summari[sz]e[:\s]+(?P<text>.+)$", self._summarise),
            (r"^(?:rewrite|make)\s+(?:this\s+)?(?:more\s+)?formal[:\s]+(?P<text>.+)$", self._formal),
            (r"^explain (?:this |the )?error[:\s]+(?P<text>.+)$", self._explain_error),
            # "translate to french: hello" / "translate to french hello" / "translate hello to french"
            (r"^translate\s+(?:to\s+)?(?P<language>[a-z]+)\s*:\s*(?P<text>.+)$", self._translate),
            (r"^translate\s+to\s+(?P<language>[a-z]+)\s+(?P<text>.+)$", self._translate),
            (r"^translate\s+(?P<text>.+?)\s+(?:in)?to\s+(?P<language>[a-z]+)$", self._translate),
            (r"^(?:reset|forget) (?:the )?conversation$", self._reset),
            (r"^(?:search youtube(?: for)?|youtube search|find on youtube)\s+(?P<query>.+)$",
             self._search_youtube),
            (r"^(?:search github(?: for)?|github search)\s+(?P<query>.+)$", self._search_github),
            (r"^(?:search stack ?overflow(?: for)?|stack ?overflow search)\s+(?P<query>.+)$", self._search_stackoverflow),
            (r"^(?:search wikipedia(?: for)?|wikipedia)\s+(?P<query>.+)$", self._search_wikipedia),
            (r"^(?:search(?: the web| google)? for|google search|look up)\s+(?P<query>.+)$",
             self._search_web),
            (r"^open website\s+(?P<name>.+)$", self._open_website_command),
            (r"^(?P<verb>open|launch|start)\s+(?P<name>.+)$", self._open_name),
        ])

        # Load Qwen in the background so the first real question is faster.
        if warm_up and model_manager.load_profile() != "strong":
            threading.Thread(target=self.ai.warm_up, daemon=True).start()

    @staticmethod
    def _compile_routes(routes):
        # DOTALL lets pasted multi-line text (error messages...) match ".+".
        flags = re.IGNORECASE | re.DOTALL
        return [(re.compile(pattern, flags), handler) for pattern, handler in routes]

    def _interactive_allowed(self, action, target, summary):
        decision, reason = authorize(action, target=target)
        if decision == Decision.ALLOW:
            return True, reason
        if decision != Decision.APPROVAL_REQUIRED or self.permission_request is None:
            return False, reason
        choice = self.permission_request(action, target, summary)
        if choice == "always":
            safeguards.grant_always(action, target)
            return True, "Allowed by user."
        if choice == "once":
            return True, "Allowed once by user."
        return False, "Permission denied."

    def _type_text(self, match):
        text = match.group("text").strip()
        try:
            target_window = automation.get_active_window()
        except automation.AutomationError as error:
            return str(error)
        if target_window is None:
            return "I couldn't identify the focused window, sir."
        allowed, reason = self._interactive_allowed(
            "type_text", "active window", f"Type into the focused app: {text[:180]}"
        )
        if not allowed:
            return reason
        try:
            automation.type_text(text, target_window)
            return "Typed your text into the focused window."
        except automation.AutomationError as error:
            return str(error)

    def _prompt_ai(self, target, prompt):
        allowed, reason = self._interactive_allowed(
            "prompt_external_ai", target, f"Send this prompt to {target.title()}: {prompt[:180]}"
        )
        if not allowed:
            return reason
        try:
            return automation.prompt_ai(target, prompt)
        except automation.AutomationError as error:
            return str(error)

    def _prompt_chatgpt(self, match):
        return self._prompt_ai("chatgpt", match.group("prompt"))

    def _prompt_claude(self, match):
        return self._prompt_ai("claude", match.group("prompt"))

    def _prompt_antigravity(self, match):
        return self._prompt_ai("antigravity", match.group("prompt"))

    def _run_command(self, match):
        command = match.group("command").strip()
        allowed, reason = self._interactive_allowed(
            "run_approved_command", "diagnostic", f"Run this diagnostic command: {command}"
        )
        if not allowed:
            return reason
        try:
            return automation.run_approved_command(command)
        except automation.AutomationError as error:
            return str(error)

    def _system_action(self, action, value=None, permission_action="system_control"):
        allowed, reason = self._interactive_allowed(permission_action, action, f"Perform {action.replace('_', ' ')}")
        if not allowed:
            return reason
        try:
            return automation.system_action(action, value)
        except automation.AutomationError as error:
            return str(error)

    def _volume_mute(self, match):
        return self._system_action("volume_mute", permission_action="volume_control")

    def _volume_set(self, match):
        level = int(match.group("level"))
        if level > 100:
            return "Volume must be between 0 and 100 percent."
        return self._system_action("volume_set", level, "volume_control")

    def _media_toggle(self, match):
        return self._system_action("media_play_pause")

    def _media_next(self, match):
        return self._system_action("media_next")

    def _media_previous(self, match):
        return self._system_action("media_previous")

    def _brightness_set(self, match):
        level = int(match.group("level"))
        if level > 100:
            return "Brightness must be between 0 and 100 percent."
        return self._system_action("brightness_set", level)

    def _lock_pc(self, match):
        return self._system_action("lock_pc")

    def _minimize_window(self, match):
        return self._system_action("minimize_window")

    def _maximize_window(self, match):
        return self._system_action("maximize_window")

    @staticmethod
    def _print_notification(message):
        print(f"\nOrbitDesk: {message}")

    # ---------- ROUTER ----------

    def respond(self, command):
        text = _normalise_command(command)

        if not text:
            return "I'm listening, sir."
        if len(text) > MAX_INPUT_CHARS:
            return "That's rather long, sir. Could you shorten it?"

        lowered = text.lower()
        if not CONTENT_TASK.match(lowered) and any(phrase in lowered for phrase in BLOCKED_PHRASES):
            if self.audit_callback:
                self.audit_callback("policy filter", "Blocked")
            return (
                "I can't perform that action, sir. "
                "It is blocked by OrbitDesk's security policy."
            )

        for pattern, handler in self.routes:
            match = pattern.search(text)
            if match:
                try:
                    reply = handler(match)
                except Exception as error:
                    print(f"[Tool error] {error}")
                    if self.audit_callback:
                        self.audit_callback(handler.__name__, "Failed")
                    return "That tool ran into a problem, sir."
                if reply is not None:
                    if self.audit_callback:
                        blocked = any(term in str(reply).casefold() for term in (
                            "blocked", "not permitted", "permission denied", "not on the approved list",
                        ))
                        self.audit_callback(handler.__name__, "Blocked" if blocked else "Completed")
                    return reply

        return self.ai.ask(text)

    # ---------- GENERAL ----------

    def _shutdown(self, _match):
        self.running = False
        return "Until next time, sir."

    def _greet(self, _match):
        return "Hello, sir. All systems are operational."

    def _identity(self, _match):
        return "I am OrbitDesk, your personal assistant, running locally on this laptop."

    def _time(self, _match):
        decision, reason = authorize("tell_time")
        if decision != Decision.ALLOW:
            return reason
        return f"The current time is {datetime.datetime.now():%I:%M %p}, sir."

    def _date(self, _match):
        decision, reason = authorize("tell_date")
        if decision != Decision.ALLOW:
            return reason
        return f"Today is {datetime.datetime.now():%A, %d %B %Y}, sir."

    def _help(self, _match):
        return (
            "I can answer questions, search and open websites, dictate text into the focused app, "
            "send prompts to ChatGPT, Claude or Antigravity, control audio and brightness, "
            "launch installed apps, run approved diagnostic commands, calculate, convert units, "
            "set timers and manage notes. Sensitive actions ask for permission first."
        )

    def _reset(self, _match):
        self.ai.clear()
        return "Conversation cleared, sir."

    def _switch_model(self, match):
        profile = model_manager.PROFILE_ALIASES.get(match.group("profile").casefold())
        if profile is None:
            return "Choose fast, coding, or strong model, sir."
        if self.model_profile_request is None:
            return "Model switching is available from the dashboard settings, sir."
        self.model_profile_request(profile)
        warning = model_manager.PROFILES[profile]["warning"]
        if warning:
            return f"I’ll download the {model_manager.PROFILES[profile]['name']} model. {warning}"
        return f"I’ll download and switch to the {model_manager.PROFILES[profile]['name']} model, sir."

    # ---------- SYSTEM ----------

    def _system_status(self, _match):
        decision, reason = authorize("system_status")
        if decision != Decision.ALLOW:
            return reason

        if psutil is None:
            free_gb = shutil.disk_usage(Path.home()).free / 1e9
            return (
                f"Running on {platform.system()} {platform.release()}. "
                f"{free_gb:.1f} GB free on your home drive, sir. "
                "Install psutil for CPU, memory and battery details."
            )

        cpu = psutil.cpu_percent(interval=0.3)
        memory = psutil.virtual_memory()
        parts = [
            f"CPU at {cpu:.0f} percent",
            f"memory at {memory.percent:.0f} percent ({memory.available / 1e9:.1f} GB free)",
        ]
        battery = psutil.sensors_battery()
        if battery is not None:
            status = "plugged in" if battery.power_plugged else "on battery"
            parts.append(f"battery at {battery.percent:.0f} percent, {status}")
        return "Status: " + "; ".join(parts) + ", sir."

    # ---------- CALCULATOR AND CONVERTER ----------

    def _calculate(self, match):
        decision, reason = authorize("calculate")
        if decision != Decision.ALLOW:
            return reason

        expression = match.group("expression").strip().lower()
        expression = re.sub(r"\b(plus|added to)\b", "+", expression)
        expression = re.sub(r"\b(minus|subtract(?:ed)?(?: by| from)?)\b", "-", expression)
        expression = re.sub(r"\b(times|multiplied by|multiply by)\b", "*", expression)
        expression = re.sub(r"\b(divided by|over)\b", "/", expression)
        expression = re.sub(r"\s+", " ", expression).strip()
        try:
            result = safe_eval(expression)
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError):
            return "I couldn't calculate that expression, sir."

        try:
            if isinstance(result, float):
                text = f"{result:.10g}"
            else:
                text = f"{result:,}"
        except ValueError:
            # Python refuses to print integers with thousands of digits.
            return "That result is too large to display, sir."

        if len(text) > 80:
            return "That result is too long to read out, sir."
        return f"The answer is {text}, sir."

    def _convert(self, match):
        decision, reason = authorize("convert_units")
        if decision != Decision.ALLOW:
            return reason

        unit_from = UNIT_ALIASES.get(match.group("unit_from").lower())
        unit_to = UNIT_ALIASES.get(match.group("unit_to").lower())
        conversion = CONVERSIONS.get((unit_from, unit_to))
        if conversion is None:
            return "I can't convert between those units yet, sir."

        try:
            amount = float(match.group("amount"))
        except ValueError:
            return "I didn't catch that amount, sir."

        result = conversion(amount)
        return f"{amount:g} {match.group('unit_from')} is {result:.2f} {match.group('unit_to')}, sir."

    # ---------- TIMERS ----------

    def _start_timer(self, seconds, label):
        with self._timer_lock:
            self._timer_counter += 1
            timer_id = self._timer_counter

            def finish():
                with self._timer_lock:
                    self._timers.pop(timer_id, None)
                message = f"Time's up, sir: {label}." if label else "Your timer has finished, sir."
                self.notify(message)

            timer = threading.Timer(seconds, finish)
            timer.daemon = True
            self._timers[timer_id] = {
                "timer": timer,
                "label": label,
                "due": time.monotonic() + seconds,
            }
            timer.start()
        return timer_id

    def _set_timer(self, match):
        decision, reason = authorize("set_timer")
        if decision != Decision.ALLOW:
            return reason

        try:
            amount = float(match.group("amount"))
        except ValueError:
            return "I didn't catch the duration, sir."

        unit = TIMER_UNITS.get(match.group("unit").lower())
        if unit is None or amount <= 0:
            return "I didn't understand that duration, sir. Try 'set timer for 10 minutes'."

        seconds = min(amount * unit, MAX_TIMER_SECONDS)
        label = (match.groupdict().get("label") or "").strip()
        self._start_timer(seconds, label)
        return f"Timer set for {_describe_seconds(seconds)}, sir."

    def _list_timers(self, _match):
        with self._timer_lock:
            items = list(self._timers.values())

        if not items:
            return "You have no timers running, sir."

        now = time.monotonic()
        lines = [
            f"{item['label'] or 'timer'}: {_describe_seconds(max(0, item['due'] - now))} left"
            for item in items
        ]
        return "Active timers: " + "; ".join(lines) + ", sir."

    def _cancel_timers(self, _match):
        decision, reason = authorize("cancel_timer")
        if decision != Decision.ALLOW:
            return reason

        with self._timer_lock:
            for item in self._timers.values():
                item["timer"].cancel()
            count = len(self._timers)
            self._timers.clear()

        return f"Cancelled {count} timer(s), sir." if count else "There were no timers to cancel, sir."

    # ---------- NOTES (kept in memory for this session) ----------

    def _add_note(self, match):
        decision, reason = authorize("manage_notes")
        if decision != Decision.ALLOW:
            return reason

        with self._notes_lock:
            self._notes.append(match.group("text").strip())
            count = len(self._notes)
        return f"Noted, sir. You have {count} note(s)."

    def _show_notes(self, _match):
        with self._notes_lock:
            notes = list(self._notes)
        if not notes:
            return "You have no notes, sir."
        return "Your notes: " + "; ".join(
            f"{number}. {note}" for number, note in enumerate(notes, 1)
        ) + "."

    def _clear_notes(self, _match):
        decision, reason = authorize("manage_notes")
        if decision != Decision.ALLOW:
            return reason

        with self._notes_lock:
            self._notes.clear()
        return "Notes cleared, sir."

    # ---------- AI TASKS ----------

    def _ai_task(self, template_key, **fields):
        decision, reason = authorize("answer_question")
        if decision != Decision.ALLOW:
            return reason
        prompt = TASK_TEMPLATES[template_key].format(**fields)
        return self.ai.ask(prompt, remember=False)

    def _summarise(self, match):
        return self._ai_task("summarise", text=match.group("text"))

    def _formal(self, match):
        return self._ai_task("formal", text=match.group("text"))

    def _explain_error(self, match):
        return self._ai_task("explain_error", text=match.group("text"))

    def _translate(self, match):
        return self._ai_task(
            "translate",
            language=match.group("language"),
            text=match.group("text"),
        )

    # ---------- WEB ----------

    def open_brave(self, url=None):
        if url is None:
            decision, reason = authorize("open_approved_app", target="browser")
        else:
            if not _is_safe_https(url):
                return "That web address isn't permitted, sir."
            decision, reason = authorize("open_approved_website", target=url)
            if decision != Decision.ALLOW and _is_safe_https(url):
                allowed, reason = self._interactive_allowed(
                    "navigate_website", url, f"Open this website: {url}"
                )
                if allowed:
                    webbrowser.open(url)
                    return BRAVE_OPENED
                return reason

        if decision != Decision.ALLOW:
            return reason

        try:
            webbrowser.open(url or "about:blank")
            return BRAVE_OPENED
        except OSError:
            return "I couldn't launch the browser, sir."

    def _browse(self, url, success_message):
        result = self.open_brave(url)
        return success_message if result == BRAVE_OPENED else result

    def open_website(self, name):
        name = name.strip().lower()

        if name.startswith("https://"):
            return self.open_brave(name)

        if name in WEBSITES:
            return self.open_brave(WEBSITES[name])

        if re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", name):
            if name.rsplit(".", 1)[-1] not in FILE_EXTENSIONS:
                return self.open_brave("https://" + name)

        return None

    def _open_website_command(self, match):
        name = match.group("name").strip()
        result = self.open_website(name)
        return result or "I don't recognise that website shortcut, sir. Try a domain such as example.com."

    def _search_web(self, match):
        query = match.group("query").strip()
        decision, reason = authorize("search_web", target=query)
        if decision != Decision.ALLOW:
            return reason
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
        return self._browse(url, f"Searching the web for {query}, sir.")

    def _search_youtube(self, match):
        query = match.group("query").strip()
        decision, reason = authorize("search_youtube", target=query)
        if decision != Decision.ALLOW:
            return reason
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
        return self._browse(url, f"Searching YouTube for {query}, sir.")

    def _search_github(self, match):
        query = match.group("query").strip()
        decision, reason = authorize("search_web", target=query)
        if decision != Decision.ALLOW:
            return reason
        url = "https://github.com/search?q=" + urllib.parse.quote_plus(query)
        return self._browse(url, f"Searching GitHub for {query}, sir.")

    def _search_stackoverflow(self, match):
        query = match.group("query").strip()
        decision, reason = authorize("search_web", target=query)
        if decision != Decision.ALLOW:
            return reason
        url = "https://stackoverflow.com/search?q=" + urllib.parse.quote_plus(query)
        return self._browse(url, f"Searching Stack Overflow for {query}, sir.")

    def _search_wikipedia(self, match):
        query = match.group("query").strip()
        decision, reason = authorize("search_web", target=query)
        if decision != Decision.ALLOW:
            return reason
        url = "https://en.wikipedia.org/w/index.php?search=" + urllib.parse.quote_plus(query)
        return self._browse(url, f"Searching Wikipedia for {query}, sir.")

    # ---------- OPENING APPS AND FOLDERS ----------

    def open_app(self, name):
        if name not in APPS:
            return None

        decision, reason = authorize("open_approved_app", target=name)
        if decision != Decision.ALLOW:
            return reason

        kind, target = APPS[name]
        try:
            if kind == "exe":
                subprocess.Popen([target])
            elif kind == "uri":
                os.startfile(target)
            elif kind == "cmd":
                path = shutil.which(target)
                if path is None:
                    return f"I couldn't find {name.title()} on this computer, sir."
                subprocess.Popen(
                    [path],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            return f"Opening {name.title()}, sir."
        except OSError:
            return f"I couldn't open {name.title()}, sir."

    def _resolve_folder(self, name):
        if name == "project":
            return PROJECT_DIR

        # First choice: where Windows says the folder is (handles OneDrive redirection).
        known = _known_folder(name)
        if known is not None and known.is_dir():
            return known

        # Fallback: guess under the home folder, trying OneDrive first.
        home = Path.home()
        for base in (home / "OneDrive", home):
            candidate = base / FOLDERS[name]
            if candidate.is_dir():
                return candidate
        return None

    def open_folder(self, name):
        if name not in FOLDERS:
            return None

        folder = self._resolve_folder(name)
        if folder is None:
            return f"I couldn't find your {name} folder, sir."

        decision, reason = authorize("open_approved_folder", target=name)
        if decision != Decision.ALLOW:
            return reason

        try:
            os.startfile(str(folder))
            return f"Opening your {name} folder, sir."
        except OSError:
            return f"I couldn't open your {name} folder, sir."

    def _open_name(self, match):
        verb = match.group("verb").lower()
        name = match.group("name").strip().lower()
        name = re.sub(r"^(?:the|my)\s+", "", name)

        if name in BROWSER_NAMES:
            return self.open_brave()

        # "open the calculator app" / "open my downloads folder"
        name = re.sub(r"\s+(?:folder|app|application|program|website|site|browser)$", "", name)
        if name == "brave":
            return self.open_brave()

        # Try each opener in turn and stop at the first one that knows the name.
        for opener in (self.open_app, self.open_folder, self.open_website):
            result = opener(name)
            if result is not None:
                return result

        if verb in {"launch", "start", "open"}:
            allowed, reason = self._interactive_allowed(
                "launch_program", name, f"Launch the installed program {name}"
            )
            if not allowed:
                return reason
            try:
                return automation.launch_program(name)
            except automation.AutomationError as error:
                return str(error)

        if verb in ("open", "launch"):
            return f"I don't have a shortcut for {name}, sir."

        return None  # "start learning Python" is a question for the AI.


if __name__ == "__main__":
    # Keyboard test without the GUI: py -3.10 brain.py
    orbitdesk = OrbitDeskBrain()

    print("OrbitDesk BRAIN TEST")
    print("Type 'help' for commands or 'exit' to quit.")

    while orbitdesk.running:
        try:
            user_command = input("\nYou: ")
            print(f"OrbitDesk: {orbitdesk.respond(user_command)}")
        except (KeyboardInterrupt, EOFError):
            print("\nOrbitDesk: Until next time, sir.")
            break
