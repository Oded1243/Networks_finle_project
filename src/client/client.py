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

# --- Constants to avoid "magic numbers" --- (This code was created/improved with AI)
# Ports
DHCP_SERVER_PORT = 67
DHCP_CLIENT_PORT = 68
DNS_PORT = 5053  # Changed from 53 to prevent collisions on Windows
FTP_TCP_PORT = 2121

# Network and file settings
LOCAL_HOST = "127.0.0.1"
BROADCAST_IP = "255.255.255.255"
CLIENT_DIR = "client_downloads"  # Relative path so it works on any computer
BUFFER_SIZE = 4096

# DHCP message types and protocol
OP_BOOTREQUEST = 1
HTYPE_ETHERNET = 1
HLEN_MAC = 6
DHCP_DISCOVER = 1
DHCP_REQUEST = 3
FTP_RUDP_PORT = 2122

# Option numbers (DHCP Options)
OPT_MESSAGE_TYPE = 53
OPT_END = 255


def create_dhcp_request(xid, mac_bytes, message_type):
    """
    Builds a DHCP request packet (Discover or Request) for the client.
    (This function was created with AI assistance)
    """
    # Header
    header = struct.pack("!BBBB", OP_BOOTREQUEST, HTYPE_ETHERNET, HLEN_MAC, 0)
    xid_secs_flags = struct.pack("!IHH", xid, 0, 0x8000)  # Broadcast flag

    # Client IP, Your IP, Server IP, Gateway IP (All zeros because we don't have an IP yet)
    zero_ip = socket.inet_aton("0.0.0.0")
    ips = struct.pack("!4s4s4s4s", zero_ip, zero_ip, zero_ip, zero_ip)

    # MAC Address + Padding (16 bytes total)
    chaddr = mac_bytes + b"\x00" * 10

    # Magic Cookie
    sname_file_cookie = b"\x00" * 192 + struct.pack("!I", 0x63825363)

    # Options
    options = struct.pack("!BBB", OPT_MESSAGE_TYPE, 1, message_type)
    options += struct.pack("!B", OPT_END)

    return header + xid_secs_flags + ips + chaddr + sname_file_cookie + options


def perform_dhcp_handshake():
    """
    Simulates the DORA process against the DHCP server.
    (This function was written with AI assistance)
    """
    print("--- Phase 1: DHCP Handshake ---")
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    client_socket.bind(("0.0.0.0", DHCP_CLIENT_PORT))
    client_socket.settimeout(3.0)

    xid = random.randint(1, 0xFFFFFFFF)
    mac_bytes = b"\xaa\xbb\xcc\xdd\xee\xff"

    try:
        # 1. DISCOVER
        print("[*] Sending DHCP DISCOVER...")
        discover_packet = create_dhcp_request(xid, mac_bytes, DHCP_DISCOVER)
        client_socket.sendto(discover_packet, (BROADCAST_IP, DHCP_SERVER_PORT))

        # 2. OFFER
        offer_data, _ = client_socket.recvfrom(BUFFER_SIZE)
        offered_ip = socket.inet_ntoa(offer_data[16:20])
        print(f"[+] Received DHCP OFFER: IP {offered_ip}")
        time.sleep(1)

        # 3. REQUEST
        print(f"[*] Sending DHCP REQUEST for IP {offered_ip}...")
        request_packet = create_dhcp_request(xid, mac_bytes, DHCP_REQUEST)
        client_socket.sendto(request_packet, (BROADCAST_IP, DHCP_SERVER_PORT))

        # 4. ACK
        ack_data, _ = client_socket.recvfrom(BUFFER_SIZE)
        print("[+] Received DHCP ACK! IP allocation successful.\n")
        return offered_ip

    except socket.timeout:
        print("[-] DHCP Server did not respond.")
        return None
    finally:
        client_socket.close()


def resolve_ftp_domain(domain):
    """
    Contacts the local DNS server to translate the FTP server name to an IP address.
    (This function was written with AI assistance)
    """
    print(f"--- Phase 2: DNS Resolution for '{domain}' ---")
    q = DNSRecord.question(domain)
    try:
        answer_bytes = q.send(LOCAL_HOST, DNS_PORT, tcp=False, timeout=3)
        parsed_answer = DNSRecord.parse(answer_bytes)

        if len(parsed_answer.rr) > 0:
            ftp_ip = str(parsed_answer.rr[0].rdata)
            print(f"[+] DNS Resolved: {domain} -> {ftp_ip}\n")
            return ftp_ip
        else:
            print(f"[-] DNS Resolution failed: No records found for {domain}")
            return None

    except Exception as e:
        print(f"[-] DNS Error: {e}")
        return None


