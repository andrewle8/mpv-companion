# mpv AI Companion

Watch films with a local AI companion. On-demand visual analysis via Ollama + mpv IPC.
Cross-platform: Windows and macOS/Linux.

---

## Requirements

- [mpv](https://mpv.io) installed
- [Ollama](https://ollama.com) running locally
- Python 3.10+

---

## Install

```bash
pip install -r requirements.txt
```

### macOS note
pynput requires Accessibility permissions for global hotkeys.
System Settings > Privacy & Security > Accessibility > add Terminal (or your Python binary).

### Windows note
Run terminal as normal user -- no elevation needed.
pywin32 is not required; named pipe access uses built-in file I/O.

---

## Ollama model setup

Recommended (best visual reasoning at 7B class, fits 4090 easily):
```bash
ollama pull qwen2.5-vl:7b
```

Newer option (native multimodal, released March 2026):
```bash
ollama pull qwen3.5:9b
```

---

## Usage

### Step 1 -- Launch mpv with IPC enabled

**macOS / Linux:**
```bash
mpv --input-ipc-server=/tmp/mpvsocket your_film.mkv
```

**Windows:**
```cmd
mpv --input-ipc-server=\\.\pipe\mpvsocket your_film.mkv
```

Tip: add an alias or batch script so you never have to type this manually.

### Step 2 -- Start the companion

```bash
python companion.py
```

Options:
```
--model qwen3.5:9b          use a different Ollama model
--ollama-url http://...      if Ollama runs on another machine
--no-check                   skip model availability check
```

---

## Interaction

| Action | Result |
|---|---|
| Press Ctrl+Shift+A | Captures frame immediately at that moment |
| Type question + Enter | Captures frame at the moment you press Enter |
| Hotkey then type | Uses the pre-captured frame from hotkey time |
| `/clear` | Resets conversation history |
| `/history` | Shows how many turns are in context |
| `/quit` or Ctrl+C | Exit |

History persists for the full viewing session -- the AI remembers what you discussed earlier.
Each query sends a text-only history (no images) to keep context lean and inference fast.

---

## Roadmap

- [ ] Floating PyQt6 side panel (GUI iteration)
- [ ] Voice input via Whisper
- [ ] Movie title auto-detection from filename + TMDB lookup for extra context
- [ ] Session export to markdown

---

## Architecture

```
mpv (video player)
  └── IPC socket (Unix socket / Windows named pipe)
        └── companion.py
              ├── pynput  (global hotkey listener, background thread)
              ├── mpv IPC (screenshot-to-file + metadata)
              └── Ollama /api/chat  (vision model, streaming off)
                    └── rich  (terminal UI)
```
