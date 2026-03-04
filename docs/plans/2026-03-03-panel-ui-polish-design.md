# Panel UI Polish Design

## Goal
Make the floating panel feel like a real app, not a dev prototype. Minimal/clean style (Linear, Raycast vibes).

## Changes

### Typography
- System font stack: `-apple-system, Segoe UI, sans-serif`
- Chat text: 14px, line-height 1.6
- Status/labels: 11px, 60% white opacity

### Header
- Remove "mpv Companion" title label
- Status text becomes the only header text (provider + model + media title)
- Keep: Clear, Settings, Collapse buttons (right-aligned)

### Message bubbles
- User: right-aligned sender label, subtle blue background `rgba(100, 180, 255, 8)`, rounded corners
- Assistant: no background, thin left border accent in green
- Timestamp as muted suffix on sender label (not part of sender name)
- 14px vertical gap between messages

### Input
- QLineEdit -> QTextEdit (2-3 lines, auto-grows)
- Enter sends, Shift+Enter inserts newline
- Placeholder: "Ask about this scene..."

### Settings panel
- Remove verbose help text from labels
- Just: Provider dropdown, Model dropdown + Refresh, Ollama URL (hidden for cloud)

### Thinking state
- Replace "Thinking..." dot animation with pulsing opacity on status label

## Files
- panel.py (all changes in one file)
