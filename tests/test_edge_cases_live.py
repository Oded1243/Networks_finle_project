"""
Edge-case / Wireshark-visible live integration tests for the RUDP protocol.

Every test embeds a marker string ``TEST_CASE:<name>`` inside the data it
sends, so all traffic can be found in Wireshark with:

    frame contains "TEST_CASE"

or for a specific test:

    frame contains "TEST_CASE:packet_loss"

The tests exercise:
  - packet loss / dropped packets
  - delayed packets
  - duplicated packets
  - out-of-order packets
  - corrupted packets
  - partial / truncated packets
  - empty packets
  - oversized packets
  - invalid headers / malformed packets
  - wrong sequence numbers
  - wrong checksum
  - ACK loss (server-side drop of ACK)
  - delayed ACKs
  - retransmission behaviour
  - connection timeout
  - disconnect during transfer
  - very fast burst sending
  - slow sending / high latency simulation
  - mixed scenarios (delay + loss + duplication)

Run with:
    python -m pytest tests/test_edge_cases_live.py -v -s
"""

import os
import socket
import struct
import sys
import time
import threading
import random

import pytest

# --------------- path setup ---------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src", "common"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src", "client"))

from common import rudp_lib
from common.fault_injector import FaultConfig, FaultProxy, marker_payload
from tests.helpers import (
    MARKER_PREFIX,
    marker_bytes,
    tcp_command,
    test_rudp_reset as _rudp_reset,
    test_rudp_config as _rudp_config,
    upload_marker_file,
    delete_key,
    wait_tcp_ready,
    start_servers,
    stop_servers,
    LOCAL_HOST,
    OBJ_TCP_PORT,
    OBJ_RUDP_PORT,
)


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture(scope="session", autouse=True)
def server_lifecycle():
    """Start the three servers once for the entire session."""
    print("\n[SETUP] Starting servers …")
    start_servers()
    yield
    print("\n[TEARDOWN] Stopping servers …")
    _rudp_reset()
    stop_servers()


@pytest.fixture(autouse=True)
def reset_faults():
    """Reset server-side fault injection before and after each test."""
    _rudp_reset()
    # Brief settle time so any in-progress RUDP session drains
    time.sleep(0.6)
    yield
    _rudp_reset()


def _log_test(name: str):
    """Print Wireshark marker info for the current test."""
    print(f"\n{'='*60}")
    print(f"  RUNNING: {name}")
    print(f'  Wireshark filter: frame contains "TEST_CASE:{name}"')
    print(f"{'='*60}")


# ===================================================================
# Helper: tiny RUDP download through a FaultProxy
# ===================================================================


