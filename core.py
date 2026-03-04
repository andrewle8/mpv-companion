"""
Shared core: MpvIPC, OllamaClient, and constants.
Imported by both companion.py (CLI) and panel.py (GUI).
No pynput/rich/PyQt6 dependencies here — kept lightweight.
"""

import base64
import json
import os
import platform
import socket
import tempfile
import threading

import httpx

# ---------------------------------------------------------------------------
# Platform config
# ---------------------------------------------------------------------------
SYSTEM = platform.system()

if SYSTEM == "Windows":
    MPV_SOCKET = r"\\.\pipe\mpvsocket"
    SCREENSHOT_PATH = os.path.join(tempfile.gettempdir(), "mpv_companion_frame.png")
    MPV_LAUNCH_CMD = r"mpv --input-ipc-server=\\.\pipe\mpvsocket <your_file>"
else:
    MPV_SOCKET = "/tmp/mpvsocket"
    SCREENSHOT_PATH = "/tmp/mpv_companion_frame.png"
    MPV_LAUNCH_CMD = "mpv --input-ipc-server=/tmp/mpvsocket <your_file>"

DEFAULT_MODEL = "qwen3.5:7b"
HOTKEY_DISPLAY = "Ctrl+Shift+A"
MAX_HISTORY_TURNS = 20

SYSTEM_PROMPT = (
    "You are a cinematic AI companion watching a film with the user. "
    "When shown a video frame, analyze composition, lighting, color, "
    "cinematography, narrative context, and emotional tone. "
    "Be conversational, insightful, and concise. "
    "The user may ask about technique, story, symbolism, or just react to what they see."
)


# ---------------------------------------------------------------------------
# mpv IPC
# ---------------------------------------------------------------------------
class MpvIPC:
    """JSON IPC bridge to a running mpv instance."""

    def __init__(self, path: str):
        self.path = path
        self._sock = None
        self._pipe = None
        self._lock = threading.Lock()
        self._req_id = 0

    def connect(self):
        if SYSTEM == "Windows":
            self._pipe = open(self.path, "r+b", buffering=0)
        else:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.connect(self.path)
            self._sock.settimeout(5.0)

    def _send(self, command: list) -> dict:
        with self._lock:
            self._req_id += 1
            payload = json.dumps({"command": command, "request_id": self._req_id}) + "\n"
            encoded = payload.encode()

            if SYSTEM == "Windows":
                self._pipe.write(encoded)
                self._pipe.flush()
                raw = self._pipe.readline()
            else:
                self._sock.sendall(encoded)
                raw = b""
                while True:
                    try:
                        chunk = self._sock.recv(4096)
                        if not chunk:
                            break
                        raw += chunk
                        if b"\n" in chunk:
                            break
                    except socket.timeout:
                        break

            target_id = self._req_id
            lines = [l for l in raw.decode(errors="replace").strip().split("\n") if l]
            for line in reversed(lines):
                try:
                    parsed = json.loads(line)
                    if parsed.get("request_id") == target_id:
                        return parsed
                except json.JSONDecodeError:
                    continue
            for line in reversed(lines):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
            return {}

    def screenshot(self, path: str) -> bool:
        result = self._send(["screenshot-to-file", path, "video"])
        return result.get("error") == "success"

    def get_time_pos(self) -> float:
        result = self._send(["get_property", "time-pos"])
        return float(result.get("data") or 0)

    def get_media_title(self) -> str:
        result = self._send(["get_property", "media-title"])
        return str(result.get("data") or "Unknown")

    def close(self):
        try:
            if self._sock:
                self._sock.close()
            if self._pipe:
                self._pipe.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------
class OllamaClient:
    """Minimal Ollama /api/chat wrapper with vision support."""

    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=90)

    def query(self, prompt: str, image_path: str | None, history: list) -> str:
        messages = list(history)

        msg: dict = {"role": "user", "content": prompt}
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as f:
                msg["images"] = [base64.b64encode(f.read()).decode()]

        messages.append(msg)

        r = self._client.post(
            f"{self.base_url}/api/chat",
            json={"model": self.model, "messages": messages, "stream": False},
        )
        r.raise_for_status()
        return r.json()["message"]["content"]

    def list_models(self) -> list[str]:
        try:
            r = self._client.get(f"{self.base_url}/api/tags", timeout=5)
            return sorted(m["name"] for m in r.json().get("models", []))
        except Exception:
            return []

    def check(self) -> bool:
        try:
            r = self._client.get(f"{self.base_url}/api/tags", timeout=5)
            models = [m["name"] for m in r.json().get("models", [])]
            return self.model in models or any(
                m.startswith(self.model.split(":")[0] + ":") for m in models
            )
        except Exception:
            return False

    def close(self):
        self._client.close()


