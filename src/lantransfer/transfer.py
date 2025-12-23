"""Transfer manager for coordinating file transfers."""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable
from uuid import uuid4

from lantransfer.client import OutgoingTransfer, TransferClient, TransferStatus
from lantransfer.discovery import Peer
from lantransfer.server import IncomingTransfer, TransferServer
from lantransfer.utils import format_size, format_speed, format_time


class TransferDirection(Enum):
    """Direction of the transfer."""
    OUTGOING = "outgoing"
    INCOMING = "incoming"


@dataclass
class QueuedTransfer:
    """A transfer in the queue."""
    
    id: str
    direction: TransferDirection
    filename: str
    total_size: int
    transferred_bytes: int = 0
    status: str = "pending"
    peer_name: str = ""
    peer_address: str = ""
    error: str | None = None
    speed: float = 0.0
    
    # Internal references
    _outgoing: OutgoingTransfer | None = field(default=None, repr=False)
    _incoming: IncomingTransfer | None = field(default=None, repr=False)
    _file_path: Path | None = field(default=None, repr=False)

    @property
    def progress(self) -> float:
        """Get progress as percentage (0-100)."""
        if self.total_size == 0:
            return 0
        return (self.transferred_bytes / self.total_size) * 100

    @property
    def progress_text(self) -> str:
        """Get human-readable progress text."""
        sent = format_size(self.transferred_bytes)
        total = format_size(self.total_size)
        return f"{sent} / {total} ({self.progress:.1f}%)"

    @property
    def speed_text(self) -> str:
        """Get human-readable speed text."""
        if self.speed <= 0:
            return ""
        return format_speed(self.speed)

    @property
    def eta_text(self) -> str:
        """Get estimated time remaining."""
        if self.speed <= 0:
            return ""
        remaining_bytes = self.total_size - self.transferred_bytes
        if remaining_bytes <= 0:
            return ""
        eta_seconds = remaining_bytes / self.speed
        return format_time(eta_seconds)

    @property
    def is_active(self) -> bool:
        """Check if transfer is actively running."""
        return self.status in ("connecting", "transferring", "retrying", "verifying")