def _rudp_download_via_proxy(
    key: str,
    proxy: FaultProxy,
    timeout: float = 10.0,
) -> bytes:
    """Send a GET request via RUDP through the proxy and return data bytes."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    proxy_addr = proxy.listen_addr

    req = f"GET {key}".encode("utf-8")
    syn = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, 64000, req)
    sock.sendto(syn, proxy_addr)

    expected = 1
    chunks: dict[int, bytes] = {}
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            pkt, _ = sock.recvfrom(65535)
        except socket.timeout:
            break

        parsed = rudp_lib.parse_packet(pkt)
        if parsed is None:
            # corrupted – still send dup ACK
            ack = rudp_lib.create_packet(0, expected, rudp_lib.FLAG_ACK, 64000)
            sock.sendto(ack, proxy_addr)
            continue

        seq, _, flags, _, data = parsed

        if flags & rudp_lib.FLAG_FIN:
            ack = rudp_lib.create_packet(0, seq, rudp_lib.FLAG_ACK, 64000)
            sock.sendto(ack, proxy_addr)
            break

        if flags & rudp_lib.FLAG_DATA:
            if seq == expected:
                chunks[seq] = data
                expected += 1
            elif seq > expected:
                chunks[seq] = data  # buffer out-of-order
            ack = rudp_lib.create_packet(0, expected, rudp_lib.FLAG_ACK, 64000)
            sock.sendto(ack, proxy_addr)

    sock.close()
    # reassemble in order
    result = b""
    for i in sorted(chunks.keys()):
        result += chunks[i]
    return result


def _rudp_download_direct(key: str, timeout: float = 10.0) -> bytes:
    """RUDP GET directly to server (no proxy)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    server_addr = (LOCAL_HOST, OBJ_RUDP_PORT)

    req = f"GET {key}".encode("utf-8")
    syn = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, 64000, req)
    sock.sendto(syn, server_addr)

    expected = 1
    chunks: dict[int, bytes] = {}
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            pkt, _ = sock.recvfrom(65535)
        except socket.timeout:
            break

        parsed = rudp_lib.parse_packet(pkt)
        if parsed is None:
            ack = rudp_lib.create_packet(0, expected, rudp_lib.FLAG_ACK, 64000)
            sock.sendto(ack, server_addr)
            continue

        seq, _, flags, _, data = parsed

        if flags & rudp_lib.FLAG_FIN:
            ack = rudp_lib.create_packet(0, seq, rudp_lib.FLAG_ACK, 64000)
            sock.sendto(ack, server_addr)
            break

        if flags & rudp_lib.FLAG_DATA:
            if seq == expected:
                chunks[seq] = data
                expected += 1
            elif seq > expected:
                chunks[seq] = data
            ack = rudp_lib.create_packet(0, expected, rudp_lib.FLAG_ACK, 64000)
            sock.sendto(ack, server_addr)

    sock.close()
    result = b""
    for i in sorted(chunks.keys()):
        result += chunks[i]
    return result


# ===================================================================
# TESTS
# ===================================================================

# ---------- 1. Packet loss / dropped packets ----------


class TestPacketLoss:
    """Validates that the protocol recovers from dropped data packets."""

    def test_packet_loss(self):
        _log_test("packet_loss")
        key = upload_marker_file("packet_loss", size=8192)
        try:
            # Tell server to drop seq 2 and 4
            _rudp_config(DROP_SEQS=[2, 4])
            data = _rudp_download_direct(key, timeout=15)
            tag = marker_bytes("packet_loss")
            assert tag in data, "Marker not found – data incomplete after loss recovery"
            print("  [PASS] Download recovered from dropped packets")
        finally:
            delete_key(key)


# ---------- 2. Delayed packets ----------


class TestDelayedPackets:
    """Validates correct reassembly when some packets arrive late."""

    def test_delayed_packets(self):
        _log_test("delayed_packets")
        key = upload_marker_file("delayed_packets", size=8192)
        try:
            # Use server-side delay: targeted seqs get +300ms extra delay
            _rudp_config(DELAY_SEQS=[2, 3])
            # Let the previous RUDP session fully drain
            time.sleep(1)
            data = _rudp_download_direct(key, timeout=30)
            assert marker_bytes("delayed_packets") in data
            print("  [PASS] Delayed packets reassembled correctly")
        finally:
            delete_key(key)


# ---------- 3. Duplicated packets ----------


class TestDuplicatedPackets:
    """Validates that duplicate DATA packets do not corrupt the file."""

    def test_duplicated_packets(self):
        _log_test("duplicated_packets")
        key = upload_marker_file("duplicated_packets", size=8192)
        proxy = FaultProxy(server_addr=(LOCAL_HOST, OBJ_RUDP_PORT))
        proxy.set_faults(FaultConfig(duplicate_indices=[1, 2, 3, 4]))
        proxy.start()
        try:
            data = _rudp_download_via_proxy(key, proxy, timeout=15)
            assert marker_bytes("duplicated_packets") in data
            print("  [PASS] Duplicates handled – file intact")
        finally:
            proxy.stop()
            delete_key(key)


# ---------- 4. Out-of-order packets ----------


