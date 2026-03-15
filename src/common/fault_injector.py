"""
Fault Injection Layer for RUDP testing.

Wraps a UDP socket to simulate network faults:
  - Packet loss (drop)
  - Delay (configurable ms)
  - Duplication
  - Reordering
  - Corruption (payload / header)
  - Truncation

Each injected packet carries a Wireshark-visible marker: TEST_CASE:<name>
so traffic can be filtered with:  frame contains "TEST_CASE"
"""

import socket
import threading
import time
import random
import copy

from common import rudp_lib


# ---------------------------------------------------------------------------
# Marker helper – embeds a tag visible to Wireshark inside RUDP payload
# ---------------------------------------------------------------------------
def marker_payload(test_name: str, extra: bytes = b"") -> bytes:
    """Return payload bytes that contain the Wireshark-visible marker."""
    tag = f"TEST_CASE:{test_name}".encode("utf-8")
    return tag + b"|" + extra if extra else tag


# ---------------------------------------------------------------------------
# FaultConfig – describes *what* to inject
# ---------------------------------------------------------------------------
class FaultConfig:
    """Configuration object describing which faults to inject."""

    def __init__(
        self,
        drop_indices: list[int] | None = None,
        delay_indices: dict[int, float] | None = None,  # idx -> seconds
        duplicate_indices: list[int] | None = None,
        reorder_pairs: list[tuple[int, int]] | None = None,
        corrupt_indices: list[int] | None = None,
        truncate_indices: list[int] | None = None,
        loss_percent: int = 0,
        global_delay_sec: float = 0.0,
    ):
        self.drop_indices = set(drop_indices or [])
        self.delay_indices = delay_indices or {}
        self.duplicate_indices = set(duplicate_indices or [])
        self.reorder_pairs = reorder_pairs or []
        self.corrupt_indices = set(corrupt_indices or [])
        self.truncate_indices = set(truncate_indices or [])
        self.loss_percent = loss_percent
        self.global_delay_sec = global_delay_sec


# ---------------------------------------------------------------------------
# FaultProxy – sits between client and server, applies FaultConfig
# ---------------------------------------------------------------------------
class FaultProxy:
    """
    A local UDP proxy that relays traffic between a test client and the real
    RUDP server while applying configured faults.

    Usage:
        proxy = FaultProxy(server_addr=("127.0.0.1", 2122), listen_port=0)
        proxy.set_faults(FaultConfig(drop_indices=[2, 5]))
        proxy.start()
        # ... send traffic to proxy.listen_addr ...
        proxy.stop()
    """

    def __init__(self, server_addr: tuple, listen_port: int = 0):
        self.server_addr = server_addr
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", listen_port))
        self.listen_addr = self.sock.getsockname()
        self.faults = FaultConfig()
        self._running = False
        self._thread: threading.Thread | None = None
        self._client_addr = None
        self._pkt_counter = 0  # counts server->client DATA packets
        self._lock = threading.Lock()
        self._reorder_buffer: dict[int, tuple[bytes, tuple]] = {}

    def set_faults(self, cfg: FaultConfig):
        with self._lock:
            self.faults = cfg
            self._pkt_counter = 0
            self._reorder_buffer.clear()

    def reset(self):
        self.set_faults(FaultConfig())

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._relay_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        # unblock recvfrom
        try:
            self.sock.sendto(b"", self.listen_addr)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2)
        self.sock.close()

    # ----- internal relay -----

    def _relay_loop(self):
        self.sock.settimeout(0.5)
        while self._running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            if not data:
                continue

            if addr == self.server_addr:
                # server -> client direction: apply faults
                self._handle_server_to_client(data)
            else:
                # client -> proxy: remember client, forward to server
                self._client_addr = addr
                self.sock.sendto(data, self.server_addr)

    def _handle_server_to_client(self, data: bytes):
        if self._client_addr is None:
            return

        with self._lock:
            cfg = self.faults

        # Count only DATA packets
        parsed = rudp_lib.parse_packet(data)
        is_data = parsed is not None and (parsed[2] & rudp_lib.FLAG_DATA)
        if is_data:
            self._pkt_counter += 1
        idx = self._pkt_counter

        # --- probabilistic loss ---
        if cfg.loss_percent and random.randint(1, 100) <= cfg.loss_percent:
            return  # drop

        # --- deterministic drop ---
        if idx in cfg.drop_indices:
            return  # drop

        # --- corruption ---
        if idx in cfg.corrupt_indices:
            data = self._corrupt(data)

        # --- truncation ---
        if idx in cfg.truncate_indices:
            data = data[: max(4, len(data) // 2)]

        # --- duplication ---
        dup = idx in cfg.duplicate_indices

        # --- reorder (swap pairs) ---
        for a, b in cfg.reorder_pairs:
            if idx == a:
                self._reorder_buffer[a] = (data, self._client_addr)
                return  # hold packet a until b arrives
            if idx == b:
                # send b first, then a
                self._delayed_send(data, self._client_addr, cfg.global_delay_sec)
                held = self._reorder_buffer.pop(a, None)
                if held:
                    self._delayed_send(held[0], held[1], cfg.global_delay_sec)
                return

        # --- per-packet delay ---
        delay = cfg.delay_indices.get(idx, cfg.global_delay_sec)

        self._delayed_send(data, self._client_addr, delay)
        if dup:
            self._delayed_send(data, self._client_addr, delay + 0.01)

    def _delayed_send(self, data: bytes, addr: tuple, delay: float):
        if delay > 0:
            threading.Timer(delay, self._raw_send, args=(data, addr)).start()
        else:
            self._raw_send(data, addr)

    def _raw_send(self, data: bytes, addr: tuple):
        try:
            self.sock.sendto(data, addr)
        except OSError:
            pass

    @staticmethod
    def _corrupt(data: bytes) -> bytes:
        """Flip a few bits in the payload area (after header)."""
        ba = bytearray(data)
        if len(ba) > rudp_lib.HEADER_SIZE + 1:
            pos = random.randint(rudp_lib.HEADER_SIZE, len(ba) - 1)
            ba[pos] ^= 0xFF
        return bytes(ba)
