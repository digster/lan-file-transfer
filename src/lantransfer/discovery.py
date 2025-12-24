"""mDNS service discovery for automatic peer detection."""

import asyncio
import socket
import threading
from dataclasses import dataclass, field
from typing import Callable

from zeroconf import IPVersion, ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from lantransfer.utils import DEFAULT_PORT, SERVICE_TYPE, get_device_name, get_local_ip


@dataclass
class Peer:
    """Represents a discovered peer on the network."""

    name: str
    address: str
    port: int
    device_id: str = ""

    @property
    def url(self) -> str:
        """Get the base URL for this peer."""
        return f"http://{self.address}:{self.port}"

    def __hash__(self) -> int:
        return hash((self.address, self.port))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Peer):
            return False
        return self.address == other.address and self.port == other.port


@dataclass
class DiscoveryService:
    """Service for discovering and advertising peers on the local network."""

    port: int = DEFAULT_PORT
    device_name: str = field(default_factory=get_device_name)
    on_peer_added: Callable[[Peer], None] | None = None
    on_peer_removed: Callable[[Peer], None] | None = None

    _zeroconf: AsyncZeroconf | None = field(default=None, init=False, repr=False)
    _browser: ServiceBrowser | None = field(default=None, init=False, repr=False)
    _service_info: AsyncServiceInfo | None = field(default=None, init=False, repr=False)
    _peers: dict[str, Peer] = field(default_factory=dict, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)
    _local_ip: str = field(default="", init=False, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)

    @property
    def peers(self) -> list[Peer]:
        """Get list of discovered peers."""
        return list(self._peers.values())

    async def start(self) -> None:
        """Start the discovery service."""
        if self._running:
            return

        self._local_ip = get_local_ip()
        self._loop = asyncio.get_running_loop()
        self._zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)

        # Register our service
        await self._register_service()

        # Start browsing for other services
        self._start_browser()

        self._running = True

    async def stop(self) -> None:
        """Stop the discovery service."""
        if not self._running:
            return

        # Unregister our service
        if self._service_info and self._zeroconf:
            await self._zeroconf.async_unregister_service(self._service_info)

        # Stop browser
        if self._browser:
            self._browser.cancel()
            self._browser = None

        # Close zeroconf
        if self._zeroconf:
            await self._zeroconf.async_close()
            self._zeroconf = None

        self._peers.clear()
        self._running = False

    async def _register_service(self) -> None:
        """Register our service on the network."""
        if not self._zeroconf:
            return

        # Create unique service name
        service_name = f"{self.device_name}.{SERVICE_TYPE}"

        self._service_info = AsyncServiceInfo(
            SERVICE_TYPE,
            service_name,
            addresses=[socket.inet_aton(self._local_ip)],
            port=self.port,
            properties={
                "version": "1.0",
                "device": self.device_name,
            },
            server=f"{self.device_name}.local.",
        )

        await self._zeroconf.async_register_service(self._service_info)

    def _start_browser(self) -> None:
        """Start browsing for other services."""
        if not self._zeroconf:
            return

        listener = _PeerListener(self)
        self._browser = ServiceBrowser(
            self._zeroconf.zeroconf, SERVICE_TYPE, listener
        )

    def _add_peer(self, name: str, info: ServiceInfo) -> None:
        """Add a discovered peer."""
        # Get the first IPv4 address
        addresses = info.parsed_addresses(IPVersion.V4Only)
        if not addresses:
            return

        address = addresses[0]

        # Skip ourselves
        if address == self._local_ip and info.port == self.port:
            return

        peer = Peer(
            name=info.properties.get(b"device", name.encode()).decode(),
            address=address,
            port=info.port,
            device_id=name,
        )

        if name not in self._peers:
            self._peers[name] = peer
            if self.on_peer_added and self._loop:
                # Thread-safe callback to main event loop
                self._loop.call_soon_threadsafe(self.on_peer_added, peer)

    def _remove_peer(self, name: str) -> None:
        """Remove a peer that went offline."""
        if name in self._peers:
            peer = self._peers.pop(name)
            if self.on_peer_removed and self._loop:
                # Thread-safe callback to main event loop
                self._loop.call_soon_threadsafe(self.on_peer_removed, peer)


class _PeerListener(ServiceListener):
    """Zeroconf service listener for peer discovery."""

    def __init__(self, discovery: DiscoveryService) -> None:
        self._discovery = discovery

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a service is added."""
        info = zc.get_service_info(type_, name)
        if info:
            self._discovery._add_peer(name, info)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a service is removed."""
        self._discovery._remove_peer(name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a service is updated."""
        # Remove and re-add to update the info
        self._discovery._remove_peer(name)
        info = zc.get_service_info(type_, name)
        if info:
            self._discovery._add_peer(name, info)