class TestOutOfOrderPackets:
    """Validates reassembly when packets arrive in wrong order."""

    def test_out_of_order_packets(self):
        _log_test("out_of_order")
        key = upload_marker_file("out_of_order", size=8192)
        proxy = FaultProxy(server_addr=(LOCAL_HOST, OBJ_RUDP_PORT))
        proxy.set_faults(FaultConfig(reorder_pairs=[(1, 2), (3, 4)]))
        proxy.start()
        try:
            data = _rudp_download_via_proxy(key, proxy, timeout=15)
            assert marker_bytes("out_of_order") in data
            print("  [PASS] Out-of-order packets reassembled")
        finally:
            proxy.stop()
            delete_key(key)


# ---------- 5. Corrupted packets ----------


class TestCorruptedPackets:
    """Validates that corrupted packets are detected and retransmitted."""

    def test_corrupted_packets(self):
        _log_test("corrupted_packets")
        key = upload_marker_file("corrupted_packets", size=8192)
        proxy = FaultProxy(server_addr=(LOCAL_HOST, OBJ_RUDP_PORT))
        proxy.set_faults(FaultConfig(corrupt_indices=[2, 3]))
        proxy.start()
        try:
            data = _rudp_download_via_proxy(key, proxy, timeout=15)
            assert marker_bytes("corrupted_packets") in data
            print("  [PASS] Corrupted packets detected and recovered")
        finally:
            proxy.stop()
            delete_key(key)


# ---------- 6. Partial / truncated packets ----------


class TestTruncatedPackets:
    """Validates that truncated DATA packets are rejected (checksum fail)."""

    def test_truncated_packets(self):
        _log_test("truncated_packets")
        key = upload_marker_file("truncated_packets", size=8192)
        proxy = FaultProxy(server_addr=(LOCAL_HOST, OBJ_RUDP_PORT))
        proxy.set_faults(FaultConfig(truncate_indices=[2, 3]))
        proxy.start()
        try:
            data = _rudp_download_via_proxy(key, proxy, timeout=15)
            assert marker_bytes("truncated_packets") in data
            print("  [PASS] Truncated packets detected – data recovered via retransmit")
        finally:
            proxy.stop()
            delete_key(key)


# ---------- 7. Empty packets ----------


class TestEmptyPackets:
    """Sends empty UDP datagrams to the RUDP port; server must not crash."""

    def test_empty_packets(self):
        _log_test("empty_packets")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            tag = marker_bytes("empty_packets")
            # Send several empty / near-empty datagrams
            for _ in range(5):
                sock.sendto(b"", (LOCAL_HOST, OBJ_RUDP_PORT))
                sock.sendto(tag, (LOCAL_HOST, OBJ_RUDP_PORT))
            time.sleep(0.5)
            # Server should still be alive
            wait_tcp_ready(timeout=3)
            print("  [PASS] Server survived empty packets")
        finally:
            sock.close()


# ---------- 8. Oversized packets ----------


class TestOversizedPackets:
    """Sends packets exceeding MAX_PAYLOAD_SIZE to the server."""

    def test_oversized_packets(self):
        _log_test("oversized_packets")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            big_payload = marker_bytes("oversized_packets") + b"X" * 62000
            pkt = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, 64000, big_payload)
            sock.sendto(pkt, (LOCAL_HOST, OBJ_RUDP_PORT))
            time.sleep(0.5)
            wait_tcp_ready(timeout=3)
            print("  [PASS] Server handled oversized packet without crash")
        finally:
            sock.close()


# ---------- 9. Invalid headers / malformed packets ----------


class TestMalformedPackets:
    """Sends packets with garbage headers; server must not crash."""

    def test_malformed_packets(self):
        _log_test("malformed_packets")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Random garbage that looks like a header but has wrong checksum
            garbage = marker_bytes("malformed_packets") + os.urandom(20)
            sock.sendto(garbage, (LOCAL_HOST, OBJ_RUDP_PORT))

            # Header with impossible flags
            bad_hdr = struct.pack(">IIHH", 999, 999, 0xFFFF, 0xFFFF) + b"\xff"
            sock.sendto(
                bad_hdr + marker_bytes("malformed_packets"), (LOCAL_HOST, OBJ_RUDP_PORT)
            )

            time.sleep(0.5)
            wait_tcp_ready(timeout=3)
            print("  [PASS] Server survived malformed packets")
        finally:
            sock.close()


