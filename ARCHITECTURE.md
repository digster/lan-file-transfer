# LAN Transfer Architecture

Cross-platform peer-to-peer file transfer application for local networks using mDNS discovery and HTTP-based chunked transfers.

## Big Picture

LAN Transfer enables users to share files between devices on the same local network without requiring a central server, cloud storage, or manual IP configuration. The application automatically discovers peers using mDNS (Bonjour/Zeroconf) and transfers files over HTTP with chunked encoding, supporting resume on interruption.

### Core Design Decisions

| Decision | Rationale |
|----------|-----------|
| **mDNS for discovery** | Zero-configuration peer detection; works across platforms |
| **HTTP for transfers** | Simple protocol, easy debugging, wide firewall compatibility |
| **Chunked transfers** | Enables progress tracking, resumability, and memory efficiency |
| **Flet for GUI** | Cross-platform Python GUI with modern look; single codebase |
| **Async I/O** | Non-blocking operations for responsive UI during large transfers |
| **Tar for folders** | Preserves directory structure; single transfer stream |

## Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                           app.py                                │
│                    (Flet GUI Application)                       │
│          Device list, Transfer queue, File picker               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
         ▼                 ▼                 ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────────┐
│ discovery.py│   │ transfer.py │   │    state.py     │
│   (mDNS)    │   │  (Manager)  │   │  (Persistence)  │
└─────────────┘   └──────┬──────┘   └─────────────────┘
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
       ┌───────────┐         ┌───────────┐
       │ client.py │         │ server.py │
       │  (Sender) │         │ (Receiver)│
       └───────────┘         └───────────┘
              │                     │
              └──────────┬──────────┘
                         ▼
                  ┌───────────┐
                  │ utils.py  │
                  │ (Helpers) │
                  └───────────┘
```

### Module Responsibilities

| Module | File | Purpose |
|--------|------|---------|
| **App** | `app.py:235` | Main Flet GUI; manages UI state, user interactions, coordinates all services |
| **Transfer Manager** | `transfer.py:83` | Orchestrates transfers; bridges UI with client/server; manages queue |
| **Discovery** | `discovery.py:39` | mDNS service registration and peer discovery via Zeroconf |
| **Server** | `server.py:40` | HTTP server (aiohttp) for receiving incoming file transfers |
| **Client** | `client.py:69` | HTTP client for sending files to peers with retry support |
| **State** | `state.py:56` | JSON persistence for resumable transfers (24h expiry) |
| **Utils** | `utils.py` | Constants, hashing, formatting, file I/O helpers |

## Data Flow

### Outgoing Transfer (Send File)

```
User selects file → GUI
       │
       ▼
TransferManager.queue_send()         ← Creates QueuedTransfer
       │
       ▼
_pending_sends queue                 ← Async queue for serialization
       │
       ▼
_send_worker() picks up              ← Background task
       │
       ▼
TransferClient.send_path()
       │
       ├─── If folder: _send_folder()
       │         │
       │         ▼
       │    Create .tar archive       ← Uncompressed for speed
       │         │
       │         ▼
       │    send_file() with tarball
       │
       ▼
POST /transfer/init                  ← Initialize with peer
       │
       ▼
POST /transfer/chunk (loop)          ← 1MB chunks with Content-Range
       │                                 Headers: X-Transfer-ID, Content-Range
       │
       ▼
POST /transfer/complete              ← Finalize, verify SHA-256 hash
       │
       ▼
Callbacks update UI                  ← on_transfer_progress, on_transfer_completed
```

### Incoming Transfer (Receive File)

```
Peer connects → HTTP Server
       │
       ▼
POST /transfer/init
       │
       ▼
Create IncomingTransfer              ← Temp file: .{id}_{filename}.part
       │
       ▼
on_transfer_started callback → UI
       │
       ▼
POST /transfer/chunk (loop)
       │
       ├─── Validate Content-Range matches progress
       ├─── Append to temp file
       ├─── Update SHA-256 hasher
       │
       ▼
POST /transfer/complete
       │
       ├─── Verify hash matches
       ├─── Rename temp → final location
       │         │
       │         ▼
       │    If .tar: Auto-extract → Delete tarball
       │
       ▼
on_transfer_completed callback → UI
```

### Resumable Transfer Flow

```
Transfer interrupted (network error)
       │
       ▼
Client retries with exponential backoff   ← 1s, 2s, 4s... up to 30s max
       │
       ├─── Max 5 retries per chunk
       │
       ▼
On reconnect: POST /transfer/init with resume_id
       │
       ▼
Server returns resume_offset               ← Bytes already received
       │
       ▼
Client seeks to offset, continues
```

## Network Protocol

### mDNS Service

| Property | Value |
|----------|-------|
| Service Type | `_lantransfer._tcp.local.` |
| Port | `8765` (default) |
| TXT Records | `version=1.0`, `device={hostname}` |

### HTTP API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/status` | Health check |
| `POST` | `/transfer/init` | Start/resume transfer |
| `POST` | `/transfer/chunk` | Upload chunk |
| `POST` | `/transfer/complete` | Finalize transfer |
| `GET` | `/transfer/{id}/status` | Query transfer state |
| `DELETE` | `/transfer/{id}` | Cancel transfer |

### Chunk Transfer Headers

```http
POST /transfer/chunk HTTP/1.1
X-Transfer-ID: abc12345
Content-Range: bytes 0-1048575/52428800
Content-Type: application/octet-stream
```

### Transfer Init Request/Response

