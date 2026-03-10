import binascii
import os
import random
import socket
import time
import sys

# Add common directory to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../common")))

from dnslib import DNSRecord
import rudp_lib

# --- Constants ---
# Ports
DHCP_SERVER_PORT = int(os.environ.get("DHCP_SERVER_PORT", 67))
DHCP_CLIENT_PORT = int(os.environ.get("DHCP_CLIENT_PORT", 68))
DNS_PORT = 5053
OBJ_TCP_PORT = 2121
OBJ_RUDP_PORT = 2122

# Network and file settings
LOCAL_HOST = "127.0.0.1"
BROADCAST_IP = "255.255.255.255"
CLIENT_DIR = "client_objects"
BUFFER_SIZE = 4096

# DHCP message types
OP_BOOTREQUEST = 1
HTYPE_ETHERNET = 1
HLEN_MAC = 6
DHCP_DISCOVER = 1
DHCP_REQUEST = 3

# DHCP Options
OPT_MESSAGE_TYPE = 53
OPT_END = 255


def _ip_to_bytes(ip_str):
    """Convert IP string to 4 bytes."""
    parts = ip_str.split(".")
    return bytes([int(p) for p in parts])


def _bytes_to_ip(data):
    """Convert 4 bytes to IP string."""
    return ".".join(str(b) for b in data[:4])


def _pack_uint32_be(value):
    """Pack 32-bit unsigned integer as big-endian bytes."""
    return value.to_bytes(4, "big")


def _pack_uint16_be(value):
    """Pack 16-bit unsigned integer as big-endian bytes."""
    return value.to_bytes(2, "big")


def _pack_uint8(value):
    """Pack 8-bit unsigned integer as bytes."""
    return bytes([value & 0xFF])


def create_dhcp_request(xid, mac_bytes, message_type):
    """Builds a DHCP request packet."""
    header = bytes([OP_BOOTREQUEST, HTYPE_ETHERNET, HLEN_MAC, 0])
    xid_secs_flags = _pack_uint32_be(xid) + _pack_uint16_be(0) + _pack_uint16_be(0x8000)
    zero_ip = _ip_to_bytes("0.0.0.0")
    ips = zero_ip + zero_ip + zero_ip + zero_ip
    chaddr = mac_bytes + b"\x00" * 10
    sname_file_cookie = b"\x00" * 192 + _pack_uint32_be(0x63825363)
    options = bytes([OPT_MESSAGE_TYPE, 1, message_type])
    options += bytes([OPT_END])
    return header + xid_secs_flags + ips + chaddr + sname_file_cookie + options


def perform_dhcp_handshake():
    """Simulates DORA process."""
    print("--- Phase 1: DHCP Handshake ---")
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    client_socket.bind(("0.0.0.0", DHCP_CLIENT_PORT))
    client_socket.settimeout(3.0)

    xid = random.randint(1, 0xFFFFFFFF)
    mac_bytes = b"\xaa\xbb\xcc\xdd\xee\xff"

    try:
        print("[*] Sending DHCP DISCOVER...")
        client_socket.sendto(
            create_dhcp_request(xid, mac_bytes, DHCP_DISCOVER),
            (BROADCAST_IP, DHCP_SERVER_PORT),
        )

        try:
            offer_data, _ = client_socket.recvfrom(BUFFER_SIZE)
            offered_ip = _bytes_to_ip(offer_data[16:20])
            print(f"[+] Received DHCP OFFER: IP {offered_ip}")
        except socket.timeout:
            print("[-] DHCP Server did not respond to DISCOVER.")
            return None

        time.sleep(1)

        print(f"[*] Sending DHCP REQUEST for IP {offered_ip}...")
        client_socket.sendto(
            create_dhcp_request(xid, mac_bytes, DHCP_REQUEST),
            (BROADCAST_IP, DHCP_SERVER_PORT),
        )

        try:
            client_socket.recvfrom(BUFFER_SIZE)
            print("[+] Received DHCP ACK! IP allocation successful.\n")
            return offered_ip
        except socket.timeout:
            print("[-] DHCP Server did not respond to REQUEST.")
            return None

    except Exception as e:
        print(f"[-] DHCP Error: {e}")
        return None
    finally:
        client_socket.close()