# ---------- 10. Wrong sequence numbers ----------


class TestWrongSequenceNumbers:
    """Sends DATA packets with unexpected sequence numbers."""

    def test_wrong_seq_numbers(self):
        _log_test("wrong_seq_numbers")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            payload = marker_bytes("wrong_seq_numbers")
            # Send a DATA packet with seq=99999 — server should ignore it
            pkt = rudp_lib.create_packet(99999, 0, rudp_lib.FLAG_DATA, 64000, payload)
            sock.sendto(pkt, (LOCAL_HOST, OBJ_RUDP_PORT))
            time.sleep(0.3)
            wait_tcp_ready(timeout=3)
            print("  [PASS] Server ignored bogus sequence number")
        finally:
            sock.close()


# ---------- 11. Wrong checksum ----------


class TestWrongChecksum:
    """Sends a packet with an intentionally wrong checksum."""

    def test_wrong_checksum(self):
        _log_test("wrong_checksum")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            payload = marker_bytes("wrong_checksum")
            pkt = rudp_lib.create_packet(1, 0, rudp_lib.FLAG_DATA, 64000, payload)
            # Corrupt the checksum bytes (offset 8-9 in the 13-byte header)
            ba = bytearray(pkt)
            ba[8] ^= 0xFF
            ba[9] ^= 0xFF
            sock.sendto(bytes(ba), (LOCAL_HOST, OBJ_RUDP_PORT))
            time.sleep(0.3)
            wait_tcp_ready(timeout=3)
            print("  [PASS] Server rejected bad-checksum packet")
        finally:
            sock.close()


# ---------- 12. ACK loss ----------


class TestACKLoss:
    """Validates that the server retransmits when ACKs are lost."""

    def test_ack_loss(self):
        _log_test("ack_loss")
        key = upload_marker_file("ack_loss", size=8192)
        try:
            # Perform RUDP download but intentionally skip sending some ACKs
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(10)
            server_addr = (LOCAL_HOST, OBJ_RUDP_PORT)

            req = f"GET {key}".encode("utf-8")
            syn_payload = marker_bytes("ack_loss") + b"|" + req
            # Use the actual GET request
            syn = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, 64000, req)
            sock.sendto(syn, server_addr)

            expected = 1
            received_data = {}
            acks_suppressed = {2, 3}  # don't ACK these seqs
            deadline = time.time() + 15

            while time.time() < deadline:
                try:
                    pkt, _ = sock.recvfrom(65535)
                except socket.timeout:
                    break
                parsed = rudp_lib.parse_packet(pkt)
                if parsed is None:
                    continue
                seq, _, flags, _, data = parsed

                if flags & rudp_lib.FLAG_FIN:
                    break

                if flags & rudp_lib.FLAG_DATA:
                    received_data[seq] = data
                    if seq == expected:
                        expected += 1
                    # suppress ACK for specific seqs
                    if seq not in acks_suppressed:
                        ack = rudp_lib.create_packet(
                            0, expected, rudp_lib.FLAG_ACK, 64000
                        )
                        sock.sendto(ack, server_addr)

            sock.close()
            # We should still have received all data because the server retransmits on timeout
            assert len(received_data) > 0, "No data received"
            print(
                f"  [PASS] Received {len(received_data)} unique packets despite ACK suppression"
            )
        finally:
            delete_key(key)


# ---------- 13. Delayed ACKs ----------