@dataclass
class TransferManager:
    """Manages all file transfers."""

    server: TransferServer = field(default_factory=TransferServer)
    client: TransferClient = field(default_factory=TransferClient)

    on_queue_updated: Callable[[], None] | None = None
    on_transfer_completed: Callable[[QueuedTransfer], None] | None = None
    on_transfer_failed: Callable[[QueuedTransfer], None] | None = None

    _queue: dict[str, QueuedTransfer] = field(default_factory=dict, init=False, repr=False)
    _pending_sends: asyncio.Queue = field(default_factory=asyncio.Queue, init=False, repr=False)
    _send_task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)

    @property
    def queue(self) -> list[QueuedTransfer]:
        """Get all transfers in the queue."""
        return list(self._queue.values())

    @property
    def active_transfers(self) -> list[QueuedTransfer]:
        """Get currently active transfers."""
        return [t for t in self._queue.values() if t.is_active]

    @property
    def completed_transfers(self) -> list[QueuedTransfer]:
        """Get completed transfers."""
        return [t for t in self._queue.values() if t.status == "completed"]

    async def start(self) -> None:
        """Start the transfer manager."""
        if self._running:
            return

        # Set up callbacks
        self._setup_callbacks()

        # Start server
        await self.server.start()

        # Start send worker
        self._send_task = asyncio.create_task(self._send_worker())

        self._running = True

    async def stop(self) -> None:
        """Stop the transfer manager."""
        if not self._running:
            return

        # Stop send worker
        if self._send_task:
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
            self._send_task = None

        # Stop server
        await self.server.stop()

        self._running = False

    def _setup_callbacks(self) -> None:
        """Set up callbacks for server and client."""
        # Server callbacks
        self.server.on_transfer_started = self._on_incoming_started
        self.server.on_transfer_progress = self._on_incoming_progress
        self.server.on_transfer_completed = self._on_incoming_completed
        self.server.on_transfer_failed = self._on_incoming_failed

        # Client callbacks
        self.client.on_transfer_started = self._on_outgoing_started
        self.client.on_transfer_progress = self._on_outgoing_progress
        self.client.on_transfer_completed = self._on_outgoing_completed
        self.client.on_transfer_failed = self._on_outgoing_failed

    def queue_send(self, path: Path, peer: Peer) -> str:
        """Queue a file or folder to be sent to a peer."""
        from lantransfer.utils import get_folder_size

        queue_id = str(uuid4())[:8]

        # Calculate size (handle both files and folders)
        if path.is_dir():
            total_size = get_folder_size(path)
            filename = f"{path.name}/"  # Indicate it's a folder
        else:
            total_size = path.stat().st_size
            filename = path.name

        transfer = QueuedTransfer(
            id=queue_id,
            direction=TransferDirection.OUTGOING,
            filename=filename,
            total_size=total_size,
            status="pending",
            peer_name=peer.name,
            peer_address=peer.address,
            _file_path=path,
        )

        self._queue[queue_id] = transfer
        self._pending_sends.put_nowait((queue_id, path, peer))

        self._notify_queue_updated()
        return queue_id

    def cancel_transfer(self, queue_id: str) -> bool:
        """Cancel a queued or active transfer."""
        if queue_id not in self._queue:
            return False

        transfer = self._queue[queue_id]
        
        if transfer.status == "completed":
            return False

        transfer.status = "cancelled"
        
        # Cancel outgoing transfer if active
        if transfer._outgoing and transfer._file_path:
            asyncio.create_task(
                self.client.cancel_transfer(transfer._file_path, transfer._outgoing.peer_url)
            )

        self._notify_queue_updated()
        return True

    def clear_completed(self) -> None:
        """Remove completed and failed transfers from the queue."""
        to_remove = [
            qid for qid, t in self._queue.items()
            if t.status in ("completed", "failed", "cancelled")
        ]
        for qid in to_remove:
            del self._queue[qid]
        
        self._notify_queue_updated()

    async def _send_worker(self) -> None:
        """Worker that processes the send queue."""
        while True:
            try:
                queue_id, path, peer = await self._pending_sends.get()

                if queue_id not in self._queue:
                    continue

                transfer = self._queue[queue_id]
                
                if transfer.status == "cancelled":
                    continue

                # Send the file or folder
                await self.client.send_path(path, peer.url)

            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log error but keep worker running
                print(f"Send worker error: {e}")

    def _notify_queue_updated(self) -> None:
        """Notify listeners that the queue has changed."""
        if self.on_queue_updated:
            self.on_queue_updated()

    # Incoming transfer callbacks
    def _on_incoming_started(self, transfer: IncomingTransfer) -> None:
        """Called when an incoming transfer starts."""
        queue_id = transfer.transfer_id

        queued = QueuedTransfer(
            id=queue_id,
            direction=TransferDirection.INCOMING,
            filename=transfer.filename,
            total_size=transfer.total_size,
            status="transferring",
            _incoming=transfer,
        )

        self._queue[queue_id] = queued
        self._notify_queue_updated()

    def _on_incoming_progress(self, transfer: IncomingTransfer) -> None:
        """Called when incoming transfer progresses."""
        queue_id = transfer.transfer_id

        if queue_id in self._queue:
            queued = self._queue[queue_id]
            queued.transferred_bytes = transfer.received_bytes
            self._notify_queue_updated()

    def _on_incoming_completed(self, transfer: IncomingTransfer) -> None:
        """Called when incoming transfer completes."""
        queue_id = transfer.transfer_id

        if queue_id in self._queue:
            queued = self._queue[queue_id]
            queued.status = "completed"
            queued.transferred_bytes = transfer.total_size
            self._notify_queue_updated()

            if self.on_transfer_completed:
                self.on_transfer_completed(queued)

    def _on_incoming_failed(self, transfer: IncomingTransfer, error: str) -> None:
        """Called when incoming transfer fails."""
        queue_id = transfer.transfer_id

        if queue_id in self._queue:
            queued = self._queue[queue_id]
            queued.status = "failed"
            queued.error = error
            self._notify_queue_updated()

            if self.on_transfer_failed:
                self.on_transfer_failed(queued)

    # Outgoing transfer callbacks
    def _on_outgoing_started(self, transfer: OutgoingTransfer) -> None:
        """Called when an outgoing transfer starts."""
        # Find the queued transfer by file path
        for queued in self._queue.values():
            if queued._file_path == transfer.file_path and queued.direction == TransferDirection.OUTGOING:
                queued._outgoing = transfer
                queued.status = "connecting"
                self._notify_queue_updated()
                break

    def _on_outgoing_progress(self, transfer: OutgoingTransfer) -> None:
        """Called when outgoing transfer progresses."""
        for queued in self._queue.values():
            if queued._outgoing == transfer:
                queued.transferred_bytes = transfer.sent_bytes
                queued.status = transfer.status.value
                queued.speed = transfer.speed
                self._notify_queue_updated()
                break

    def _on_outgoing_completed(self, transfer: OutgoingTransfer) -> None:
        """Called when outgoing transfer completes."""
        for queued in self._queue.values():
            if queued._outgoing == transfer:
                queued.status = "completed"
                queued.transferred_bytes = transfer.total_size
                self._notify_queue_updated()

                if self.on_transfer_completed:
                    self.on_transfer_completed(queued)
                break

    def _on_outgoing_failed(self, transfer: OutgoingTransfer, error: str) -> None:
        """Called when outgoing transfer fails."""
        for queued in self._queue.values():
            if queued._outgoing == transfer:
                queued.status = "failed"
                queued.error = error
                self._notify_queue_updated()

                if self.on_transfer_failed:
                    self.on_transfer_failed(queued)
                break

