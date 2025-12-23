"""Utility functions for LAN Transfer."""

import asyncio
import hashlib
import os
import random
from pathlib import Path
from typing import AsyncIterator, Callable

# Constants
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for large file transfer
DEFAULT_PORT = 8765
SERVICE_TYPE = "_lantransfer._tcp.local."
MAX_RETRY_DELAY = 30  # Maximum retry delay in seconds
INITIAL_RETRY_DELAY = 1  # Initial retry delay in seconds
CONNECTION_TIMEOUT = 30  # Connection timeout in seconds


def get_file_hash(file_path: Path) -> str:
    """Calculate SHA-256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


async def get_file_hash_async(file_path: Path) -> str:
    """Calculate SHA-256 hash of a file asynchronously."""
    import aiofiles

    sha256_hash = hashlib.sha256()
    async with aiofiles.open(file_path, "rb") as f:
        while chunk := await f.read(CHUNK_SIZE):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable size."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def format_speed(bytes_per_second: float) -> str:
    """Format transfer speed to human readable format."""
    return f"{format_size(int(bytes_per_second))}/s"


def format_time(seconds: float) -> str:
    """Format seconds to human readable time."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


async def read_file_chunks(
    file_path: Path,
    start_offset: int = 0,
    chunk_size: int = CHUNK_SIZE,
    progress_callback: Callable[[int], None] | None = None,
) -> AsyncIterator[bytes]:
    """Read file in chunks asynchronously, starting from offset."""
    import aiofiles

    total_read = start_offset
    async with aiofiles.open(file_path, "rb") as f:
        await f.seek(start_offset)
        while chunk := await f.read(chunk_size):
            yield chunk
            total_read += len(chunk)
            if progress_callback:
                progress_callback(total_read)


def get_device_name() -> str:
    """Get a friendly device name."""
    import socket

    hostname = socket.gethostname()
    # Clean up the hostname for display
    if hostname.endswith(".local"):
        hostname = hostname[:-6]
    return hostname


def get_local_ip() -> str:
    """Get the local IP address of this machine."""
    import socket

    try:
        # Create a socket to determine the local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        # Connect to a non-routable address to get local IP
        s.connect(("10.254.254.254", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def generate_transfer_id() -> str:
    """Generate a unique transfer ID."""
    import uuid

    return str(uuid.uuid4())[:8]


async def retry_with_backoff(
    func: Callable,
    max_retries: int = 5,
    initial_delay: float = INITIAL_RETRY_DELAY,
    max_delay: float = MAX_RETRY_DELAY,
    exceptions: tuple = (Exception,),
):
    """
    Retry an async function with exponential backoff.
    
    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries
        exceptions: Tuple of exceptions to catch and retry on
    
    Returns:
        Result of the function call
    
    Raises:
        The last exception if all retries fail
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except exceptions as e:
            last_exception = e
            if attempt == max_retries:
                raise

            # Add jitter to prevent thundering herd
            jitter = random.uniform(0, delay * 0.1)
            await asyncio.sleep(delay + jitter)

            # Exponential backoff with max cap
            delay = min(delay * 2, max_delay)

    raise last_exception  # type: ignore


def get_data_dir() -> Path:
    """Get the application data directory."""
    home = Path.home()
    data_dir = home / ".lantransfer"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_downloads_dir() -> Path:
    """Get the default downloads directory for received files."""
    downloads = get_data_dir() / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads


def get_folder_size(folder_path: Path) -> int:
    """Calculate total size of all files in a folder."""
    total_size = 0
    for dirpath, _, filenames in os.walk(folder_path):
        for filename in filenames:
            filepath = Path(dirpath) / filename
            total_size += filepath.stat().st_size
    return total_size


def list_folder_files(folder_path: Path) -> list[tuple[Path, int]]:
    """List all files in a folder with their relative paths and sizes."""
    files = []
    for dirpath, _, filenames in os.walk(folder_path):
        for filename in filenames:
            filepath = Path(dirpath) / filename
            relative_path = filepath.relative_to(folder_path)
            files.append((relative_path, filepath.stat().st_size))
    return files