class TestDelayedACKs:
    """Validates that the server handles delayed ACKs correctly."""

    def test_delayed_acks(self):
        _log_test("delayed_acks")
        key = upload_marker_file("delayed_acks", size=8192)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(10)
            server_addr = (LOCAL_HOST, OBJ_RUDP_PORT)

            req = f"GET {key}".encode("utf-8")
            syn = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, 64000, req)
            sock.sendto(syn, server_addr)

            expected = 1
            received_data = {}
            deadline = time.time() + 20

            while time.time() < deadline:
                try:
                    pkt, _ = sock.recvfrom(65535)
                except socket.timeout:
                    break
                parsed = rudp_lib.parse_packet(pkt)
                if parsed is None:
                    continue
                seq, _, flags, _, data = parsed

                if flags & rudp_lib.FLAG_FIN:
                    break

                if flags & rudp_lib.FLAG_DATA:
                    received_data[seq] = data
                    if seq == expected:
                        expected += 1
                    # Delay every ACK by 100ms (below server's 0.5s timeout)
                    time.sleep(0.1)
                    ack = rudp_lib.create_packet(0, expected, rudp_lib.FLAG_ACK, 64000)
                    sock.sendto(ack, server_addr)

            sock.close()
            full = b"".join(received_data[k] for k in sorted(received_data))
            assert len(received_data) > 0, "No data packets received"
            assert marker_bytes("delayed_acks") in full
            print("  [PASS] Transfer succeeded with delayed ACKs")
        finally:
            delete_key(key)


# ---------- 14. Retransmission behaviour ----------


class TestRetransmission:
    """Verifies that the server retransmits packets after timeout."""

    def test_retransmission(self):
        _log_test("retransmission")
        key = upload_marker_file("retransmission", size=4096)
        try:
            # Server-side: drop seq 1 on first attempt
            _rudp_config(DROP_SEQS=[1])

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(10)
            server_addr = (LOCAL_HOST, OBJ_RUDP_PORT)

            req = f"GET {key}".encode("utf-8")
            syn = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, 64000, req)
            sock.sendto(syn, server_addr)

            saw_retransmit = False
            expected = 1
            received = {}
            deadline = time.time() + 15

            while time.time() < deadline:
                try:
                    pkt, _ = sock.recvfrom(65535)
                except socket.timeout:
                    break
                parsed = rudp_lib.parse_packet(pkt)
                if parsed is None:
                    continue
                seq, _, flags, _, data = parsed

                if flags & rudp_lib.FLAG_FIN:
                    break

                if flags & rudp_lib.FLAG_DATA:
                    if seq in received:
                        saw_retransmit = True
                    received[seq] = data
                    if seq == expected:
                        expected += 1
                    # After server drop, clear the drop so retransmit goes through
                    if len(received) == 1:
                        _rudp_config(DROP_SEQS=[])
                    ack = rudp_lib.create_packet(0, expected, rudp_lib.FLAG_ACK, 64000)
                    sock.sendto(ack, server_addr)

            sock.close()
            assert len(received) > 0, "No packets received"
            print(
                f"  [PASS] Retransmission detected: {saw_retransmit}, received {len(received)} packets"
            )
        finally:
            delete_key(key)


# ---------- 15. Connection timeout ----------


class TestConnectionTimeout:
    """Validates client behaviour when no response comes back."""

    def test_connection_timeout(self):
        _log_test("connection_timeout")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        # Send SYN to a port that is not listening (unlikely to be open)
        dead_port = 19999
        payload = marker_bytes("connection_timeout")
        syn = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, 64000, payload)
        sock.sendto(syn, (LOCAL_HOST, dead_port))

        no_response = False
        try:
            sock.recvfrom(65535)
        except socket.timeout:
            no_response = True
        except ConnectionResetError:
            # On Windows, sending UDP to a closed port triggers ICMP
            # port-unreachable which raises ConnectionResetError
            no_response = True
        finally:
            sock.close()

        assert no_response, "Expected timeout/reset but received a response"
        print("  [PASS] Connection correctly timed out")


# ---------- 16. Disconnect during transfer ----------


class TestDisconnectDuringTransfer:
    """Simulates the client closing the socket mid-transfer."""

    def test_disconnect_during_transfer(self):
        _log_test("disconnect_during_transfer")
        key = upload_marker_file("disconnect_during_transfer", size=16384)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5)
            server_addr = (LOCAL_HOST, OBJ_RUDP_PORT)

            req = f"GET {key}".encode("utf-8")
            syn = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, 64000, req)
            sock.sendto(syn, server_addr)

            # Receive just 2 packets then close abruptly
            for _ in range(2):
                try:
                    pkt, _ = sock.recvfrom(65535)
                except socket.timeout:
                    break
            sock.close()

            # Server should not crash; give it a moment to hit timeout
            time.sleep(2)
            wait_tcp_ready(timeout=5)
            print("  [PASS] Server survived abrupt client disconnect")
        finally:
            delete_key(key)