```json
// Request
{
  "filename": "document.pdf",
  "size": 52428800,
  "hash": "sha256:abc123...",
  "resume_id": "optional-previous-id"
}

// Response
{
  "transfer_id": "abc12345",
  "resume_offset": 0,
  "status": "ready"
}
```

## Async Architecture

### Event Loop Structure

The application runs on a single asyncio event loop managed by Flet:

```python
# Entry point (app.py:714)
ft.app(target=main_async)  # Flet manages the event loop

# Inside main_async:
await discovery.start()          # Registers mDNS service
await transfer_manager.start()   # Starts HTTP server + send worker
```

### Concurrency Model

| Component | Concurrency Pattern |
|-----------|---------------------|
| GUI Updates | Flet's `page.update()` (thread-safe) |
| mDNS Browser | Runs in separate thread (`ServiceBrowser`), callbacks via `loop.call_soon_threadsafe` |
| Send Queue | `asyncio.Queue` with single worker task |
| HTTP Server | aiohttp's async request handlers |
| File I/O | `aiofiles` for non-blocking reads/writes |
| Tarball Creation | `run_in_executor` (thread pool) to avoid blocking |

### Key Async Patterns

```python
# Thread-safe callback from mDNS thread (discovery.py:155)
self._loop.call_soon_threadsafe(self.on_peer_added, peer)

# Non-blocking file hash (utils.py:28)
async with aiofiles.open(file_path, "rb") as f:
    while chunk := await f.read(CHUNK_SIZE):
        sha256_hash.update(chunk)

# Blocking tarball in thread pool (client.py:141)
await loop.run_in_executor(None, self._create_tarball, folder_path, tarball_path)
```

## Error Handling & Resilience

### Retry Strategy

| Parameter | Value | Location |
|-----------|-------|----------|
| Max retries | 5 | `client.py:72` |
| Initial delay | 1 second | `utils.py:15` |
| Max delay | 30 seconds | `utils.py:14` |
| Backoff | Exponential (×2) | `client.py:421` |

### Transfer States

```
pending → connecting → transferring ←→ retrying → completed
                   ↘               ↗      ↓
                    → cancelled ←─────── failed

For folders:
pending → tarring → connecting → transferring → completed
                                      ↓
Incoming folders:
transferring → extracting → completed
```

### Failure Modes

| Failure | Handling |
|---------|----------|
| Network timeout | Retry with backoff |
| Hash mismatch | Fail transfer, delete temp file |
| File not found | Immediate failure |
| Peer offline | Discovery removes from list |
| Mid-transfer cancel | Set cancel flag, cleanup temp files |

## Key Files Reference

| Need to... | Look at... |
|------------|------------|
| Understand the UI layout | `app.py:285` (`_build_ui`) |
| Modify transfer protocol | `server.py:107` (routes), `client.py:338` (chunk sending) |
| Change discovery service type | `utils.py:13` (`SERVICE_TYPE`) |
| Adjust chunk size | `utils.py:11` (`CHUNK_SIZE = 1MB`) |
| Add transfer state persistence | `state.py:56` (`StateManager`) |
| Change retry behavior | `client.py:72`, `utils.py:14-16` |
| Modify tarball handling | `client.py:167` (create), `server.py:381` (extract) |

## Development Workflow

### Prerequisites

```bash
# Python 3.11+ required
python --version  # Should be 3.11+

# Install uv (recommended)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Setup & Run

```bash
# Install dependencies
uv sync

# Run from source
uv run lantransfer
# Or: uv run python -m lantransfer

# Run with verbose logging (if needed)
python -m lantransfer
```

### Build Executable

```bash
# Install build dependencies
uv sync --extra build

# Build for current platform
uv run python build.py

# Output:
# - macOS: dist/LANTransfer.app
# - Linux: dist/LANTransfer
# - Windows: dist/LANTransfer.exe
```

### Code Quality

```bash
# Install dev dependencies
uv sync --extra dev

# Lint
uv run ruff check src/

# Type check
uv run mypy src/

# Auto-fix lint issues
uv run ruff check --fix src/
```

### Project Structure

```
file-transfer-claude/
├── src/lantransfer/        # Main package
│   ├── __init__.py         # Version info
│   ├── __main__.py         # Entry point
│   ├── app.py              # Flet GUI (722 lines)
│   ├── transfer.py         # Transfer orchestration
│   ├── discovery.py        # mDNS peer discovery
│   ├── server.py           # HTTP receiver
│   ├── client.py           # HTTP sender
│   ├── state.py            # Persistence
│   └── utils.py            # Shared utilities
├── build.py                # Build script
├── pyproject.toml          # Project config
└── ARCHITECTURE.md         # This file
```

## Configuration Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `DEFAULT_PORT` | 8765 | HTTP server port |
| `CHUNK_SIZE` | 1MB | Transfer chunk size |
| `CONNECTION_TIMEOUT` | 30s | HTTP timeout |
| `MAX_RETRY_DELAY` | 30s | Max backoff delay |
| `STATE_CLEANUP_AGE` | 24h | Resume state expiry |

## Data Storage

```
~/.lantransfer/
├── downloads/              # Received files (default location)
│   ├── received_file.pdf
│   └── extracted_folder/
└── transfers.json          # Resumable transfer state
```

## Platform Notes

| Platform | Notes |
|----------|-------|
| **macOS** | Full support; builds .app bundle |
| **Linux** | Full support; requires `xdg-open` for folder opening |
| **Windows** | Untested but should work; uses `flet pack` |

### Firewall Considerations

- Port 8765 (TCP) must be open for HTTP transfers
- mDNS uses port 5353 (UDP) for discovery
- Both peers must be on the same local network segment
