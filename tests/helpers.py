"""
Test helpers for the edge-case / Wireshark-visible test suite.

Provides:
  - Server process management (start / wait-ready / stop)
  - TCP helper to send raw commands and TEST_RUDP config
  - RUDP client helper that sends through the FaultProxy
  - Marker-rich file creation for Wireshark visibility
"""

import os
import socket
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Paths & ports
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OBJ_TCP_PORT = 2121
OBJ_RUDP_PORT = 2122
DNS_PORT = 5053
LOCAL_HOST = "127.0.0.1"
BUFFER_SIZE = 4096

# ---------------------------------------------------------------------------
# Wireshark marker constant
# ---------------------------------------------------------------------------
MARKER_PREFIX = "TEST_CASE:"


def marker_bytes(test_name: str) -> bytes:
    """Return the Wireshark marker for *test_name*."""
    return f"{MARKER_PREFIX}{test_name}".encode("utf-8")


# ---------------------------------------------------------------------------
# TCP helpers
# ---------------------------------------------------------------------------
def tcp_command(cmd: str, data: bytes | None = None, timeout: float = 5.0) -> str:
    """Send a single TCP command to the object storage server and return the reply."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((LOCAL_HOST, OBJ_TCP_PORT))
    s.send(cmd.encode("utf-8"))

    if data is not None:
        resp = s.recv(BUFFER_SIZE).decode("utf-8")
        if resp == "READY":
            s.sendall(data)
            final = s.recv(BUFFER_SIZE).decode("utf-8")
            s.close()
            return final
        s.close()
        return resp

    resp = s.recv(BUFFER_SIZE).decode("utf-8")
    s.close()
    return resp


def test_rudp_reset():
    """Reset server-side RUDP fault injection to defaults."""
    return tcp_command("TEST_RUDP RESET")


def test_rudp_config(**kwargs) -> str:
    """Configure server-side fault injection.

    Keyword args: LOSS_PERCENT, DELAY_MS, DROP_SEQS, DELAY_SEQS, CORRUPT_SEQS
    List values are comma-separated (e.g. DROP_SEQS=[1,3] -> "DROP_SEQS=1,3").
    """
    tokens = []
    for k, v in kwargs.items():
        if isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        tokens.append(f"{k.upper()}={v}")
    cmd = "TEST_RUDP CONFIG " + " ".join(tokens)
    return tcp_command(cmd)


# ---------------------------------------------------------------------------
# File upload / download helpers
# ---------------------------------------------------------------------------
def upload_marker_file(test_name: str, size: int = 4096) -> str:
    """Create and upload a file whose content contains the Wireshark marker.

    Returns the key (filename) used on the server.
    """
    key = f"test-wire-{test_name}.bin"
    tag = marker_bytes(test_name)
    # Fill with marker repeated to reach desired size
    body = (tag + b"\n") * (size // (len(tag) + 1) + 1)
    body = body[:size]

    resp = tcp_command(f"PUT {key} {len(body)}", body)
    if "SUCCESS" not in resp:
        raise RuntimeError(f"Upload failed for {test_name}: {resp}")
    return key


def delete_key(key: str):
    """Delete a key from the server (best-effort cleanup)."""
    try:
        tcp_command(f"DELETE {key}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Server readiness probes
# ---------------------------------------------------------------------------
def wait_tcp_ready(timeout: float = 15.0):
    """Block until the TCP object storage server accepts connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect((LOCAL_HOST, OBJ_TCP_PORT))
            s.close()
            return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    raise RuntimeError("TCP server did not become ready in time")


def wait_dns_ready(timeout: float = 10.0):
    """Block until the local DNS server responds."""
    try:
        from dnslib import DNSRecord
    except ImportError:
        return  # Can't check without dnslib

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            q = DNSRecord.question("object.store")
            q.send(LOCAL_HOST, DNS_PORT, tcp=False, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("DNS server did not become ready in time")


# ---------------------------------------------------------------------------
# Server process management
# ---------------------------------------------------------------------------
_server_processes: list[subprocess.Popen] = []


def start_servers():
    """Launch DHCP, DNS, and Object Storage servers as background processes."""
    global _server_processes

    # Kill any lingering server processes first
    _kill_existing_servers()
    time.sleep(0.5)

    scripts = [
        os.path.join(PROJECT_ROOT, "src", "servers", "dhcp_server.py"),
        os.path.join(PROJECT_ROOT, "src", "servers", "local_dns.py"),
        os.path.join(PROJECT_ROOT, "src", "servers", "object_storage_server.py"),
    ]

    env = os.environ.copy()
    # Add src/ to PYTHONPATH so "from common import rudp_lib" resolves
    src_dir = os.path.join(PROJECT_ROOT, "src")
    env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")
    # Use non-privileged ports for DHCP if not admin
    if sys.platform == "win32":
        try:
            import ctypes

            if not ctypes.windll.shell32.IsUserAnAdmin():
                env["DHCP_SERVER_PORT"] = "6700"
                env["DHCP_CLIENT_PORT"] = "6800"
        except Exception:
            env["DHCP_SERVER_PORT"] = "6700"
            env["DHCP_CLIENT_PORT"] = "6800"

    for script in scripts:
        proc = subprocess.Popen(
            [sys.executable, script],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        _server_processes.append(proc)
        time.sleep(0.8)

    # Wait for the critical servers to be ready
    wait_tcp_ready()
    wait_dns_ready()


def stop_servers():
    """Terminate all background server processes."""
    global _server_processes
    for proc in _server_processes:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _server_processes.clear()
    _kill_existing_servers()


def _kill_existing_servers():
    """Best-effort cleanup of running server processes."""
    targets = ["dhcp_server.py", "local_dns.py", "object_storage_server.py"]
    if sys.platform == "win32":
        for t in targets:
            subprocess.run(
                [
                    "powershell",
                    "-Command",
                    f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{t}*' }} | Stop-Process -Force -ErrorAction SilentlyContinue",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    else:
        for t in targets:
            subprocess.run(
                ["pkill", "-f", t], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