def resolve_domain(domain):
    """Contacts local DNS to resolve domain."""
    print(f"--- Phase 2: DNS Resolution for '{domain}' ---")
    q = DNSRecord.question(domain)
    try:
        answer_bytes = q.send(LOCAL_HOST, DNS_PORT, tcp=False, timeout=3)
        parsed_answer = DNSRecord.parse(answer_bytes)

        if len(parsed_answer.rr) > 0:
            ip = str(parsed_answer.rr[0].rdata)
            print(f"[+] DNS Resolved: {domain} -> {ip}\n")
            return ip
        else:
            print(f"[-] DNS Resolution failed for {domain}")
            return None
    except Exception as e:
        print(f"[-] DNS Error: {e}")
        return None


def tcp_object_client(server_ip):
    """Connects to Object Storage via TCP to manage Replicated Objects."""
    if not os.path.exists(CLIENT_DIR):
        os.makedirs(CLIENT_DIR)

    print(f"--- Phase 3: Object Storage Client (TCP) ---")
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        print(f"[*] Connecting to Object Storage at {server_ip}:{OBJ_TCP_PORT}...")
        client_socket.connect((server_ip, OBJ_TCP_PORT))
        print("[+] Connected successfully!\n")

        # 1. LIST_BUCKETS (Replicas)
        print("[*] Storage Nodes (Buckets):")
        client_socket.send("LIST_BUCKETS".encode("utf-8"))
        print(f"{client_socket.recv(BUFFER_SIZE).decode('utf-8')}\n")

        # 2. LIST Objects
        print(f"[*] LIST objects...")
        client_socket.send("LIST".encode("utf-8"))
        local_list = client_socket.recv(BUFFER_SIZE).decode("utf-8")
        print(f"-- Objects --\n{local_list}\n-------------")

        client_socket.send("QUIT".encode("utf-8"))
        print("[*] TCP Session Closed.")

    except Exception as e:
        print(f"[-] TCP Client error: {e}")
    finally:
        client_socket.close()


def rudp_object_client(server_ip, object_key):
    """Connects via RUDP to GET an object (image)."""
    print(f"\n--- Phase 4: Object Storage Client (RUDP) ---")

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_socket.settimeout(5.0)
    server_addr = (server_ip, OBJ_RUDP_PORT)

    print(f"[*] Sending RUDP SYN request for GET '{object_key}'...")
    req_data = f"GET {object_key}".encode("utf-8")
    syn_packet = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, 64000, req_data)
    client_socket.sendto(syn_packet, server_addr)

    expected_seq = 1
    filepath = os.path.join(CLIENT_DIR, f"rudp_{object_key}")
    print(f"[*] Waiting for RUDP data packets...")

    with open(filepath, "wb") as f:
        while True:
            try:
                packet_bytes, _ = client_socket.recvfrom(65535)
                parsed = rudp_lib.parse_packet(packet_bytes)
                if parsed is None:
                    continue
                seq_num, ack_num, flags, _, data = parsed

                if flags & rudp_lib.FLAG_FIN:
                    print("\n[*] Received FIN. Transfer complete.")
                    ack_packet = rudp_lib.create_packet(
                        0, seq_num, rudp_lib.FLAG_ACK, 64000
                    )
                    client_socket.sendto(ack_packet, server_addr)
                    break

                if flags & rudp_lib.FLAG_DATA:
                    if seq_num == expected_seq:
                        f.write(data)
                        print(f"  [+] Packet {seq_num} received. Sending ACK.")
                        expected_seq += 1
                        ack_packet = rudp_lib.create_packet(
                            0, expected_seq, rudp_lib.FLAG_ACK, 64000
                        )
                        client_socket.sendto(ack_packet, server_addr)
                    else:
                        print(
                            f"  [-] Out of order! Expected {expected_seq}, got {seq_num}."
                        )
                        ack_packet = rudp_lib.create_packet(
                            0, expected_seq, rudp_lib.FLAG_ACK, 64000
                        )
                        client_socket.sendto(ack_packet, server_addr)

            except socket.timeout:
                print("\n[-] RUDP Timeout.")
                break
            except Exception as e:
                print(f"\n[-] RUDP Error: {e}")
                break

    print(f"[V] RUDP Download finished: {filepath}")


if __name__ == "__main__":
    print("=== Starting Object Storage Client ===\n")

    my_ip = perform_dhcp_handshake()

    if my_ip:
        # Use new domain name
        target_domain = "object.store"
        server_ip = resolve_domain(target_domain)

        if server_ip:
            tcp_object_client(server_ip)
        else:
            print("[!] DNS Resolution failed.")
    else:
        print("[!] DHCP Handshake failed.")
