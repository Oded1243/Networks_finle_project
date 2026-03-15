import pytest
import socket
import struct


def _build_dns_query(domain):
    """Build a raw DNS A-record query packet."""
    import random

    tx_id = random.randint(0, 0xFFFF)
    flags = 0x0100
    header = struct.pack("!HHHHHH", tx_id, flags, 1, 0, 0, 0)
    qname = b""
    for part in domain.split("."):
        qname += bytes([len(part)]) + part.encode("utf-8")
    qname += b"\x00"
    question = qname + struct.pack("!HH", 1, 1)
    return header + question


def _parse_dns_response(data):
    """Parse a DNS response and return the first A-record IP or None."""
    if len(data) < 12:
        return None
    header = struct.unpack("!HHHHHH", data[:12])
    ancount = header[3]
    if ancount == 0:
        return None
    offset = 12
    while offset < len(data):
        length = data[offset]
        if length == 0:
            offset += 1
            break
        offset += 1 + length
    offset += 4
    for _ in range(ancount):
        if offset + 2 > len(data):
            break
        if data[offset] & 0xC0 == 0xC0:
            offset += 2
        else:
            while offset < len(data):
                length = data[offset]
                if length == 0:
                    offset += 1
                    break
                offset += 1 + length
        if offset + 10 > len(data):
            break
        rtype, _, _, rdlength = struct.unpack("!HHLH", data[offset : offset + 10])
        offset += 10
        if rtype == 1 and rdlength == 4:
            return socket.inet_ntoa(data[offset : offset + 4])
        offset += rdlength
    return None


def test_dns_query():
    print("Building DNS query for test.local...")

    print("Sending to 127.0.0.1 on port 5053...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        query = _build_dns_query("test.local")
        sock.sendto(query, ("127.0.0.1", 5053))
        data, _ = sock.recvfrom(512)
        sock.close()
        ip = _parse_dns_response(data)
        print(f"\n--- Success! Received Response ---")
        print(f"Resolved: test.local -> {ip}")

    except TimeoutError:
        pytest.fail("The server did not respond (Timeout).")
    except ConnectionRefusedError:
        pytest.fail("Connection refused. Is the server definitely running?")
    except Exception as e:
        pytest.fail(f"Unexpected Error: {e}")


if __name__ == "__main__":
    test_dns_query()
