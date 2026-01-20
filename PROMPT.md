# Prompts

## 2026-01-19: Create ARCHITECTURE.md

Create a comprehensive ARCHITECTURE.md file documenting the LAN Transfer project - a cross-platform file transfer tool for macOS/Linux using mDNS discovery and HTTP-based chunked transfers.

### Architecture Summary Discovered

**Project**: LAN Transfer - peer-to-peer file sharing over local network
**Stack**: Python 3.11+, Flet (GUI), aiohttp (HTTP), Zeroconf (mDNS)

### Key Components
| Component | File | Purpose |
|-----------|------|---------|
| GUI | `app.py` | Flet-based desktop UI with device list, transfer queue |
| Transfer Manager | `transfer.py` | Coordinates transfers, manages queue |
| Discovery | `discovery.py` | mDNS service registration & peer discovery |
| Server | `server.py` | HTTP receiver for incoming transfers |
| Client | `client.py` | HTTP sender for outgoing transfers |
| State | `state.py` | Persistence for resumable transfers |
| Utils | `utils.py` | Hashing, formatting, file I/O helpers |

### Implementation Plan

1. **Create ARCHITECTURE.md** with:
   - Big Picture - What the project does, core design decisions
   - Component Overview - 7 modules and their responsibilities
   - Data Flow Diagrams - Outgoing/incoming/resumable transfer flows
   - Network Protocol - HTTP endpoints, mDNS service type
   - Async Architecture - Event loop, concurrency model
   - Error Handling - Retry logic, resilience patterns
   - Key Files Reference - Where to find what
   - Development Workflow - Build, lint, run commands

2. **Verify** - Ensure the file accurately reflects the codebase