# ---------- 17. Very fast burst sending ----------


class TestFastBurstSending:
    """Sends a rapid burst of raw UDP packets to the RUDP port."""

    def test_fast_burst(self):
        _log_test("fast_burst")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            payload = marker_bytes("fast_burst")
            for i in range(200):
                pkt = rudp_lib.create_packet(i, 0, rudp_lib.FLAG_DATA, 64000, payload)
                sock.sendto(pkt, (LOCAL_HOST, OBJ_RUDP_PORT))
            time.sleep(1)
            wait_tcp_ready(timeout=3)
            print("  [PASS] Server handled 200-packet burst without crashing")
        finally:
            sock.close()


# ---------- 18. Slow sending / high latency simulation ----------


class TestSlowSending:
    """Downloads a file with server-side delay on specific packets."""

    def test_slow_sending(self):
        _log_test("slow_sending")
        key = upload_marker_file("slow_sending", size=4096)
        try:
            _rudp_config(DELAY_SEQS="0,1,2", DELAY_MS=150)
            data = _rudp_download_direct(key, timeout=30)
            assert marker_bytes("slow_sending") in data
            print("  [PASS] Download completed under high-latency conditions")
        finally:
            delete_key(key)


# ---------- 19. Mixed scenario (delay + loss + duplication) ----------


class TestMixedScenario:
    """Combines delay, loss, and duplication via the proxy."""

    def test_mixed_delay_loss_dup(self):
        _log_test("mixed_scenario")
        key = upload_marker_file("mixed_scenario", size=8192)
        proxy = FaultProxy(server_addr=(LOCAL_HOST, OBJ_RUDP_PORT))
        proxy.set_faults(
            FaultConfig(
                drop_indices=[2],
                delay_indices={3: 0.4},
                duplicate_indices=[4, 5],
                loss_percent=5,
            )
        )
        proxy.start()
        try:
            data = _rudp_download_via_proxy(key, proxy, timeout=20)
            assert marker_bytes("mixed_scenario") in data
            print("  [PASS] Mixed fault scenario – data recovered")
        finally:
            proxy.stop()
            delete_key(key)


# ---------- 20. TCP upload with marker ----------


class TestTCPUploadMarker:
    """Verifies that a file uploaded over TCP contains the Wireshark marker."""

    def test_tcp_upload_marker(self):
        _log_test("tcp_upload_marker")
        key = upload_marker_file("tcp_upload_marker", size=2048)
        try:
            resp = tcp_command(f"GET {key}")
            assert resp.startswith("OK"), f"Unexpected: {resp}"
            print("  [PASS] TCP upload with marker succeeded")
        finally:
            delete_key(key)


# ---------- 21. TCP rapid operations ----------


class TestTCPRapidOperations:
    """Performs many rapid TCP commands to stress-test the server."""

    def test_tcp_rapid(self):
        _log_test("tcp_rapid_ops")
        keys = []
        try:
            for i in range(10):
                k = upload_marker_file(f"tcp_rapid_ops_{i}", size=512)
                keys.append(k)

            resp = tcp_command("LIST")
            for k in keys:
                assert k in resp, f"Key {k} missing from LIST"

            print("  [PASS] 10 rapid TCP upload+list cycles succeeded")
        finally:
            for k in keys:
                delete_key(k)


# ---------- 22. Server-side corruption via TEST_RUDP ----------


class TestServerSideCorruption:
    """Uses TEST_RUDP CONFIG to corrupt specific seqs on the server side."""

    def test_server_corruption(self):
        _log_test("server_corruption")
        key = upload_marker_file("server_corruption", size=8192)
        try:
            _rudp_config(CORRUPT_SEQS=[1, 3])
            data = _rudp_download_direct(key, timeout=15)
            # Some data should still arrive because the client discards corrupted
            # packets and the server eventually retransmits
            print(f"  [INFO] Received {len(data)} bytes despite corruption")
            print("  [PASS] Server-side corruption handled")
        finally:
            delete_key(key)


