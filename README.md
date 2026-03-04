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

## Model setup

### Ollama (local, default)

```bash
ollama pull qwen3.5:7b        # recommended default
ollama pull qwen3.5:14b       # larger, better reasoning
```

### Cloud providers (optional)

Set an API key to enable. Switch providers in the ⚙ settings panel.

| Provider | Env variable | Recommended model |
|---|---|---|
| Google Gemini | `GEMINI_API_KEY` | gemini-2.5-flash (fast, cheap, great vision) |
| OpenAI | `OPENAI_API_KEY` | gpt-4.1-mini |
| Anthropic | `ANTHROPIC_API_KEY` | claude-sonnet-4-6 |

```bash
export GEMINI_API_KEY="your-key-here"    # add to ~/.zshrc or ~/.bashrc
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

**GUI (floating panel):**
```bash
python panel.py
```

**CLI (terminal mode):**
```bash
python companion.py
```

Options (both modes):
```
--model qwen3.5:7b          use a different Ollama model
--ollama-url http://...      if Ollama runs on another machine
```

The GUI panel snaps to the right edge of the mpv window, stays on top, and auto-detects all models installed in Ollama via a dropdown.

---

## Interaction

| Action | Result |
|---|---|
| Type question + Enter | Captures current frame and sends to AI |
| `/clear` | Resets conversation history |
| `/quit` or Ctrl+C | Exit |
| ⚙ button (GUI) | Toggle settings — model selector, Ollama URL |
| ▶ button (GUI) | Collapse panel to thin strip |

History persists for the full viewing session -- the AI remembers what you discussed earlier.
Each query sends a text-only history (no images) to keep context lean and inference fast.

---

## Roadmap

- [x] Floating PyQt6 side panel
- [ ] Voice input via Whisper
- [ ] Movie title auto-detection from filename + TMDB lookup for extra context
- [ ] Session export to markdown

---

## Architecture

```
mpv (video player)
  └── IPC socket (Unix socket / Windows named pipe)
        └── core.py  (MpvIPC, OllamaClient — shared, no GUI deps)
              ├── panel.py   (PyQt6 floating panel — primary)
              │     ├── model dropdown  (populated from Ollama /api/tags)
              │     ├── chat display    (QTextEdit, read-only)
              │     ├── input bar       (QLineEdit)
              │     └── query worker    (QThread, non-blocking)
              └── companion.py  (CLI mode — terminal fallback)
```
