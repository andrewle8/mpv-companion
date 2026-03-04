# mpv AI Companion

Ask questions about what you're watching. Grabs frames from mpv over IPC, sends them to an LLM, shows the response in a side panel or terminal.

Works on Windows and macOS. Each query captures the current frame and sends it to the model.

## Requirements

- [mpv](https://mpv.io)
- [Ollama](https://ollama.com) running locally, or a cloud API key
- Python 3.10+

## Install

```bash
pip install -r requirements.txt
# or
pip install .
```

**macOS**: pynput needs Accessibility permissions for hotkeys. System Settings > Privacy & Security > Accessibility > add Terminal.

**Windows**: run as normal user, no elevation needed. pywin32 is not required.

## Models

You need a **vision model**. Text-only models won't see anything. Pick any vision-capable model from [Ollama's library](https://ollama.com/search?c=vision) and switch models in the panel's settings dropdown.

### Cloud providers (GUI only)

Set an API key to unlock. Switch providers in the settings panel.

| Provider | Env variable | Model |
|---|---|---|
| Google Gemini | `GEMINI_API_KEY` | gemini-2.5-flash |
| OpenAI | `OPENAI_API_KEY` | gpt-4.1-mini |
| Anthropic | `ANTHROPIC_API_KEY` | claude-sonnet-4-6 |

```bash
export GEMINI_API_KEY="your-key-here"    # add to ~/.zshrc or ~/.bashrc
```

## Usage

### 1. Start mpv with IPC

```bash
# macOS/Linux
mpv --input-ipc-server=/tmp/mpvsocket your_film.mkv

# Windows
mpv --input-ipc-server=\\.\pipe\mpvsocket your_film.mkv
```

Tip: alias this so you don't have to type it every time.

### 2. Start the companion

```bash
python panel.py              # GUI panel (snaps to mpv window)
python companion.py          # CLI mode
```

```
--model gemma3:4b           pick a different model
--ollama-url http://...      remote Ollama server
```

## Controls

| | |
|---|---|
| Type + Enter | Captures the current frame and sends to the model |
| Ctrl+Space (CLI) | Pre-capture a frame, then type your question |
| `/clear` | Reset conversation |
| `/quit` / Ctrl+C | Exit |

The model remembers earlier questions for the whole session. Only text history is sent on follow-ups (no old images) to keep things fast.

## Architecture

```
mpv
  IPC socket / named pipe
    core.py    MpvIPC, 4 LLM clients (Ollama, Gemini, OpenAI, Anthropic)
      panel.py       PyQt6 floating panel, snaps to mpv, model picker
      companion.py   CLI fallback (Ollama only)
```
