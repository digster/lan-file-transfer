"""Transfer state persistence for resumable transfers."""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lantransfer.utils import get_data_dir


STATE_FILE = "transfers.json"
STATE_CLEANUP_AGE = 86400  # 24 hours in seconds


@dataclass
class TransferState:
    """Persisted state of a transfer for resumption."""

    transfer_id: str
    file_path: str
    filename: str
    peer_url: str
    peer_name: str
    total_size: int
    sent_bytes: int
    file_hash: str
    direction: str  # "outgoing" or "incoming"
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TransferState":
        """Create from dictionary."""
        return cls(**data)

    @property
    def is_expired(self) -> bool:
        """Check if this state is old and should be cleaned up."""
        return (time.time() - self.updated_at) > STATE_CLEANUP_AGE

    @property
    def can_resume(self) -> bool:
        """Check if this transfer can be resumed."""
        # Can resume if file exists and we haven't transferred everything
        if self.direction == "outgoing":
            file_path = Path(self.file_path)
            return file_path.exists() and self.sent_bytes < self.total_size
        return self.sent_bytes < self.total_size


class StateManager:
    """Manages persistent transfer state."""

    def __init__(self, data_dir: Path | None = None):
        """
        Initialize the state manager.
        
        Args:
            data_dir: Directory to store state file. Defaults to ~/.lantransfer/
        """
        self._data_dir = data_dir or get_data_dir()
        self._state_file = self._data_dir / STATE_FILE
        self._states: dict[str, TransferState] = {}
        self._load()

    def _load(self) -> None:
        """Load state from disk."""
        if not self._state_file.exists():
            return

        try:
            with open(self._state_file, "r") as f:
                data = json.load(f)

            for item in data.get("transfers", []):
                try:
                    state = TransferState.from_dict(item)
                    if not state.is_expired:
                        self._states[state.transfer_id] = state
                except (KeyError, TypeError):
                    continue

        except (json.JSONDecodeError, IOError):
            # Corrupted file, start fresh
            self._states = {}

    def _save(self) -> None:
        """Save state to disk."""
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Clean up expired states before saving
        self._cleanup_expired()

        data = {
            "version": 1,
            "transfers": [state.to_dict() for state in self._states.values()],
        }

        try:
            with open(self._state_file, "w") as f:
                json.dump(data, f, indent=2)
        except IOError:
            pass

    def _cleanup_expired(self) -> None:
        """Remove expired transfer states."""
        expired = [
            tid for tid, state in self._states.items()
            if state.is_expired
        ]
        for tid in expired:
            del self._states[tid]

    def save_outgoing_transfer(
        self,
        transfer_id: str,
        file_path: Path,
        peer_url: str,
        peer_name: str,
        total_size: int,
        sent_bytes: int,
        file_hash: str,
    ) -> None:
        """Save or update an outgoing transfer state."""
        now = time.time()

        if transfer_id in self._states:
            state = self._states[transfer_id]
            state.sent_bytes = sent_bytes
            state.updated_at = now
        else:
            state = TransferState(
                transfer_id=transfer_id,
                file_path=str(file_path),
                filename=file_path.name,
                peer_url=peer_url,
                peer_name=peer_name,
                total_size=total_size,
                sent_bytes=sent_bytes,
                file_hash=file_hash,
                direction="outgoing",
                created_at=now,
                updated_at=now,
            )
            self._states[transfer_id] = state

        self._save()

    def save_incoming_transfer(
        self,
        transfer_id: str,
        filename: str,
        total_size: int,
        received_bytes: int,
        expected_hash: str,
    ) -> None:
        """Save or update an incoming transfer state."""
        now = time.time()

        if transfer_id in self._states:
            state = self._states[transfer_id]
            state.sent_bytes = received_bytes
            state.updated_at = now
        else:
            state = TransferState(
                transfer_id=transfer_id,
                file_path="",  # Not applicable for incoming
                filename=filename,
                peer_url="",  # Not applicable for incoming
                peer_name="",
                total_size=total_size,
                sent_bytes=received_bytes,
                file_hash=expected_hash,
                direction="incoming",
                created_at=now,
                updated_at=now,
            )
            self._states[transfer_id] = state

        self._save()

    def get_transfer(self, transfer_id: str) -> TransferState | None:
        """Get a transfer state by ID."""
        return self._states.get(transfer_id)

    def get_resumable_transfers(self) -> list[TransferState]:
        """Get all transfers that can be resumed."""
        return [
            state for state in self._states.values()
            if state.can_resume
        ]

    def get_outgoing_by_file(self, file_path: Path, peer_url: str) -> TransferState | None:
        """Find an existing outgoing transfer for a file and peer."""
        file_path_str = str(file_path)
        for state in self._states.values():
            if (
                state.direction == "outgoing"
                and state.file_path == file_path_str
                and state.peer_url == peer_url
                and state.can_resume
            ):
                return state
        return None

    def complete_transfer(self, transfer_id: str) -> None:
        """Mark a transfer as complete and remove from state."""
        if transfer_id in self._states:
            del self._states[transfer_id]
            self._save()

    def fail_transfer(self, transfer_id: str) -> None:
        """
        Mark a transfer as failed but keep state for potential retry.
        
        The state will be automatically cleaned up after 24 hours.
        """
        if transfer_id in self._states:
            self._states[transfer_id].updated_at = time.time()
            self._save()

    def remove_transfer(self, transfer_id: str) -> None:
        """Remove a transfer state entirely."""
        if transfer_id in self._states:
            del self._states[transfer_id]
            self._save()

    def clear_all(self) -> None:
        """Clear all transfer states."""
        self._states.clear()
        self._save()

    @property
    def pending_transfers(self) -> list[TransferState]:
        """Get all pending (incomplete) transfers."""
        return [
            state for state in self._states.values()
            if state.sent_bytes < state.total_size
        ]

    @property
    def outgoing_transfers(self) -> list[TransferState]:
        """Get all outgoing transfer states."""
        return [
            state for state in self._states.values()
            if state.direction == "outgoing"
        ]

    @property
    def incoming_transfers(self) -> list[TransferState]:
        """Get all incoming transfer states."""
        return [
            state for state in self._states.values()
            if state.direction == "incoming"
        ]





