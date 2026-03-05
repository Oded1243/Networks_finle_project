import binascii
import os
import random
import socket
import struct
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


def create_dhcp_request(xid, mac_bytes, message_type):
    """Builds a DHCP request packet."""
    header = struct.pack("!BBBB", OP_BOOTREQUEST, HTYPE_ETHERNET, HLEN_MAC, 0)
    xid_secs_flags = struct.pack("!IHH", xid, 0, 0x8000)
    zero_ip = socket.inet_aton("0.0.0.0")
    ips = struct.pack("!4s4s4s4s", zero_ip, zero_ip, zero_ip, zero_ip)
    chaddr = mac_bytes + b"\x00" * 10
    sname_file_cookie = b"\x00" * 192 + struct.pack("!I", 0x63825363)
    options = struct.pack("!BBB", OPT_MESSAGE_TYPE, 1, message_type)
    options += struct.pack("!B", OPT_END)
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
            offered_ip = socket.inet_ntoa(offer_data[16:20])
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


def create_test_image(filename):
    """Generates a small valid BMP image for testing."""
    # Simple BMP Header (1x1 pixel)
    bmp_header = b"BM\x3e\x00\x00\x00\x00\x00\x00\x00\x36\x00\x00\x00\x28\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x18\x00\x00\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\x00\x00\x00\x00"
    # Adding padding data to make it larger
    data = bmp_header + b"\x00" * 1000
    with open(filename, "wb") as f:
        f.write(data)
    print(f"[*] Generated test image '{filename}' ({len(data)} bytes).")
    return len(data)


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

        # Test Image
        object_key = "test_image.bmp"
        filesize = create_test_image(object_key)

        # 1. LIST_BUCKETS (Replicas)
        print("[*] Storage Nodes (Buckets):")
        client_socket.send("LIST_BUCKETS".encode("utf-8"))
        print(f"{client_socket.recv(BUFFER_SIZE).decode('utf-8')}\n")

        # 2. PUT (Upload Image) - Replicates automatically
        print(f"[*] PUT: Uploading image '{object_key}'...")
        client_socket.send(f"PUT {object_key} {filesize}".encode("utf-8"))

        response = client_socket.recv(BUFFER_SIZE).decode("utf-8")
        if response == "READY":
            with open(object_key, "rb") as f:
                client_socket.sendall(f.read())
            print(f"[TCP] {client_socket.recv(BUFFER_SIZE).decode('utf-8')}")
        else:
            print(f"[-] Server not ready: {response}")

        # 3. LIST Objects
        print(f"[*] LIST objects...")
        client_socket.send("LIST".encode("utf-8"))
        local_list = client_socket.recv(BUFFER_SIZE).decode("utf-8")
        print(f"-- Objects --\n{local_list}\n-------------")

        # 4. GET (Download Image)
        if object_key in local_list:
            print(f"\n[*] GET: Downloading image '{object_key}'...")
            client_socket.send(f"GET {object_key}".encode("utf-8"))

            response = client_socket.recv(BUFFER_SIZE).decode("utf-8")
            if response.startswith("OK"):
                size = int(response.split(" ")[1])
                print(f"[+] Server ready. Object size: {size} bytes")
                client_socket.send("READY".encode("utf-8"))

                filepath = os.path.join(CLIENT_DIR, object_key)
                with open(filepath, "wb") as f:
                    bytes_received = 0
                    while bytes_received < size:
                        chunk = client_socket.recv(
                            min(size - bytes_received, BUFFER_SIZE)
                        )
                        if not chunk:
                            break
                        f.write(chunk)
                        bytes_received += len(chunk)
                print(f"[V] Downloaded to '{CLIENT_DIR}'.")
            else:
                print(f"[-] Server refused GET: {response}")

        # 5. DELETE Object (Removes all replicas)
        print(f"\n[*] DELETE: Deleting '{object_key}'...")
        client_socket.send(f"DELETE {object_key}".encode("utf-8"))
        print(f"[TCP] {client_socket.recv(BUFFER_SIZE).decode('utf-8')}")

        # Cleanup local file
        if os.path.exists(object_key):
            os.remove(object_key)

        client_socket.send("QUIT".encode("utf-8"))
        print("[*] TCP Session Closed.")

    except Exception as e:
        print(f"[-] TCP Client error: {e}")
    finally:
        client_socket.close()


def rudp_object_client(server_ip, object_key):
    """Connects via RUDP to GET an object (image)."""
    print(f"\n--- Phase 4: Object Storage Client (RUDP) ---")

    # Pre-check: Ensure the object exists using TCP for the test
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((server_ip, OBJ_TCP_PORT))

        # Re-upload image
        filesize = create_test_image(object_key)
        sock.send(f"PUT {object_key} {filesize}".encode("utf-8"))
        if sock.recv(1024) == b"READY":
            with open(object_key, "rb") as f:
                sock.sendall(f.read())
            sock.recv(1024)
        sock.close()
        print(f"[*] (Setup) Re-uploaded '{object_key}' via TCP.")
        if os.path.exists(object_key):
            os.remove(object_key)

    except Exception as e:
        print(f"[-] Setup failed: {e}")

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

            # Since TCP connection deletes the file at the end, run RUDP test
            rudp_object_client(server_ip, "test_image.bmp")
        else:
            print("[!] DNS Resolution failed.")
    else:
        print("[!] DHCP Handshake failed.")