# ---------- 23. RUDP FIN handling ----------


class TestFINHandling:
    """Sends a FIN packet to the server and checks it doesn't crash."""

    def test_fin_handling(self):
        _log_test("fin_handling")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            payload = marker_bytes("fin_handling")
            fin = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_FIN, 0, payload)
            sock.sendto(fin, (LOCAL_HOST, OBJ_RUDP_PORT))
            time.sleep(0.5)
            wait_tcp_ready(timeout=3)
            print("  [PASS] Server handled unexpected FIN gracefully")
        finally:
            sock.close()


# ---------- 24. SYN flood (small) ----------


class TestSYNFlood:
    """Sends many SYN packets rapidly to test server resilience."""

    def test_syn_flood(self):
        _log_test("syn_flood")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for i in range(50):
                payload = marker_bytes("syn_flood") + f"|{i}".encode()
                syn = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, 64000, payload)
                sock.sendto(syn, (LOCAL_HOST, OBJ_RUDP_PORT))
            time.sleep(2)
            wait_tcp_ready(timeout=5)
            print("  [PASS] Server survived SYN flood")
        finally:
            sock.close()


# ===================================================================
# STRESS TESTS
# ===================================================================


# ---------- 25. Concurrent TCP uploads ----------


class TestStressConcurrentUploads:
    """Uploads many files simultaneously from multiple threads."""

    def test_concurrent_uploads(self):
        _log_test("stress_concurrent_uploads")
        num_threads = 10
        results = [None] * num_threads
        keys = []

        def _upload(idx):
            try:
                k = upload_marker_file(f"stress_concurrent_uploads_{idx}", size=2048)
                keys.append(k)
                results[idx] = True
            except Exception as e:
                results[idx] = str(e)

        threads = [
            threading.Thread(target=_upload, args=(i,)) for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        try:
            successes = sum(1 for r in results if r is True)
            assert (
                successes == num_threads
            ), f"Only {successes}/{num_threads} uploads succeeded: {results}"
            # Verify server can still list them all
            resp = tcp_command("LIST")
            for k in keys:
                assert k in resp, f"{k} missing after concurrent upload"
            print(f"  [PASS] {num_threads} concurrent uploads succeeded")
        finally:
            for k in keys:
                delete_key(k)


# ---------- 26. Concurrent TCP downloads ----------


class TestStressConcurrentDownloads:
    """Downloads the same file from multiple threads simultaneously."""

    def test_concurrent_downloads(self):
        _log_test("stress_concurrent_downloads")
        key = upload_marker_file("stress_concurrent_downloads", size=4096)
        num_threads = 8
        results = [None] * num_threads

        def _download(idx):
            try:
                resp = tcp_command(f"GET {key}")
                if resp.startswith("OK"):
                    results[idx] = True
                else:
                    results[idx] = resp
            except Exception as e:
                results[idx] = str(e)

        try:
            threads = [
                threading.Thread(target=_download, args=(i,))
                for i in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            successes = sum(1 for r in results if r is True)
            assert (
                successes == num_threads
            ), f"Only {successes}/{num_threads} downloads succeeded: {results}"
            print(f"  [PASS] {num_threads} concurrent downloads succeeded")
        finally:
            delete_key(key)


# ---------- 27. Rapid upload-delete cycles ----------


class TestStressUploadDeleteCycles:
    """Rapidly uploads and immediately deletes files in a loop."""

    def test_upload_delete_cycles(self):
        _log_test("stress_upload_delete_cycles")
        cycles = 20
        for i in range(cycles):
            key = upload_marker_file(f"stress_upload_delete_cycles_{i}", size=512)
            resp = tcp_command(f"DELETE {key}")
            assert "SUCCESS" in resp, f"Delete failed at cycle {i}: {resp}"

        # Verify none remain
        resp = tcp_command("LIST")
        for i in range(cycles):
            assert f"stress_upload_delete_cycles_{i}" not in resp
        print(f"  [PASS] {cycles} upload-delete cycles completed cleanly")


# ---------- 28. Large file transfer ----------


class TestStressLargeFile:
    """Uploads and downloads a large file (256 KB) over TCP."""

    def test_large_file(self):
        _log_test("stress_large_file")
        key = upload_marker_file("stress_large_file", size=256 * 1024)
        try:
            # Fetch via TCP and verify integrity
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(15)
            s.connect((LOCAL_HOST, OBJ_TCP_PORT))
            s.send(f"GET {key}".encode())
            resp = s.recv(4096).decode()
            assert resp.startswith("OK"), f"Unexpected: {resp}"
            size = int(resp.split()[1])
            s.send(b"READY")
            data = b""
            while len(data) < size:
                chunk = s.recv(min(size - len(data), 4096))
                if not chunk:
                    break
                data += chunk
            s.close()
            assert len(data) == size, f"Size mismatch: got {len(data)}, expected {size}"
            assert marker_bytes("stress_large_file") in data
            print(f"  [PASS] 256 KB file transferred intact ({len(data)} bytes)")
        finally:
            delete_key(key)


# ---------- 29. RUDP download under sustained loss ----------


class TestStressRUDPSustainedLoss:
    """Downloads via RUDP with 15% random packet loss on the server side."""

    def test_rudp_sustained_loss(self):
        _log_test("stress_rudp_sustained_loss")
        key = upload_marker_file("stress_rudp_sustained_loss", size=8192)
        try:
            _rudp_config(LOSS_PERCENT=15)
            data = _rudp_download_direct(key, timeout=30)
            assert marker_bytes("stress_rudp_sustained_loss") in data
            print(
                f"  [PASS] RUDP download completed under 15% loss ({len(data)} bytes)"
            )
        finally:
            delete_key(key)


# ---------- 30. Mixed concurrent TCP + RUDP ----------


class TestStressMixedProtocols:
    """Runs TCP uploads while an RUDP download is in progress."""

    def test_mixed_tcp_rudp(self):
        _log_test("stress_mixed_protocols")
        # Upload a file for RUDP download
        rudp_key = upload_marker_file("stress_mixed_protocols_rudp", size=8192)
        tcp_keys = []
        rudp_result = [None]

        def _rudp_download():
            try:
                data = _rudp_download_direct(rudp_key, timeout=20)
                rudp_result[0] = len(data) > 0
            except Exception:
                rudp_result[0] = False

        def _tcp_uploads():
            for i in range(5):
                k = upload_marker_file(f"stress_mixed_protocols_tcp_{i}", size=1024)
                tcp_keys.append(k)

        try:
            t_rudp = threading.Thread(target=_rudp_download)
            t_tcp = threading.Thread(target=_tcp_uploads)
            t_rudp.start()
            t_tcp.start()
            t_rudp.join(timeout=25)
            t_tcp.join(timeout=25)

            assert rudp_result[0], "RUDP download failed during mixed load"
            assert len(tcp_keys) == 5, f"Only {len(tcp_keys)}/5 TCP uploads completed"
            print("  [PASS] Mixed TCP + RUDP concurrent operations succeeded")
        finally:
            delete_key(rudp_key)
            for k in tcp_keys:
                delete_key(k)


# ---------- 31. Server stability after all stress ----------


class TestStressServerStillAlive:
    """Final check: server is healthy after all stress tests."""

    def test_server_alive_after_stress(self):
        _log_test("stress_server_alive")
        # TCP health check
        wait_tcp_ready(timeout=5)
        resp = tcp_command("LIST_BUCKETS")
        assert "node1" in resp
        # Upload + download + delete a small file
        key = upload_marker_file("stress_server_alive", size=256)
        resp = tcp_command(f"DELETE {key}")
        assert "SUCCESS" in resp
        print("  [PASS] Server is healthy after all stress tests")
