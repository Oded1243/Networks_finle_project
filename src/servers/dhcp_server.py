import binascii
import os
import socket
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../common")))

import rudp_lib

DHCP_SERVER_PORT = int(os.environ.get("DHCP_SERVER_PORT", 67))
DHCP_CLIENT_PORT = int(os.environ.get("DHCP_CLIENT_PORT", 68))

OP_BOOTREQUEST = 1
OP_BOOTREPLY = 2

HTYPE_ETHERNET = 1
HLEN_MAC = 6

DHCP_DISCOVER = 1
DHCP_OFFER = 2
DHCP_REQUEST = 3
DHCP_ACK = 5

OPT_LEASE_TIME = 51
OPT_MESSAGE_TYPE = 53
OPT_SERVER_ID = 54
OPT_END = 255

SERVER_IP = "192.168.1.100"
BROADCAST_IP = "255.255.255.255"
LEASE_TIME_SEC = 3600

# IP pool range
POOL_START = "192.168.1.150"
POOL_END = "192.168.1.200"

# {mac_str: ip_str} – active leases
_leases = {}
# {ip_str: mac_str} – reverse lookup
_ip_to_mac = {}


def _next_available_ip():
    """Return the first unleased IP in the pool, or None if exhausted."""
    start = list(map(int, POOL_START.split(".")))
    end = list(map(int, POOL_END.split(".")))
    current = start[:]
    while current <= end:
        ip_str = ".".join(str(o) for o in current)
        if ip_str not in _ip_to_mac:
            return ip_str
        current[3] += 1
        for i in (3, 2, 1):
            if current[i] > 255:
                current[i] = 0
                current[i - 1] += 1
    return None


def _allocate_ip(mac_str):
    """Return an IP for *mac_str*, reusing an existing lease or picking a new one."""
    if mac_str in _leases:
        return _leases[mac_str]
    ip = _next_available_ip()
    if ip is None:
        print("[!] IP pool exhausted!")
        return None
    _leases[mac_str] = ip
    _ip_to_mac[ip] = mac_str
    return ip


def _ip_to_bytes(ip_str):
    """Convert IP string to 4 bytes."""
    parts = ip_str.split(".")
    return bytes([int(p) for p in parts])


def get_dhcp_message_type(options_bytes):
    """
    Extracts DHCP message type from the options area.
    """
    i = 0
    while i < len(options_bytes):
        opt_code = options_bytes[i]
        if opt_code == OPT_END:
            break
        if opt_code == 0:
            i += 1
            continue

        opt_len = options_bytes[i + 1]
        if opt_code == OPT_MESSAGE_TYPE:
            return options_bytes[i + 2]

        i += 2 + opt_len
    return None


def create_dhcp_response(xid, client_mac_bytes, message_type, offered_ip):
    """
    Builds a response packet (Offer or ACK).
    """
    server_ip_packed = _ip_to_bytes(SERVER_IP)
    offered_ip_packed = _ip_to_bytes(offered_ip)
    zero_ip_packed = _ip_to_bytes("0.0.0.0")

    header = bytes([OP_BOOTREPLY, HTYPE_ETHERNET, HLEN_MAC, 0])
    xid_secs_flags = (
        rudp_lib._pack_uint32_be(xid)
        + rudp_lib._pack_uint16_be(0)
        + rudp_lib._pack_uint16_be(0x8000)
    )

    ips = zero_ip_packed + offered_ip_packed + server_ip_packed + zero_ip_packed

    chaddr = client_mac_bytes + b"\x00" * 10
    sname_file_cookie = b"\x00" * 192 + rudp_lib._pack_uint32_be(0x63825363)

    options = bytes([OPT_MESSAGE_TYPE, 1, message_type])
    options += bytes([OPT_SERVER_ID, 4]) + server_ip_packed
    options += bytes([OPT_LEASE_TIME, 4]) + rudp_lib._pack_uint32_be(LEASE_TIME_SEC)
    options += bytes([OPT_END])

    return header + xid_secs_flags + ips + chaddr + sname_file_cookie + options


def start_dhcp_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server_socket.bind(("0.0.0.0", DHCP_SERVER_PORT))
    print(f"[*] DHCP Server listening on port {DHCP_SERVER_PORT}...")

    try:
        while True:
            packet_data, client_address = server_socket.recvfrom(1024)
            if len(packet_data) < 240:
                continue

            op = packet_data[0]
            if op == OP_BOOTREQUEST:
                xid = rudp_lib._unpack_uint32_be(packet_data[4:8])
                client_mac_bytes = packet_data[28:34]
                mac_str = binascii.hexlify(client_mac_bytes).decode("utf-8")

                options_bytes = packet_data[240:]
                msg_type = get_dhcp_message_type(options_bytes)

                if msg_type == DHCP_DISCOVER:
                    offered_ip = _allocate_ip(mac_str)
                    if offered_ip is None:
                        print(f"\n[!] No IPs available for {mac_str}")
                        continue
                    print(f"\n[+] Received DISCOVER from {mac_str}")
                    print(f"[*] Sending OFFER ({offered_ip})...")
                    response = create_dhcp_response(
                        xid, client_mac_bytes, DHCP_OFFER, offered_ip
                    )
                    server_socket.sendto(response, (BROADCAST_IP, DHCP_CLIENT_PORT))

                elif msg_type == DHCP_REQUEST:
                    offered_ip = _leases.get(mac_str)
                    if offered_ip is None:
                        offered_ip = _allocate_ip(mac_str)
                    if offered_ip is None:
                        print(f"\n[!] No IPs available for {mac_str}")
                        continue
                    print(f"\n[+] Received REQUEST from {mac_str} for IP {offered_ip}")
                    print("[*] Sending ACK...")
                    response = create_dhcp_response(
                        xid, client_mac_bytes, DHCP_ACK, offered_ip
                    )
                    server_socket.sendto(response, (BROADCAST_IP, DHCP_CLIENT_PORT))
                    print("[V] IP Assigned successfully!")

    except KeyboardInterrupt:
        print("\n[-] Server stopped.")
    finally:
        server_socket.close()


if __name__ == "__main__":
    start_dhcp_server()
