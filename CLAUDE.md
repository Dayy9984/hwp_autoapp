# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

---

# Inserty AI — Developer Guide

## Project Overview

**Inserty AI** is an AI-assisted desktop application for editing HWP (Hangul Word Processor) documents. It connects to a running HWP instance via Windows COM automation.

### Tech Stack
- **Frontend**: React 18.3 + TypeScript 5.4 + Vite 5.4 + Tailwind CSS 3.4
- **Desktop**: Electron 33.2 (Main + Preload + Renderer)
- **Backend**: Python 3.12 + pyhwpx (HWP COM automation)
- **State**: Zustand 4.5
- **Database**: better-sqlite3 (local SQLite)
- **IPC**: JSON-RPC over stdio (Electron ↔ Python)

### Supported HWP Versions
- HWP 2018/2020 (32-bit)
- HWP 2022/2024 (64-bit)

### Key Features
1. **Track Changes**: Accept/reject document edits (bulk or partial)
2. **AI Chat**: Document context-aware AI conversation with streaming
3. **RAG**: OpenAI Vector Stores + file_search API
4. **Multi-Document**: Manage multiple open HWP windows simultaneously
5. **Window Binding**: Auto-detect and bind to running HWP instances

## Development Commands

```bash
pnpm dev             # Start dev mode
pnpm build           # Full build (Python Nuitka + Electron installer)
pnpm build:python    # Python backend only
```

## Architecture

```
React Frontend (Renderer)
  └─ IPC (contextBridge)
Electron Main Process
  └─ subprocess stdio (JSON-RPC)
Python Backend
```

## Critical: HWP COM Automation

### WindowHandle vs HWnd

```python
# CORRECT — works on both 32-bit and 64-bit HWP
hwnd = hwp.XHwpWindows.Item(0).WindowHandle

# WRONG — fails on HWP 2020 and earlier
hwnd = hwp.XHwpWindows.Item(0).HWnd
```

### Document Name

```python
# CORRECT
active_doc = xdocs.Active_XHwpDocument
active_path = active_doc.FullName if active_doc else ''
```

## IPC Pattern

```typescript
// Frontend
const result = await window.electronAPI.documents.select(type, index)

// Preload
documents: { select: (type, index) => ipcRenderer.invoke('documents:select', { type, index }) }

// Main
ipcMain.handle('documents:select', async (_, args) => pythonBridge.call('documents:select', args))
```

## CVD Format

HTML-like markup representing HWP document structure. IDs must be bare integers:

```xml
<p id="1" font-size="12pt">text</p>
<td id="2" colspan="2"><p id="3">cell</p></td>
```

Use `id: 4` in commands, never `id: cell-4`.

## Key Files

| File | Purpose |
|------|---------|
| `python/hwp_com_process.py` | Main JSON-RPC server |
| `python/services/file_search_service.py` | OpenAI Vector Stores RAG |
| `python/llm/streaming_client.py` | LLM communication |
| `electron/main/index.ts` | Electron main process |
| `electron/services/python-bridge.ts` | Python bridge |
| `src/stores/document-store.ts` | Document state |
