"""HTTP client for sending file transfers with retry support."""

import asyncio
import os
import tarfile
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import aiohttp

from lantransfer.utils import (
    CHUNK_SIZE,
    CONNECTION_TIMEOUT,
    INITIAL_RETRY_DELAY,
    MAX_RETRY_DELAY,
    get_file_hash_async,
    get_folder_size,
)


class TransferStatus(Enum):
    """Status of a file transfer."""
    PENDING = "pending"
    CONNECTING = "connecting"
    TRANSFERRING = "transferring"
    RETRYING = "retrying"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class OutgoingTransfer:
    """Represents an outgoing file transfer."""

    file_path: Path
    peer_url: str
    transfer_id: str = ""
    total_size: int = 0
    sent_bytes: int = 0
    file_hash: str = ""
    status: TransferStatus = TransferStatus.PENDING
    error: str | None = None
    retry_count: int = 0
    speed: float = 0.0  # bytes per second
    # Internal: the original path used to queue this transfer (for folder transfers, this is the folder path)
    original_path: Path | None = None
    # Internal: the key used in _cancel_flags and _active_transfers
    _transfer_key: str = ""

    @property
    def progress(self) -> float:
        """Get transfer progress as a percentage (0-100)."""
        if self.total_size == 0:
            return 0
        return (self.sent_bytes / self.total_size) * 100

    @property
    def filename(self) -> str:
        """Get the filename being transferred."""
        return self.file_path.name