def tcp_ftp_client(server_ip):
    """
    Connects to the FTP server via TCP, requests a file list, and downloads a file.
    (This function was written with AI assistance)
    """
    if not os.path.exists(CLIENT_DIR):
        os.makedirs(CLIENT_DIR)

    print(f"--- Phase 3: FTP Application (TCP) ---")
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        print(f"[*] Connecting to FTP Server at {server_ip}:{FTP_TCP_PORT}...")
        client_socket.connect((server_ip, FTP_TCP_PORT))
        print("[+] Connected successfully!\n")

        # 1. LIST command
        print("[*] Sending LIST command...")
        client_socket.send("LIST".encode("utf-8"))
        file_list = client_socket.recv(BUFFER_SIZE).decode("utf-8")
        print(f"--- Files on Server ---\n{file_list}\n-----------------------")

        # 2. RETR command (downloads the first file in the list)
        files = file_list.split("\n")
        if files and files[0] != "Empty directory":
            target_file = files[0]
            print(f"\n[*] Sending RETR command for '{target_file}'...")
            client_socket.send(f"RETR {target_file}".encode("utf-8"))

            response = client_socket.recv(BUFFER_SIZE).decode("utf-8")
            if response.startswith("OK"):
                filesize = int(response.split(" ")[1])
                print(f"[+] Server ready to send. File size: {filesize} bytes")

                # Sending readiness confirmation to server
                client_socket.send("READY".encode("utf-8"))

                filepath = os.path.join(CLIENT_DIR, target_file)
                with open(filepath, "wb") as f:
                    bytes_received = 0
                    while bytes_received < filesize:
                        chunk = client_socket.recv(BUFFER_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)
                        bytes_received += len(chunk)

                print(
                    f"[V] File '{target_file}' downloaded successfully to '{CLIENT_DIR}' folder!"
                )
            else:
                print(f"[-] Server refused RETR: {response}")

        # 3. QUIT command
        client_socket.send("QUIT".encode("utf-8"))
        print("[*] Disconnected from FTP Server.")

    except Exception as e:
        print(f"[-] FTP Client error: {e}")
    finally:
        client_socket.close()


def rudp_ftp_client(server_ip, filename):
    """
    Connects to the FTP server using RUDP (Reliable UDP) that we developed and downloads the file.
    Implements the client side of Go-Back-N: receives in order and sends ACKs.
    (This function was written with AI assistance)
    """
    print(f"\n--- Phase 4: FTP Application (RUDP) ---")

    # UDP Socket
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_socket.settimeout(5.0)
    server_addr = (server_ip, FTP_RUDP_PORT)

    # 1. Sending connection request and download (SYN + RETR)
    print(f"[*] Sending RUDP SYN request for '{filename}'...")
    req_data = f"RETR {filename}".encode("utf-8")
    # Creating a packet using our library: seq=0, ack=0, flag=SYN
    syn_packet = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, req_data)
    client_socket.sendto(syn_packet, server_addr)

    # Client state management variables
    expected_seq = 1
    # Save the file with a rudp_ prefix to distinguish it from the TCP file
    filepath = os.path.join(CLIENT_DIR, f"rudp_{filename}")

    print(f"[*] Waiting for RUDP data packets...")

    with open(filepath, "wb") as f:
        while True:
            try:
                # Receiving a packet from the network (up to 65535 bytes)
                packet_bytes, _ = client_socket.recvfrom(65535)

                # Parsing the packet into parts according to our header
                seq_num, ack_num, flags, data = rudp_lib.parse_packet(packet_bytes)

                # 2. Did the server finish sending? (FIN)
                if flags & rudp_lib.FLAG_FIN:
                    print("\n[*] Received FIN flag. File transfer complete.")
                    print("[*] Sending final ACK...")
                    ack_packet = rudp_lib.create_packet(0, seq_num, rudp_lib.FLAG_ACK)
                    client_socket.sendto(ack_packet, server_addr)
                    break

                # 3. Is this a data packet (DATA)?
                if flags & rudp_lib.FLAG_DATA:
                    # Order checking (Go-Back-N Logic)
                    if seq_num == expected_seq:
                        # The packet arrived in the correct order! Save it
                        f.write(data)
                        print(f"  [+] Received packet {seq_num} (Valid). Sending ACK.")

                        expected_seq += (
                            1  # Increment the counter *before* sending the confirmation
                        )

                        # Send ACK with the next number we expect (new expected_seq)
                        ack_packet = rudp_lib.create_packet(
                            0, expected_seq, rudp_lib.FLAG_ACK
                        )
                        client_socket.sendto(ack_packet, server_addr)

                    else:
                        # Out of order packet!
                        print(
                            f"  [-] Out of order! Expected {expected_seq}, got {seq_num}. Dropping."
                        )
                        # Send ACK again to inform the server which packet we're still waiting for
                        ack_packet = rudp_lib.create_packet(
                            0, expected_seq, rudp_lib.FLAG_ACK
                        )
                        client_socket.sendto(ack_packet, server_addr)

            except socket.timeout:
                print("\n[-] RUDP Timeout: No packets received from server.")
                break
            except Exception as e:
                print(f"\n[-] RUDP Client error: {e}")
                break

    print(f"[V] RUDP Download finished. Saved to '{filepath}'.")


if __name__ == "__main__":
    print("=== Starting Client App ===\n")

    my_ip = perform_dhcp_handshake()

    if my_ip:
        ftp_server_name = "ftp.local"
        ftp_server_ip = resolve_ftp_domain(ftp_server_name)

        if ftp_server_ip:
            # Running the FTP stage (TCP)
            tcp_ftp_client(ftp_server_ip)

            # Phase 4: Connecting to the application server via RUDP (Reliable UDP)
            print("\n" + "=" * 40)
            rudp_ftp_client(ftp_server_ip, "test_file.txt")

        else:
            print("[!] Cannot proceed to FTP without DNS resolution.")
    else:
        print("[!] Cannot proceed without a valid IP address.")
