"""HTTP server for receiving file transfers."""

import asyncio
import hashlib
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import aiofiles
from aiohttp import web

from lantransfer.utils import (
    CHUNK_SIZE,
    DEFAULT_PORT,
    format_size,
    generate_transfer_id,
    get_downloads_dir,
)


@dataclass
class IncomingTransfer:
    """Represents an incoming file transfer."""

    transfer_id: str
    filename: str
    total_size: int
    expected_hash: str
    received_bytes: int = 0
    temp_path: Path | None = None
    final_path: Path | None = None
    hasher: hashlib.sha256 = field(default_factory=hashlib.sha256, repr=False)
    completed: bool = False
    error: str | None = None


@dataclass
class TransferServer:
    """HTTP server for receiving file transfers."""

    port: int = DEFAULT_PORT
    download_dir: Path = field(default_factory=get_downloads_dir)
    on_transfer_started: Callable[[IncomingTransfer], None] | None = None
    on_transfer_progress: Callable[[IncomingTransfer], None] | None = None
    on_transfer_completed: Callable[[IncomingTransfer], None] | None = None
    on_transfer_failed: Callable[[IncomingTransfer, str], None] | None = None

    _app: web.Application | None = field(default=None, init=False, repr=False)
    _runner: web.AppRunner | None = field(default=None, init=False, repr=False)
    _site: web.TCPSite | None = field(default=None, init=False, repr=False)
    _transfers: dict[str, IncomingTransfer] = field(default_factory=dict, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the HTTP server."""
        if self._running:
            return

        # Ensure download directory exists
        self.download_dir.mkdir(parents=True, exist_ok=True)

        # Create aiohttp app
        self._app = web.Application(client_max_size=CHUNK_SIZE * 2)
        self._setup_routes()

        # Start server
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await self._site.start()

        self._running = True

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if not self._running:
            return

        # Clean up incomplete transfers
        for transfer in self._transfers.values():
            if transfer.temp_path and transfer.temp_path.exists():
                try:
                    transfer.temp_path.unlink()
                except Exception:
                    pass

        if self._site:
            await self._site.stop()
            self._site = None

        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        self._app = None
        self._transfers.clear()
        self._running = False

    def _setup_routes(self) -> None:
        """Set up HTTP routes."""
        if not self._app:
            return

        self._app.router.add_get("/status", self._handle_status)
        self._app.router.add_post("/transfer/init", self._handle_init)
        self._app.router.add_post("/transfer/chunk", self._handle_chunk)
        self._app.router.add_post("/transfer/complete", self._handle_complete)
        self._app.router.add_get("/transfer/{transfer_id}/status", self._handle_transfer_status)
        self._app.router.add_delete("/transfer/{transfer_id}", self._handle_cancel)

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "ok",
            "active_transfers": len(self._transfers),
        })

    async def _handle_init(self, request: web.Request) -> web.Response:
        """Initialize a new transfer or resume an existing one."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        filename = data.get("filename")
        total_size = data.get("size")
        expected_hash = data.get("hash", "")
        resume_id = data.get("resume_id")

        if not filename or not total_size:
            return web.json_response(
                {"error": "Missing required fields: filename, size"},
                status=400,
            )

        # Check for existing transfer to resume
        if resume_id and resume_id in self._transfers:
            existing = self._transfers[resume_id]
            if existing.filename == filename and existing.total_size == total_size:
                return web.json_response({
                    "transfer_id": resume_id,
                    "resume_offset": existing.received_bytes,
                    "status": "resuming",
                })

        # Create new transfer
        transfer_id = generate_transfer_id()
        temp_path = self.download_dir / f".{transfer_id}_{filename}.part"
        final_path = self.download_dir / filename

        # Handle filename conflicts
        counter = 1
        while final_path.exists():
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            final_path = self.download_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        transfer = IncomingTransfer(
            transfer_id=transfer_id,
            filename=filename,
            total_size=total_size,
            expected_hash=expected_hash,
            temp_path=temp_path,
            final_path=final_path,
        )

        self._transfers[transfer_id] = transfer

        # Create empty temp file
        async with aiofiles.open(temp_path, "wb") as f:
            pass

        if self.on_transfer_started:
            self.on_transfer_started(transfer)

        return web.json_response({
            "transfer_id": transfer_id,
            "resume_offset": 0,
            "status": "ready",
        })

    async def _handle_chunk(self, request: web.Request) -> web.Response:
        """Receive a file chunk."""
        transfer_id = request.headers.get("X-Transfer-ID")
        if not transfer_id or transfer_id not in self._transfers:
            return web.json_response({"error": "Invalid transfer ID"}, status=400)

        transfer = self._transfers[transfer_id]

        # Parse range header
        range_header = request.headers.get("Content-Range", "")
        start_byte = 0
        if range_header.startswith("bytes "):
            try:
                range_part = range_header.split(" ")[1].split("/")[0]
                start_byte = int(range_part.split("-")[0])
            except (ValueError, IndexError):
                pass

        # Validate start position matches our progress
        if start_byte != transfer.received_bytes:
            return web.json_response({
                "error": "Invalid chunk position",
                "expected": transfer.received_bytes,
                "received": start_byte,
            }, status=400)

        if not transfer.temp_path:
            return web.json_response({"error": "Transfer not initialized"}, status=400)

        try:
            # Read and write chunk
            chunk = await request.read()
            
            async with aiofiles.open(transfer.temp_path, "ab") as f:
                await f.write(chunk)

            # Update progress
            transfer.received_bytes += len(chunk)
            transfer.hasher.update(chunk)

            if self.on_transfer_progress:
                self.on_transfer_progress(transfer)

            return web.json_response({
                "status": "ok",
                "received": transfer.received_bytes,
                "total": transfer.total_size,
                "progress": transfer.received_bytes / transfer.total_size,
            })

        except Exception as e:
            error_msg = str(e)
            transfer.error = error_msg
            if self.on_transfer_failed:
                self.on_transfer_failed(transfer, error_msg)
            return web.json_response({"error": error_msg}, status=500)

    async def _handle_complete(self, request: web.Request) -> web.Response:
        """Finalize a transfer and verify hash."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        transfer_id = data.get("transfer_id")
        if not transfer_id or transfer_id not in self._transfers:
            return web.json_response({"error": "Invalid transfer ID"}, status=400)

        transfer = self._transfers[transfer_id]

        # Verify we received all bytes
        if transfer.received_bytes != transfer.total_size:
            return web.json_response({
                "error": "Incomplete transfer",
                "received": transfer.received_bytes,
                "expected": transfer.total_size,
            }, status=400)

        # Verify hash
        computed_hash = transfer.hasher.hexdigest()
        if transfer.expected_hash and computed_hash != transfer.expected_hash:
            error_msg = "Hash mismatch - file may be corrupted"
            transfer.error = error_msg
            if self.on_transfer_failed:
                self.on_transfer_failed(transfer, error_msg)
            
            # Clean up temp file
            if transfer.temp_path and transfer.temp_path.exists():
                transfer.temp_path.unlink()
            
            return web.json_response({
                "error": error_msg,
                "expected_hash": transfer.expected_hash,
                "computed_hash": computed_hash,
            }, status=400)

        # Move temp file to final location
        if transfer.temp_path and transfer.final_path:
            try:
                transfer.temp_path.rename(transfer.final_path)
            except OSError:
                # Cross-device move, copy instead
                import shutil
                shutil.move(str(transfer.temp_path), str(transfer.final_path))

        # Auto-extract tar.gz files (folder transfers)
        extracted_path = None
        if transfer.final_path and transfer.final_path.suffix == ".gz" and ".tar" in transfer.final_path.name:
            try:
                extracted_path = await self._extract_tarball(transfer.final_path)
            except Exception:
                # Extraction failed, keep the tarball
                pass

        transfer.completed = True

        if self.on_transfer_completed:
            self.on_transfer_completed(transfer)

        # Clean up transfer record
        del self._transfers[transfer_id]

        return web.json_response({
            "status": "completed",
            "path": str(extracted_path or transfer.final_path),
            "size": format_size(transfer.total_size),
            "hash_verified": bool(transfer.expected_hash),
            "extracted": extracted_path is not None,
        })

    async def _handle_transfer_status(self, request: web.Request) -> web.Response:
        """Get status of a specific transfer."""
        transfer_id = request.match_info["transfer_id"]

        if transfer_id not in self._transfers:
            return web.json_response({"error": "Transfer not found"}, status=404)

        transfer = self._transfers[transfer_id]

        return web.json_response({
            "transfer_id": transfer_id,
            "filename": transfer.filename,
            "received_bytes": transfer.received_bytes,
            "total_size": transfer.total_size,
            "progress": transfer.received_bytes / transfer.total_size if transfer.total_size else 0,
            "completed": transfer.completed,
            "error": transfer.error,
        })

    async def _handle_cancel(self, request: web.Request) -> web.Response:
        """Cancel an ongoing transfer."""
        transfer_id = request.match_info["transfer_id"]

        if transfer_id not in self._transfers:
            return web.json_response({"error": "Transfer not found"}, status=404)

        transfer = self._transfers[transfer_id]

        # Clean up temp file
        if transfer.temp_path and transfer.temp_path.exists():
            try:
                transfer.temp_path.unlink()
            except Exception:
                pass

        del self._transfers[transfer_id]

        return web.json_response({"status": "cancelled"})

    async def _extract_tarball(self, tarball_path: Path) -> Path:
        """Extract a tarball and return the path to the extracted folder."""
        extract_dir = tarball_path.parent

        # Extract in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._do_extract,
            tarball_path,
            extract_dir,
        )

        # Find the extracted folder name (first entry in the tarball)
        with tarfile.open(tarball_path, "r:gz") as tar:
            first_member = tar.getmembers()[0]
            extracted_name = first_member.name.split("/")[0]

        # Remove the tarball after extraction
        tarball_path.unlink()

        return extract_dir / extracted_name

    def _do_extract(self, tarball_path: Path, extract_dir: Path) -> None:
        """Extract tarball synchronously."""
        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(path=extract_dir)

