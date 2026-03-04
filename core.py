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
