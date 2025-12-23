# LAN Transfer

A cross-platform file transfer tool for sending files and folders between Linux and macOS machines over local WiFi LAN. No internet required - works entirely on your local network.

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## Features

- **Automatic Device Discovery** - Devices find each other automatically via mDNS (Bonjour/Avahi)
- **Large File Support** - Streaming transfers with 1MB chunks for efficient large file handling
- **Resumable Transfers** - Automatically resume interrupted transfers from last checkpoint
- **Network Resilience** - Automatic retry with exponential backoff on connection drops
- **Folder Transfer** - Send entire folders (automatically compressed and extracted)
- **Progress Tracking** - Real-time progress bars with speed and ETA
- **Integrity Verification** - SHA-256 hash verification on transfer completion
- **Modern UI** - Clean, dark-themed Material Design interface
- **Portable** - Single executable file per platform

## Screenshots

```
┌─────────────────────────────────────────────────────────────┐
│  LAN Transfer                                          [─][□][×]│
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐  ┌───────────────────────────────┐ │
│  │ Nearby Devices      │  │ Transfer Queue                │ │
│  │ ┌─────────────────┐ │  │ ┌───────────────────────────┐ │ │
│  │ │ 🖥️ MacBook-Pro   │ │  │ │ project.zip → Mac       │ │ │
│  │ │   192.168.1.42  │ │  │ │ ████████████░░ 78% 45MB/s│ │ │
│  │ └─────────────────┘ │  │ └───────────────────────────┘ │ │
│  │ ┌─────────────────┐ │  │ ┌───────────────────────────┐ │ │
│  │ │ 🐧 Ubuntu-PC     │ │  │ │ photos/ → Ubuntu        │ │ │
│  │ │   192.168.1.55  │ │  │ │ Waiting...              │ │ │
│  │ └─────────────────┘ │  │ └───────────────────────────┘ │ │
│  └─────────────────────┘  └───────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │           Drop files here or click to browse           │ │
│  └─────────────────────────────────────────────────────────┘ │
│  Status: Ready • 192.168.1.100:8765                         │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### Using Pre-built Executables

1. Download the appropriate executable for your platform from the releases
2. Run the executable on both machines
3. Select a discovered device from the "Nearby Devices" list
4. Click the drop zone or drag files to start transferring

### Running from Source

#### Prerequisites

- Python 3.11 or higher
- [uv](https://github.com/astral-sh/uv) package manager

#### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/file-transfer-claude.git
cd file-transfer-claude

# Install dependencies using uv
uv sync

# Run the application
uv run python -m lantransfer
```

## Development

### Using Dev Containers

This project includes a devcontainer configuration for VS Code / Cursor:

1. Open the project in VS Code or Cursor
2. When prompted, click "Reopen in Container"
3. The container will set up the development environment automatically

### Manual Setup

```bash
# Create virtual environment and install dependencies
uv sync

# Install development dependencies
uv sync --all-extras

# Run the application
uv run python -m lantransfer

# Run linting
uv run ruff check src/

# Run type checking
uv run mypy src/
```

### Building Executables

Build a single-file executable for distribution:

```bash
# Install build dependencies
uv sync --extra build

# Run the build script
uv run python build.py
```

The executable will be created in the `dist/` directory:
- macOS: `dist/lantransfer-macos`
- Linux: `dist/lantransfer-linux`

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Flet GUI                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Device List  │  │ Transfer     │  │ File/Folder     │  │
│  │              │  │ Queue        │  │ Picker          │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                     Transfer Manager                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Queue        │  │ Progress     │  │ State            │  │
│  │ Management   │  │ Tracking     │  │ Persistence      │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
├────────────────────────┬────────────────────────────────────┤
│   Discovery Service    │         HTTP Transport             │
│  ┌──────────────────┐ │  ┌─────────────┐  ┌─────────────┐  │
│  │ mDNS/Zeroconf    │ │  │ Server      │  │ Client      │  │
│  │ Peer Discovery   │ │  │ (Receiver)  │  │ (Sender)    │  │
│  └──────────────────┘ │  └─────────────┘  └─────────────┘  │
└────────────────────────┴────────────────────────────────────┘
```

### Components

| Component | File | Description |
|-----------|------|-------------|
| GUI | `app.py` | Flet-based user interface |
| Discovery | `discovery.py` | mDNS service for peer discovery |
| Server | `server.py` | HTTP server for receiving files |
| Client | `client.py` | HTTP client for sending files |
| Transfer Manager | `transfer.py` | Coordinates transfers and queue |
| State | `state.py` | Persists transfer state for resume |
| Utils | `utils.py` | Helper functions |

## Transfer Protocol

### File Transfer Flow

```
Sender                              Receiver
  │                                     │
  │  POST /transfer/init               │
  │  {filename, size, hash}            │
  │─────────────────────────────────────>│
  │                                     │
  │  200 OK {transfer_id, resume_offset}│
  │<─────────────────────────────────────│
  │                                     │
  │  POST /transfer/chunk              │
  │  Content-Range: bytes X-Y/Total    │
  │─────────────────────────────────────>│
  │                 ...                  │
  │  (repeat for each chunk)            │
  │                                     │
  │  POST /transfer/complete           │
  │─────────────────────────────────────>│
  │                                     │
  │  200 OK {hash_verified: true}       │
  │<─────────────────────────────────────│
```

### Resumable Transfers

If a transfer is interrupted:

1. The receiver persists progress to `~/.lantransfer/transfers.json`
2. On reconnection, sender calls `/transfer/init` with the same parameters
3. Receiver responds with `resume_offset` indicating where to continue
4. Sender skips to the resume offset and continues

### Retry Logic

On network errors:
- Automatic retry with exponential backoff (1s, 2s, 4s, ... max 30s)
- Up to 5 retry attempts per chunk
- Progress preserved between retries

## Configuration

### Data Directory

The application stores data in `~/.lantransfer/`:

```
~/.lantransfer/
├── downloads/        # Received files
└── transfers.json    # Transfer state for resume
```

### Network

- Default port: `8765`
- Protocol: HTTP over TCP
- Discovery: mDNS/DNS-SD (`_lantransfer._tcp.local.`)

## Troubleshooting

### Devices not discovering each other

1. Ensure both devices are on the same WiFi network
2. Check firewall settings - allow UDP port 5353 (mDNS) and TCP port 8765
3. On Linux, ensure Avahi daemon is running: `sudo systemctl start avahi-daemon`

### Transfer fails with "Connection refused"

1. Check if the receiver application is running
2. Verify firewall allows incoming connections on port 8765
3. Try restarting the application on both devices

### Slow transfer speeds

1. Ensure devices are on the same local network (not through VPN)
2. Check WiFi signal strength
3. Try moving devices closer to the router

## Requirements

### macOS

- macOS 10.15 (Catalina) or later
- Bonjour (built-in)

### Linux

- Ubuntu 20.04 / Debian 11 or later (or equivalent)
- Avahi daemon for mDNS: `sudo apt install avahi-daemon`
- GTK3 for GUI: `sudo apt install libgtk-3-0`

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request