# ---------------------------------------------------------------------------
# Cloud providers — unified interface: query(prompt, image_path, history)
# ---------------------------------------------------------------------------
def _read_image_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


PROVIDERS = {
    "ollama": {"name": "Ollama (local)", "env_key": None},
    "gemini": {"name": "Google Gemini", "env_key": "GEMINI_API_KEY"},
    "openai": {"name": "OpenAI", "env_key": "OPENAI_API_KEY"},
    "anthropic": {"name": "Anthropic", "env_key": "ANTHROPIC_API_KEY"},
}

CLOUD_MODELS = {
    "gemini": ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"],
    "openai": ["gpt-4o-mini", "gpt-4o"],
    "anthropic": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
}


class GeminiClient:
    """Google Gemini API client with vision support."""

    def __init__(self, model: str = "gemini-2.0-flash"):
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self._api_key = os.environ.get("GEMINI_API_KEY", "")
        self._client = httpx.Client(timeout=90)

    def query(self, prompt: str, image_path: str | None, history: list) -> str:
        if not self._api_key:
            return "Set GEMINI_API_KEY environment variable to use Gemini."

        parts = []
        # Add history as text context
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            parts.append({"text": f"[{role}]: {msg['content']}"})

        parts.append({"text": prompt})

        if image_path and os.path.exists(image_path):
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": _read_image_b64(image_path),
                }
            })

        r = self._client.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            params={"key": self._api_key},
            json={
                "contents": [{"parts": parts}],
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            },
        )
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    def list_models(self) -> list[str]:
        return CLOUD_MODELS["gemini"]

    def close(self):
        self._client.close()


class OpenAIClient:
    """OpenAI API client with vision support."""

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.base_url = "https://api.openai.com/v1"
        self._api_key = os.environ.get("OPENAI_API_KEY", "")
        self._client = httpx.Client(timeout=90)

    def query(self, prompt: str, image_path: str | None, history: list) -> str:
        if not self._api_key:
            return "Set OPENAI_API_KEY environment variable to use OpenAI."

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        content: list = [{"type": "text", "text": prompt}]
        if image_path and os.path.exists(image_path):
            b64 = _read_image_b64(image_path)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })

        messages.append({"role": "user", "content": content})

        r = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self.model, "messages": messages},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def list_models(self) -> list[str]:
        return CLOUD_MODELS["openai"]

    def close(self):
        self._client.close()


class AnthropicClient:
    """Anthropic API client with vision support."""

    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model
        self.base_url = "https://api.anthropic.com/v1"
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = httpx.Client(timeout=90)

    def query(self, prompt: str, image_path: str | None, history: list) -> str:
        if not self._api_key:
            return "Set ANTHROPIC_API_KEY environment variable to use Anthropic."

        messages = []
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        content: list = []
        if image_path and os.path.exists(image_path):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": _read_image_b64(image_path),
                },
            })
        content.append({"type": "text", "text": prompt})

        messages.append({"role": "user", "content": content})

        r = self._client.post(
            f"{self.base_url}/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self.model,
                "system": SYSTEM_PROMPT,
                "messages": messages,
                "max_tokens": 1024,
            },
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]

    def list_models(self) -> list[str]:
        return CLOUD_MODELS["anthropic"]

    def close(self):
        self._client.close()


def create_client(provider: str, model: str = "", **kwargs):
    """Factory to create the right client for a given provider."""
    if provider == "gemini":
        return GeminiClient(model or "gemini-2.0-flash")
    elif provider == "openai":
        return OpenAIClient(model or "gpt-4o-mini")
    elif provider == "anthropic":
        return AnthropicClient(model or "claude-sonnet-4-6")
    else:
        return OllamaClient(model or DEFAULT_MODEL, **kwargs)
