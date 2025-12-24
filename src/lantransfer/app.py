"""Main Flet application for LAN Transfer."""

import asyncio
from pathlib import Path

import flet as ft

from lantransfer import __app_name__, __version__
from lantransfer.discovery import DiscoveryService, Peer
from lantransfer.transfer import QueuedTransfer, TransferDirection, TransferManager
from lantransfer.utils import DEFAULT_PORT, format_size, get_downloads_dir, get_local_ip


# Color scheme - Modern dark theme with cyan accents
COLORS = {
    "background": "#0f0f14",
    "surface": "#1a1a24",
    "surface_variant": "#252532",
    "primary": "#00d4aa",
    "primary_variant": "#00a888",
    "secondary": "#7c3aed",
    "text": "#e8e8ed",
    "text_secondary": "#8888a0",
    "success": "#22c55e",
    "error": "#ef4444",
    "warning": "#f59e0b",
}


class DeviceCard(ft.Container):
    """Card representing a discovered peer device."""

    def __init__(self, peer: Peer, on_select: callable):
        self.peer = peer
        self._on_select = on_select
        self._selected = False

        # Device icon based on name hints
        icon = ft.Icons.COMPUTER
        if "mac" in peer.name.lower() or "book" in peer.name.lower():
            icon = ft.Icons.LAPTOP_MAC
        elif "linux" in peer.name.lower() or "ubuntu" in peer.name.lower():
            icon = ft.Icons.COMPUTER

        super().__init__(
            content=ft.Row(
                [
                    ft.Icon(icon, color=COLORS["primary"], size=28),
                    ft.Column(
                        [
                            ft.Text(
                                peer.name,
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color=COLORS["text"],
                            ),
                            ft.Text(
                                peer.address,
                                size=12,
                                color=COLORS["text_secondary"],
                            ),
                        ],
                        spacing=2,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                spacing=12,
            ),
            padding=ft.padding.all(12),
            border_radius=8,
            bgcolor=COLORS["surface_variant"],
            border=ft.border.all(1, COLORS["surface_variant"]),
            on_click=self._handle_click,
            ink=True,
        )

    def _handle_click(self, e):
        self._selected = not self._selected
        self.border = ft.border.all(
            2 if self._selected else 1,
            COLORS["primary"] if self._selected else COLORS["surface_variant"],
        )
        self.update()
        if self._on_select:
            self._on_select(self.peer if self._selected else None)


class TransferCard(ft.Container):
    """Card showing a transfer in the queue."""

    def __init__(self, transfer: QueuedTransfer, on_cancel: callable):
        self.transfer = transfer
        self._on_cancel = on_cancel

        # Direction icon
        if transfer.direction == TransferDirection.OUTGOING:
            direction_icon = ft.Icons.UPLOAD_FILE
            direction_text = f"→ {transfer.peer_name or transfer.peer_address}"
        else:
            direction_icon = ft.Icons.FILE_DOWNLOAD
            direction_text = "← Receiving"

        # Status color and icon
        status_color = COLORS["text_secondary"]
        status_icon = ft.Icons.HOURGLASS_EMPTY
        if transfer.status == "transferring":
            status_color = COLORS["primary"]
            status_icon = ft.Icons.SYNC
        elif transfer.status == "completed":
            status_color = COLORS["success"]
            status_icon = ft.Icons.CHECK_CIRCLE
        elif transfer.status == "failed":
            status_color = COLORS["error"]
            status_icon = ft.Icons.ERROR
        elif transfer.status == "retrying":
            status_color = COLORS["warning"]
            status_icon = ft.Icons.REFRESH
        elif transfer.status == "cancelled":
            status_color = COLORS["warning"]
            status_icon = ft.Icons.CANCEL

        # Progress bar
        progress_bar = ft.ProgressBar(
            value=transfer.progress / 100 if transfer.is_active else (1 if transfer.status == "completed" else 0),
            color=status_color,
            bgcolor=COLORS["surface"],
            height=4,
            border_radius=2,
        )

        # Speed and ETA
        speed_eta_text = ""
        if transfer.is_active and transfer.speed > 0:
            speed_eta_text = f"{transfer.speed_text}"
            if transfer.eta_text:
                speed_eta_text += f" • {transfer.eta_text} remaining"

        # Cancel button (only for active transfers)
        cancel_btn = None
        if transfer.is_active or transfer.status == "pending":
            cancel_btn = ft.IconButton(
                icon=ft.Icons.CLOSE,
                icon_color=COLORS["text_secondary"],
                icon_size=18,
                tooltip="Cancel",
                on_click=lambda e: self._on_cancel(transfer.id) if self._on_cancel else None,
            )

        super().__init__(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(direction_icon, color=COLORS["primary"], size=20),
                            ft.Column(
                                [
                                    ft.Text(
                                        transfer.filename,
                                        size=13,
                                        weight=ft.FontWeight.W_500,
                                        color=COLORS["text"],
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Text(
                                        direction_text,
                                        size=11,
                                        color=COLORS["text_secondary"],
                                    ),
                                ],
                                spacing=1,
                                expand=True,
                            ),
                            ft.Row(
                                [
                                    ft.Icon(status_icon, color=status_color, size=16),
                                    ft.Text(
                                        transfer.status.title(),
                                        size=11,
                                        color=status_color,
                                    ),
                                ],
                                spacing=4,
                            ),
                            cancel_btn if cancel_btn else ft.Container(),
                        ],
                        spacing=10,
                    ),
                    progress_bar,
                    ft.Row(
                        [
                            ft.Text(
                                transfer.progress_text,
                                size=11,
                                color=COLORS["text_secondary"],
                            ),
                            ft.Text(
                                speed_eta_text,
                                size=11,
                                color=COLORS["text_secondary"],
                            ) if speed_eta_text else ft.Container(),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    # Show error if failed
                    ft.Text(
                        transfer.error or "",
                        size=11,
                        color=COLORS["error"],
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ) if transfer.error else ft.Container(),
                ],
                spacing=8,
            ),
            padding=ft.padding.all(12),
            border_radius=8,
            bgcolor=COLORS["surface_variant"],
        )


class LANTransferApp:
    """Main application class."""

    def __init__(self, page: ft.Page):
        self.page = page
        self._selected_peer: Peer | None = None
        self._discovery: DiscoveryService | None = None
        self._transfer_manager: TransferManager | None = None

        # UI components
        self._devices_list: ft.Column | None = None
        self._transfers_list: ft.Column | None = None
        self._status_text: ft.Text | None = None
        self._drop_zone: ft.Container | None = None

    async def initialize(self):
        """Initialize the application."""
        # Setup page
        self.page.title = f"{__app_name__} v{__version__}"
        self.page.bgcolor = COLORS["background"]
        self.page.padding = 0
        self.page.window.width = 900
        self.page.window.height = 650
        self.page.window.min_width = 700
        self.page.window.min_height = 500

        # Initialize services
        self._discovery = DiscoveryService(
            port=DEFAULT_PORT,
            on_peer_added=self._on_peer_added,
            on_peer_removed=self._on_peer_removed,
        )

        self._transfer_manager = TransferManager()
        self._transfer_manager.on_queue_updated = self._on_queue_updated
        self._transfer_manager.on_transfer_completed = self._on_transfer_done
        self._transfer_manager.on_transfer_failed = self._on_transfer_done

        # Build UI
        self._build_ui()

        # Start services
        await self._discovery.start()
        await self._transfer_manager.start()

        # Update status
        local_ip = get_local_ip()
        self._status_text.value = f"Ready • {local_ip}:{DEFAULT_PORT}"
        self.page.update()

    def _build_ui(self):
        """Build the main UI."""
        # Header
        header = ft.Container(
            content=ft.Row(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.SWAP_HORIZ, color=COLORS["primary"], size=28),
                            ft.Text(
                                __app_name__,
                                size=20,
                                weight=ft.FontWeight.BOLD,
                                color=COLORS["text"],
                            ),
                        ],
                        spacing=10,
                    ),
                    ft.Row(
                        [
                            ft.IconButton(
                                icon=ft.Icons.FOLDER_OPEN,
                                icon_color=COLORS["text_secondary"],
                                tooltip="Open downloads folder",
                                on_click=self._open_downloads,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.SETTINGS,
                                icon_color=COLORS["text_secondary"],
                                tooltip="Settings",
                            ),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            padding=ft.padding.symmetric(horizontal=20, vertical=12),
            bgcolor=COLORS["surface"],
        )

        # Devices panel
        self._devices_list = ft.Column(
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

        devices_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                "Nearby Devices",
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color=COLORS["text"],
                            ),
                            ft.IconButton(
                                icon=ft.Icons.REFRESH,
                                icon_color=COLORS["text_secondary"],
                                icon_size=18,
                                tooltip="Refresh",
                                on_click=self._refresh_devices,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Container(
                        content=self._devices_list,
                        expand=True,
                    ),
                ],
                spacing=10,
            ),
            width=260,
            padding=ft.padding.all(16),
            bgcolor=COLORS["surface"],
            border_radius=12,
        )

        # Transfers panel
        self._transfers_list = ft.Column(
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

        # Empty state for transfers
        self._empty_transfers = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.CLOUD_QUEUE, color=COLORS["text_secondary"], size=48),
                    ft.Text(
                        "No active transfers",
                        size=14,
                        color=COLORS["text_secondary"],
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            alignment=ft.alignment.center,
            expand=True,
        )

        transfers_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                "Transfer Queue",
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color=COLORS["text"],
                            ),
                            ft.TextButton(
                                "Clear completed",
                                style=ft.ButtonStyle(color=COLORS["text_secondary"]),
                                on_click=self._clear_completed,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Container(
                        content=ft.Stack(
                            [
                                self._empty_transfers,
                                self._transfers_list,
                            ],
                        ),
                        expand=True,
                    ),
                ],
                spacing=10,
            ),
            expand=True,
            padding=ft.padding.all(16),
            bgcolor=COLORS["surface"],
            border_radius=12,
        )

        # Drop zone
        self._drop_zone = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.CLOUD_UPLOAD, color=COLORS["text_secondary"], size=36),
                    ft.Text(
                        "Drop files here or click to browse",
                        size=14,
                        color=COLORS["text_secondary"],
                    ),
                    ft.Text(
                        "Select a device first",
                        size=12,
                        color=COLORS["text_secondary"],
                        italic=True,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=8,
            ),
            height=100,
            border=ft.border.all(2, COLORS["surface_variant"]),
            border_radius=12,
            bgcolor=COLORS["surface"],
            alignment=ft.alignment.center,
            on_click=self._pick_files,
        )

        # Status bar
        self._status_text = ft.Text(
            "Starting...",
            size=12,
            color=COLORS["text_secondary"],
        )

        status_bar = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.CIRCLE, color=COLORS["success"], size=10),
                    self._status_text,
                ],
                spacing=8,
            ),
            padding=ft.padding.symmetric(horizontal=20, vertical=10),
            bgcolor=COLORS["surface"],
        )

        # Main layout
        main_content = ft.Container(
            content=ft.Row(
                [
                    devices_panel,
                    ft.Column(
                        [
                            transfers_panel,
                            self._drop_zone,
                        ],
                        spacing=16,
                        expand=True,
                    ),
                ],
                spacing=16,
                expand=True,
            ),
            padding=ft.padding.all(16),
            expand=True,
        )

        # File picker
        self._file_picker = ft.FilePicker(on_result=self._on_files_picked)
        self.page.overlay.append(self._file_picker)

        # Build page
        self.page.add(
            ft.Column(
                [
                    header,
                    main_content,
                    status_bar,
                ],
                spacing=0,
                expand=True,
            )
        )

        # Setup drag and drop from OS
        self.page.on_file_drop = self._on_file_drop
        self.page.on_file_drag_enter = self._on_file_drag_enter
        self.page.on_file_drag_leave = self._on_file_drag_leave
        self.page.on_keyboard_event = self._handle_keyboard

    def _on_peer_added(self, peer: Peer):
        """Called when a new peer is discovered."""
        if self._devices_list:
            card = DeviceCard(peer, self._on_device_selected)
            self._devices_list.controls.append(card)
            self.page.update()

    def _on_peer_removed(self, peer: Peer):
        """Called when a peer goes offline."""
        if self._devices_list:
            self._devices_list.controls = [
                c for c in self._devices_list.controls
                if not (isinstance(c, DeviceCard) and c.peer == peer)
            ]
            if self._selected_peer == peer:
                self._selected_peer = None
                self._update_drop_zone()
            self.page.update()

    def _on_device_selected(self, peer: Peer | None):
        """Called when a device is selected/deselected."""
        self._selected_peer = peer
        self._update_drop_zone()
        self.page.update()

    def _update_drop_zone(self):
        """Update the drop zone based on selection state."""
        if self._selected_peer:
            self._drop_zone.border = ft.border.all(2, COLORS["primary"])
            self._drop_zone.content.controls[2].value = f"Send to {self._selected_peer.name}"
        else:
            self._drop_zone.border = ft.border.all(2, COLORS["surface_variant"])
            self._drop_zone.content.controls[2].value = "Select a device first"

    def _on_queue_updated(self):
        """Called when the transfer queue changes."""
        self._refresh_transfers()

    def _on_transfer_done(self, transfer: QueuedTransfer):
        """Called when a transfer completes or fails."""
        self._refresh_transfers()

    def _refresh_transfers(self):
        """Refresh the transfers list."""
        if not self._transfers_list or not self._transfer_manager:
            return

        queue = self._transfer_manager.queue

        # Show/hide empty state
        self._empty_transfers.visible = len(queue) == 0
        self._transfers_list.visible = len(queue) > 0

        # Update transfers
        self._transfers_list.controls = [
            TransferCard(t, self._cancel_transfer)
            for t in queue
        ]

        try:
            self.page.update()
        except Exception:
            pass

    def _refresh_devices(self, e=None):
        """Refresh the devices list."""
        if self._devices_list and self._discovery:
            self._devices_list.controls = [
                DeviceCard(peer, self._on_device_selected)
                for peer in self._discovery.peers
            ]
            self.page.update()

    def _pick_files(self, e=None):
        """Open file picker dialog."""
        if not self._selected_peer:
            self.page.show_snack_bar(
                ft.SnackBar(
                    content=ft.Text("Please select a device first"),
                    bgcolor=COLORS["warning"],
                )
            )
            return

        # Show dialog to choose between files or folder
        self._show_picker_dialog()

    def _show_picker_dialog(self):
        """Show dialog to choose between file or folder picker."""
        self._picker_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("What would you like to send?"),
            content=ft.Text("Choose to send individual files or an entire folder."),
            actions=[
                ft.TextButton("Files", on_click=self._pick_files_action),
                ft.TextButton("Folder", on_click=self._pick_folder_action),
                ft.TextButton("Cancel", on_click=self._close_picker_dialog),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        self.page.open(self._picker_dialog)

    def _close_picker_dialog(self, e=None):
        """Close the picker dialog."""
        if hasattr(self, '_picker_dialog') and self._picker_dialog:
            self.page.close(self._picker_dialog)

    async def _pick_files_action_async(self):
        """Handle picking files asynchronously."""
        self._close_picker_dialog()
        await asyncio.sleep(0.1)  # Small delay to let dialog close
        self._file_picker.pick_files(
            allow_multiple=True,
            dialog_title="Select files to send",
        )

    def _pick_files_action(self, e):
        """Handle picking files."""
        self.page.run_task(self._pick_files_action_async)

    async def _pick_folder_action_async(self):
        """Handle picking a folder asynchronously."""
        self._close_picker_dialog()
        await asyncio.sleep(0.1)  # Small delay to let dialog close
        self._file_picker.get_directory_path(
            dialog_title="Select folder to send",
        )

    def _pick_folder_action(self, e):
        """Handle picking a folder."""
        self.page.run_task(self._pick_folder_action_async)

    def _on_files_picked(self, e: ft.FilePickerResultEvent):
        """Called when files or folder are picked."""
        if not self._selected_peer or not self._transfer_manager:
            return

        # Handle folder selection
        if e.path:
            folder_path = Path(e.path)
            if folder_path.is_dir():
                self._transfer_manager.queue_send(folder_path, self._selected_peer)
            return

        # Handle file selection
        if not e.files:
            return

        for file in e.files:
            file_path = Path(file.path)
            self._transfer_manager.queue_send(file_path, self._selected_peer)

    def _cancel_transfer(self, transfer_id: str):
        """Cancel a transfer."""
        if self._transfer_manager:
            self._transfer_manager.cancel_transfer(transfer_id)

    def _clear_completed(self, e=None):
        """Clear completed transfers from the queue."""
        if self._transfer_manager:
            self._transfer_manager.clear_completed()

    def _open_downloads(self, e=None):
        """Open the downloads folder."""
        import subprocess
        import sys

        downloads = get_downloads_dir()
        if sys.platform == "darwin":
            subprocess.run(["open", str(downloads)])
        elif sys.platform == "linux":
            subprocess.run(["xdg-open", str(downloads)])

    def _on_file_drag_enter(self, e: ft.ControlEvent):
        """Handle files being dragged over the window."""
        # Highlight drop zone
        self._drop_zone.bgcolor = COLORS["surface_variant"]
        self._drop_zone.border = ft.border.all(
            2,
            COLORS["primary"] if self._selected_peer else COLORS["warning"],
        )
        self._drop_zone.content.controls[0].color = COLORS["primary"]
        self._drop_zone.content.controls[1].value = "Drop files to send"
        self.page.update()

    def _on_file_drag_leave(self, e: ft.ControlEvent):
        """Handle files leaving the drag area."""
        # Reset drop zone appearance
        self._drop_zone.bgcolor = COLORS["surface"]
        self._drop_zone.content.controls[0].color = COLORS["text_secondary"]
        self._drop_zone.content.controls[1].value = "Drop files here or click to browse"
        self._update_drop_zone()
        self.page.update()

    def _on_file_drop(self, e: ft.ControlEvent):
        """Handle files dropped from the OS."""
        # Reset drop zone appearance
        self._drop_zone.bgcolor = COLORS["surface"]
        self._drop_zone.content.controls[0].color = COLORS["text_secondary"]
        self._drop_zone.content.controls[1].value = "Drop files here or click to browse"
        self._update_drop_zone()

        if not self._selected_peer:
            self.page.show_snack_bar(
                ft.SnackBar(
                    content=ft.Text("Please select a device first"),
                    bgcolor=COLORS["warning"],
                )
            )
            self.page.update()
            return

        if not self._transfer_manager:
            return

        # Queue each dropped file/folder for transfer
        for file_path_str in e.files:
            file_path = Path(file_path_str)
            if file_path.exists():
                self._transfer_manager.queue_send(file_path, self._selected_peer)

        self.page.update()

    def _handle_keyboard(self, e: ft.KeyboardEvent):
        """Handle keyboard events."""
        pass

    async def cleanup(self):
        """Clean up resources."""
        if self._transfer_manager:
            await self._transfer_manager.stop()
        if self._discovery:
            await self._discovery.stop()


async def main_async(page: ft.Page):
    """Async main function for Flet."""
    app = LANTransferApp(page)
    await app.initialize()

    # Handle page close
    async def on_close(e):
        await app.cleanup()

    page.on_close = on_close


def run_app() -> int:
    """Run the application."""
    ft.app(target=main_async)
    return 0


if __name__ == "__main__":
    run_app()

