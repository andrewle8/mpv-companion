#!/usr/bin/env python3
"""
mpv AI Companion — CLI mode.
Watch films with a local AI companion via Ollama + mpv IPC.
"""

import argparse
import os
import tempfile
import threading
import time

import httpx
from pynput import keyboard
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from core import (
    DEFAULT_MODEL,
    HOTKEY_DISPLAY,
    MAX_HISTORY_TURNS,
    MPV_LAUNCH_CMD,
    MPV_SOCKET,
    SCREENSHOT_PATH,
    SYSTEM_PROMPT,
    MpvIPC,
    OllamaClient,
)

console = Console()


class Companion:
    def __init__(self, model: str, ollama_url: str):
        self.mpv = MpvIPC(MPV_SOCKET)
        self.ollama = OllamaClient(model, ollama_url)
        self.history: list[dict] = []
        self._frame_lock = threading.Lock()
        self._preshot_path: str | None = None
        self._preshot_ts: float = 0.0
        self.media_title = "Unknown"

    def _on_hotkey(self):
        ts = self.mpv.get_time_pos()
        shot_path = os.path.join(
            tempfile.gettempdir(), f"mpv_companion_{int(ts * 1000)}.png"
        )
        ok = self.mpv.screenshot(shot_path)
        if ok:
            with self._frame_lock:
                self._preshot_path = shot_path
                self._preshot_ts = ts
            mins, secs = int(ts // 60), int(ts % 60)
            console.print(
                f"\n[cyan][Hotkey] Frame captured at {mins:02d}:{secs:02d} "
                f"-- type your question and press Enter[/cyan]"
            )
        else:
            console.print("\n[red][Hotkey] Frame capture failed[/red]")

    def _start_hotkey_listener(self):
        combo = keyboard.HotKey(
            keyboard.HotKey.parse("<ctrl>+<space>"),
            self._on_hotkey,
        )

        def on_press(key):
            try:
                combo.press(key)
            except Exception:
                pass

        def on_release(key):
            try:
                combo.release(key)
            except Exception:
                pass

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
        return listener

    def _query(self, user_input: str):
        with self._frame_lock:
            preshot = self._preshot_path
            preshot_ts = self._preshot_ts
            self._preshot_path = None

        # Determine anchor timestamp
        current_pos = self.mpv.get_time_pos()
        t = preshot_ts if preshot else current_pos

        # Clean up preshot — we'll recapture all 3 frames via seek
        if preshot and os.path.exists(preshot):
            os.unlink(preshot)

        # Capture 3 frames spread over 5 seconds for temporal context
        timestamps = [max(0.0, t - 5.0), max(0.0, t - 2.5), t]
        image_paths: list[str] = []
        tmp_dir = tempfile.gettempdir()

        for i, ts in enumerate(timestamps):
            self.mpv.seek(ts)
            time.sleep(0.15)
            path = os.path.join(tmp_dir, f"mpv_companion_{i}_{int(ts * 1000)}.png")
            if self.mpv.screenshot(path):
                image_paths.append(path)

        # Seek back to where the video was playing
        self.mpv.seek(current_pos)

        mins, secs = int(t // 60), int(t % 60)
        ts_str = f"{mins:02d}:{secs:02d}"

        console.print(f"[dim]Frames: {len(image_paths)} captured around {ts_str} | Thinking...[/dim]")

        if not self.history:
            prompt = (
                f"[System: {SYSTEM_PROMPT}]\n\n"
                f"Film: {self.media_title}\n"
                f"Timestamp: {ts_str}\n\n"
                f"{user_input}"
            )
        else:
            prompt = f"[{ts_str}] {user_input}"

        try:
            response = self.ollama.query(prompt, image_paths or None, self.history)
        except httpx.HTTPStatusError as e:
            response = f"Ollama HTTP error: {e.response.status_code}"
        except httpx.ConnectError:
            response = "Cannot reach Ollama. Is it running?"
        except Exception as e:
            response = f"Error: {e}"
        finally:
            for p in image_paths:
                if os.path.exists(p):
                    os.unlink(p)

        self.history.append({"role": "user", "content": prompt})
        self.history.append({"role": "assistant", "content": response})

        max_msgs = MAX_HISTORY_TURNS * 2
        if len(self.history) > max_msgs:
            self.history = self.history[-max_msgs:]

        return response, ts_str

    def run(self):
        console.print(
            Panel.fit(
                f"[bold cyan]mpv AI Companion[/bold cyan]\n"
                f"Model:  [green]{self.ollama.model}[/green]\n"
                f"Hotkey: [yellow]{HOTKEY_DISPLAY}[/yellow]  captures frame immediately\n"
                f"Or just type a question -- frame is captured at Enter\n\n"
                f"[dim]/clear[/dim]  reset history\n"
                f"[dim]/history[/dim]  show turn count\n"
                f"[dim]/quit[/dim]  exit",
                title="[bold]Ready[/bold]",
                border_style="cyan",
            )
        )

        console.print("[dim]Connecting to mpv...[/dim]")
        try:
            self.mpv.connect()
            self.media_title = self.mpv.get_media_title()
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            console.print(f"\n[red]Could not connect to mpv IPC socket.[/red]")
            console.print(f"[yellow]Launch mpv first:[/yellow]")
            console.print(f"  [bold]{MPV_LAUNCH_CMD}[/bold]\n")
            return

        console.print(f"[green]Connected[/green] -- [bold]{self.media_title}[/bold]\n")

        listener = self._start_hotkey_listener()

        try:
            while True:
                try:
                    user_input = input("\n[Ask] ").strip()
                except (EOFError, KeyboardInterrupt):
                    break

                if not user_input:
                    continue

                if user_input.lower() == "/quit":
                    break

                if user_input.lower() == "/clear":
                    self.history.clear()
                    self._preshot_path = None
                    console.print("[yellow]History cleared.[/yellow]")
                    continue

                if user_input.lower() == "/history":
                    turns = len(self.history) // 2
                    console.print(f"[dim]{turns} turn(s) in history.[/dim]")
                    continue

                response, ts = self._query(user_input)

                console.print(
                    Panel(
                        Text(response, overflow="fold"),
                        title=f"[cyan]AI @ {ts}[/cyan]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )

        finally:
            listener.stop()
            self.mpv.close()
            self.ollama.close()
            console.print("\n[dim]Companion closed.[/dim]")


def main():
    parser = argparse.ArgumentParser(
        description="mpv AI Companion -- watch films with a local AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Launch mpv first:\n  {MPV_LAUNCH_CMD}",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Ollama model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--ollama-url", default="http://localhost:11434",
        help="Ollama base URL (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--no-check", action="store_true",
        help="Skip Ollama model availability check",
    )
    args = parser.parse_args()

    if not args.no_check:
        console.print("[dim]Checking Ollama...[/dim]")
        client = OllamaClient(args.model, args.ollama_url)
        if not client.check():
            console.print(f"[red]Model '{args.model}' not found in Ollama.[/red]")
            console.print(f"[yellow]Run: ollama pull {args.model}[/yellow]")
            console.print("[dim]Proceeding anyway -- will fail on first query if not available.[/dim]\n")
        client.close()

    companion = Companion(model=args.model, ollama_url=args.ollama_url)
    companion.run()


if __name__ == "__main__":
    main()
