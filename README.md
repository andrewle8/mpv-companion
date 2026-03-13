# mpv AI Companion

Ask questions about what you're watching. Captures the current frame from mpv, sends it to a vision model, and shows the response in a floating side panel or your terminal.

Windows and macOS.

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

**macOS**: pynput needs Accessibility permissions. System Settings > Privacy & Security > Accessibility > add Terminal.

## Models

You need a **vision model** -- text-only models can't see the frame. Pick any vision-capable model from [Ollama's library](https://ollama.com/search?c=vision) and switch models in the panel's settings dropdown.

### Cloud providers (GUI only)

Set an API key to unlock cloud models. Switch providers in the settings panel.

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

Tip: add an alias so you don't type the IPC flag every time.

### 2. Start the companion

```bash
python panel.py              # GUI panel (snaps to mpv window)
python companion.py          # CLI mode (Ollama only)
```

Options:
```
--model gemma3:4b           pick a different model
--ollama-url http://...      remote Ollama server
```

## Controls

| | |
|---|---|
| Type + Enter | Capture the current frame and ask the model |
| Ctrl+Space (CLI) | Freeze-frame first, then type your question |
| `/clear` | Reset conversation |
| `/quit` / Ctrl+C | Exit |

The model remembers earlier questions for the whole session. Only text history is sent on follow-ups (no old images) to keep requests fast.