@dataclass
class TransferClient:
    """Client for sending files to peers."""

    max_retries: int = 5
    chunk_size: int = CHUNK_SIZE
    timeout: int = CONNECTION_TIMEOUT

    on_transfer_started: Callable[[OutgoingTransfer], None] | None = None
    on_transfer_progress: Callable[[OutgoingTransfer], None] | None = None
    on_transfer_completed: Callable[[OutgoingTransfer], None] | None = None
    on_transfer_failed: Callable[[OutgoingTransfer, str], None] | None = None
    on_transfer_cancelled: Callable[[OutgoingTransfer], None] | None = None

    _active_transfers: dict[str, OutgoingTransfer] = field(
        default_factory=dict, init=False, repr=False
    )
    _cancel_flags: dict[str, bool] = field(default_factory=dict, init=False, repr=False)

    async def send_path(
        self,
        path: Path,
        peer_url: str,
        resume_id: str | None = None,
    ) -> OutgoingTransfer:
        """
        Send a file or folder to a peer.
        
        Args:
            path: Path to the file or folder to send
            peer_url: Base URL of the peer (e.g., http://192.168.1.42:8765)
            resume_id: Optional transfer ID to resume an interrupted transfer
        
        Returns:
            OutgoingTransfer object with final status
        """
        if not path.exists():
            raise FileNotFoundError(f"Path not found: {path}")

        if path.is_dir():
            return await self._send_folder(path, peer_url)
        else:
            return await self.send_file(path, peer_url, resume_id)

    async def _send_folder(
        self,
        folder_path: Path,
        peer_url: str,
    ) -> OutgoingTransfer:
        """
        Send a folder by compressing it to a tarball.
        
        Args:
            folder_path: Path to the folder to send
            peer_url: Base URL of the peer
        
        Returns:
            OutgoingTransfer object with final status
        """
        # Create a temporary tarball
        temp_dir = tempfile.mkdtemp()
        tarball_name = f"{folder_path.name}.tar.gz"
        tarball_path = Path(temp_dir) / tarball_name

        try:
            # Create tarball in a thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._create_tarball,
                folder_path,
                tarball_path,
            )

            # Send the tarball, passing the original folder path for matching
            transfer = await self.send_file(tarball_path, peer_url, original_path=folder_path)

            # Update filename to show it was a folder
            transfer.file_path = folder_path

            return transfer

        finally:
            # Clean up temp file
            if tarball_path.exists():
                tarball_path.unlink()
            if Path(temp_dir).exists():
                os.rmdir(temp_dir)

    def _create_tarball(self, folder_path: Path, tarball_path: Path) -> None:
        """Create a compressed tarball of a folder."""
        with tarfile.open(tarball_path, "w:gz") as tar:
            tar.add(folder_path, arcname=folder_path.name)

    async def send_file(
        self,
        file_path: Path,
        peer_url: str,
        resume_id: str | None = None,
        original_path: Path | None = None,
    ) -> OutgoingTransfer:
        """
        Send a file to a peer.
        
        Args:
            file_path: Path to the file to send
            peer_url: Base URL of the peer (e.g., http://192.168.1.42:8765)
            resume_id: Optional transfer ID to resume an interrupted transfer
            original_path: For folder transfers, the original folder path (used for matching)
        
        Returns:
            OutgoingTransfer object with final status
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if not file_path.is_file():
            raise ValueError(f"Not a file: {file_path}")

        peer_url = peer_url.rstrip("/")
        
        # Use original_path for the transfer key if provided (folder transfers)
        # This allows proper matching and cancellation
        key_path = original_path if original_path else file_path
        transfer_key = f"{peer_url}:{key_path}"
        
        # Create transfer object
        transfer = OutgoingTransfer(
            file_path=file_path,
            peer_url=peer_url,
            total_size=file_path.stat().st_size,
            original_path=original_path if original_path else file_path,
            _transfer_key=transfer_key,
        )

        self._active_transfers[transfer_key] = transfer
        self._cancel_flags[transfer_key] = False

        try:
            # Check for early cancellation before starting
            if self._cancel_flags.get(transfer_key, False):
                transfer.status = TransferStatus.CANCELLED
                if self.on_transfer_cancelled:
                    self.on_transfer_cancelled(transfer)
                return transfer
            
            # Calculate file hash
            transfer.status = TransferStatus.CONNECTING
            transfer.file_hash = await get_file_hash_async(file_path)

            if self.on_transfer_started:
                self.on_transfer_started(transfer)

            # Initialize transfer with peer
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as session:
                init_response = await self._init_transfer(session, transfer, resume_id)

                if not init_response:
                    return transfer

                transfer.transfer_id = init_response["transfer_id"]
                resume_offset = init_response.get("resume_offset", 0)

                if resume_offset > 0:
                    transfer.sent_bytes = resume_offset

                # Send file chunks
                await self._send_chunks(session, transfer)

                # Finalize transfer
                if transfer.status == TransferStatus.TRANSFERRING:
                    await self._complete_transfer(session, transfer)
                elif transfer.status == TransferStatus.CANCELLED:
                    if self.on_transfer_cancelled:
                        self.on_transfer_cancelled(transfer)

        except asyncio.CancelledError:
            transfer.status = TransferStatus.CANCELLED
            if self.on_transfer_cancelled:
                self.on_transfer_cancelled(transfer)
            raise
        except Exception as e:
            transfer.status = TransferStatus.FAILED
            transfer.error = str(e)
            if self.on_transfer_failed:
                self.on_transfer_failed(transfer, str(e))
        finally:
            if transfer_key in self._active_transfers:
                del self._active_transfers[transfer_key]
            if transfer_key in self._cancel_flags:
                del self._cancel_flags[transfer_key]

        return transfer

    async def cancel_transfer(self, file_path: Path, peer_url: str) -> bool:
        """Cancel an ongoing transfer.
        
        Returns True if a transfer was found and marked for cancellation.
        """
        peer_url = peer_url.rstrip("/")
        transfer_key = f"{peer_url}:{file_path}"
        if transfer_key in self._cancel_flags:
            self._cancel_flags[transfer_key] = True
            return True
        return False
    
    def cancel_transfer_by_key(self, transfer_key: str) -> bool:
        """Cancel an ongoing transfer by its internal key.
        
        Returns True if a transfer was found and marked for cancellation.
        """
        if transfer_key in self._cancel_flags:
            self._cancel_flags[transfer_key] = True
            return True
        return False

    async def _init_transfer(
        self,
        session: aiohttp.ClientSession,
        transfer: OutgoingTransfer,
        resume_id: str | None = None,
    ) -> dict | None:
        """Initialize transfer with the peer."""
        url = f"{transfer.peer_url}/transfer/init"
        data = {
            "filename": transfer.file_path.name,
            "size": transfer.total_size,
            "hash": transfer.file_hash,
        }
        if resume_id:
            data["resume_id"] = resume_id

        try:
            async with session.post(url, json=data) as response:
                if response.status != 200:
                    # Try to parse JSON error, fallback to text
                    try:
                        error_data = await response.json()
                        error_msg = error_data.get("error", "Failed to initialize transfer")
                    except Exception:
                        error_text = await response.text()
                        error_msg = f"Server error ({response.status}): {error_text[:200]}"
                    
                    transfer.status = TransferStatus.FAILED
                    transfer.error = error_msg
                    if self.on_transfer_failed:
                        self.on_transfer_failed(transfer, transfer.error)
                    return None

                return await response.json()

        except aiohttp.ClientError as e:
            transfer.status = TransferStatus.FAILED
            transfer.error = f"Connection error: {e}"
            if self.on_transfer_failed:
                self.on_transfer_failed(transfer, transfer.error)
            return None

    async def _send_chunks(
        self,
        session: aiohttp.ClientSession,
        transfer: OutgoingTransfer,
    ) -> None:
        """Send file chunks with retry support."""
        import aiofiles

        transfer.status = TransferStatus.TRANSFERRING
        url = f"{transfer.peer_url}/transfer/chunk"
        transfer_key = transfer._transfer_key

        retry_delay = INITIAL_RETRY_DELAY
        last_progress_time = asyncio.get_event_loop().time()
        last_sent_bytes = transfer.sent_bytes

        async with aiofiles.open(transfer.file_path, "rb") as f:
            await f.seek(transfer.sent_bytes)

            while transfer.sent_bytes < transfer.total_size:
                # Check for cancellation
                if self._cancel_flags.get(transfer_key, False):
                    transfer.status = TransferStatus.CANCELLED
                    return

                chunk = await f.read(self.chunk_size)
                if not chunk:
                    break

                chunk_start = transfer.sent_bytes
                chunk_end = transfer.sent_bytes + len(chunk) - 1

                headers = {
                    "X-Transfer-ID": transfer.transfer_id,
                    "Content-Range": f"bytes {chunk_start}-{chunk_end}/{transfer.total_size}",
                    "Content-Type": "application/octet-stream",
                }

                success = False
                for attempt in range(self.max_retries + 1):
                    try:
                        async with session.post(
                            url, data=chunk, headers=headers
                        ) as response:
                            if response.status == 200:
                                success = True
                                transfer.retry_count = 0
                                retry_delay = INITIAL_RETRY_DELAY

                                transfer.sent_bytes += len(chunk)

                                # Calculate speed
                                current_time = asyncio.get_event_loop().time()
                                time_diff = current_time - last_progress_time
                                if time_diff > 0.5:  # Update speed every 0.5 seconds
                                    bytes_diff = transfer.sent_bytes - last_sent_bytes
                                    transfer.speed = bytes_diff / time_diff
                                    last_progress_time = current_time
                                    last_sent_bytes = transfer.sent_bytes

                                if self.on_transfer_progress:
                                    self.on_transfer_progress(transfer)

                                break
                            else:
                                error_data = await response.json()
                                raise aiohttp.ClientError(
                                    error_data.get("error", f"Server error: {response.status}")
                                )

                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        transfer.retry_count = attempt + 1
                        transfer.status = TransferStatus.RETRYING

                        if attempt == self.max_retries:
                            transfer.status = TransferStatus.FAILED
                            transfer.error = f"Max retries exceeded: {e}"
                            if self.on_transfer_failed:
                                self.on_transfer_failed(transfer, transfer.error)
                            return

                        # Exponential backoff
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)

                        # Re-seek to retry same chunk
                        await f.seek(chunk_start)
                        chunk = await f.read(self.chunk_size)

                if not success:
                    return

                transfer.status = TransferStatus.TRANSFERRING

    async def _complete_transfer(
        self,
        session: aiohttp.ClientSession,
        transfer: OutgoingTransfer,
    ) -> None:
        """Finalize the transfer and verify hash."""
        transfer.status = TransferStatus.VERIFYING
        url = f"{transfer.peer_url}/transfer/complete"

        try:
            async with session.post(
                url, json={"transfer_id": transfer.transfer_id}
            ) as response:
                result = await response.json()

                if response.status == 200:
                    transfer.status = TransferStatus.COMPLETED
                    if self.on_transfer_completed:
                        self.on_transfer_completed(transfer)
                else:
                    transfer.status = TransferStatus.FAILED
                    transfer.error = result.get("error", "Transfer verification failed")
                    if self.on_transfer_failed:
                        self.on_transfer_failed(transfer, transfer.error)

        except aiohttp.ClientError as e:
            transfer.status = TransferStatus.FAILED
            transfer.error = f"Failed to complete transfer: {e}"
            if self.on_transfer_failed:
                self.on_transfer_failed(transfer, transfer.error)

