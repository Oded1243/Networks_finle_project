import socket
import time
import random
import os
import re
import sys

# Add common directory to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../common")))

try:
    from dnslib import DNSRecord
    import rudp_lib
except ImportError as e:
    print(f"Warning: Failed to import required modules: {e}")

# --- Constants ---
DHCP_SERVER_PORT = 67
DHCP_CLIENT_PORT = 68
DNS_PORT = 5053
OBJ_TCP_PORT = 2121
OBJ_RUDP_PORT = 2122
LOCAL_HOST = "127.0.0.1"
BROADCAST_IP = "255.255.255.255"
BUFFER_SIZE = 4096

# DHCP Constants
OP_BOOTREQUEST = 1
HTYPE_ETHERNET = 1
HLEN_MAC = 6
DHCP_DISCOVER = 1
DHCP_REQUEST = 3
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


class NetworkManager:
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.server_ip = None
        self.my_ip = None
        self.connected = False
        self.retry_interval = 5  # seconds
        self.max_retries = 6  # None = infinite retries

    def log(self, message):
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)

    def _retry_with_interval(self, func, func_name, *args, **kwargs):
        """
        Retry a function with 5-second intervals on failure.
        Displays retry attempts to the user.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                result = func(*args, **kwargs)
                if result is not None:
                    return result
                else:
                    # None result means failure, retry
                    self.log(
                        f"[!] {func_name} failed. Retrying in {self.retry_interval} seconds..."
                    )
                    time.sleep(self.retry_interval)
            except Exception as e:
                self.log(
                    f"[!] {func_name} error: {e}. Retrying in {self.retry_interval} seconds..."
                )
                time.sleep(self.retry_interval)

            if self.max_retries is not None and attempt >= self.max_retries:
                self.log(f"[-] {func_name} failed after {self.max_retries} attempts.")
                return None

    def _create_dhcp_request(self, xid, mac_bytes, message_type):
        header = bytes([OP_BOOTREQUEST, HTYPE_ETHERNET, HLEN_MAC, 0])
        xid_secs_flags = (
            _pack_uint32_be(xid) + _pack_uint16_be(0) + _pack_uint16_be(0x8000)
        )
        zero_ip = _ip_to_bytes("0.0.0.0")
        ips = zero_ip + zero_ip + zero_ip + zero_ip
        chaddr = mac_bytes + b"\x00" * 10
        sname_file_cookie = b"\x00" * 192 + _pack_uint32_be(0x63825363)
        options = bytes([OPT_MESSAGE_TYPE, 1, message_type])
        options += bytes([OPT_END])
        return header + xid_secs_flags + ips + chaddr + sname_file_cookie + options

    def _perform_dhcp_handshake_single(self):
        """Single DHCP handshake attempt (without retries)"""
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Bind to 0.0.0.0 to receive broadcast
        try:
            client_socket.bind(("0.0.0.0", DHCP_CLIENT_PORT))
        except OSError:
            # If port 68 is busy (e.g. real client running), try a random port or fail
            self.log("[-] Warning: Port 68 busy, DHCP might fail or conflict with OS.")
            # We proceed anyway or raise

        client_socket.settimeout(3.0)

        xid = random.randint(1, 0xFFFFFFFF)
        mac_bytes = b"\xaa\xbb\xcc\xdd\xee\xff"

        try:
            self.log("[*] Sending DHCP DISCOVER...")
            client_socket.sendto(
                self._create_dhcp_request(xid, mac_bytes, DHCP_DISCOVER),
                (BROADCAST_IP, DHCP_SERVER_PORT),
            )

            try:
                offer_data, _ = client_socket.recvfrom(BUFFER_SIZE)
                offered_ip = _bytes_to_ip(offer_data[16:20])
                self.log(f"[+] Received DHCP OFFER: IP {offered_ip}")
            except socket.timeout:
                self.log("[-] DHCP Server did not respond to DISCOVER.")
                return None

            time.sleep(0.5)

            self.log(f"[*] Sending DHCP REQUEST for IP {offered_ip}...")
            client_socket.sendto(
                self._create_dhcp_request(xid, mac_bytes, DHCP_REQUEST),
                (BROADCAST_IP, DHCP_SERVER_PORT),
            )

            try:
                client_socket.recvfrom(BUFFER_SIZE)
                self.log(f"[+] Received DHCP ACK! IP allocation successful.")
                self.my_ip = offered_ip
                return offered_ip
            except socket.timeout:
                self.log("[-] DHCP Server did not respond to REQUEST.")
                return None

        except Exception as e:
            self.log(f"[-] DHCP Error: {e}")
            return None
        finally:
            client_socket.close()

    def perform_dhcp_handshake(self):
        """DHCP handshake with automatic retries on failure"""
        self.log("--- Phase 1: DHCP Handshake ---")
        return self._retry_with_interval(
            self._perform_dhcp_handshake_single, "DHCP Handshake"
        )

    def _resolve_domain_single(self, domain="object.store"):
        """Single DNS resolution attempt (without retries)"""
        self.log(f"--- Phase 2: DNS Resolution for '{domain}' ---")
        q = DNSRecord.question(domain)
        try:
            answer_bytes = q.send(LOCAL_HOST, DNS_PORT, tcp=False, timeout=3)
            parsed_answer = DNSRecord.parse(answer_bytes)

            if len(parsed_answer.rr) > 0:
                ip = str(parsed_answer.rr[0].rdata)
                self.log(f"[+] DNS Resolved: {domain} -> {ip}")
                self.server_ip = ip
                return ip
            else:
                self.log(f"[-] DNS Resolution failed for {domain}")
                return None
        except Exception as e:
            self.log(f"[-] DNS Error: {e}")
            return None

    def resolve_domain(self, domain="object.store"):
        """DNS resolution with automatic retries on failure"""
        return self._retry_with_interval(
            self._resolve_domain_single, f"DNS Resolution for '{domain}'", domain
        )

    def connect_sequence(self):
        ip = self.perform_dhcp_handshake()
        if not ip:
            return False

        server_ip = self.resolve_domain()
        if not server_ip:
            return False

        self.connected = True
        return True

    def _tcp_send_command_single(self, cmd, data=None):
        """Single TCP command attempt (without retries)"""
        if not self.server_ip:
            self.log("[-] Not connected to server.")
            return None

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self.server_ip, OBJ_TCP_PORT))
            s.send(cmd.encode("utf-8"))

            # If we need to send data (like file content)
            if data:
                resp = s.recv(BUFFER_SIZE).decode("utf-8")
                if resp == "READY":
                    s.sendall(data)
                    final_resp = s.recv(BUFFER_SIZE).decode("utf-8")
                    s.close()
                    return final_resp
                else:
                    s.close()
                    return resp

            # Simple command response
            response = s.recv(BUFFER_SIZE).decode("utf-8")
            s.close()
            return response
        except Exception as e:
            self.log(f"[-] TCP Error: {e}")
            return None

    def tcp_send_command(self, cmd, data=None):
        """TCP command with automatic retries on connection failure"""
        return self._retry_with_interval(
            self._tcp_send_command_single, "TCP Command", cmd, data
        )

    def list_buckets(self):
        res = self.tcp_send_command("LIST_BUCKETS")
        if res:
            return res.split("\n")
        return []

    def list_files(self):
        res = self.tcp_send_command("LIST")
        if res:
            # Parse "filename (size bytes)"
            files = []
            for line in res.split("\n"):
                if "Storage is empty" in line:
                    continue
                if not line.strip():
                    continue
                # Parsing logic: "test.png (123 bytes)"
                try:
                    name_part, size_part = line.rsplit(" (", 1)
                    size = size_part.split(" ")[0]
                    files.append((name_part, size))
                except ValueError:
                    files.append((line, "?"))
            return files
        return []

    def upload_file(self, filepath):
        if not os.path.exists(filepath):
            self.log(f"[-] File not found: {filepath}")
            return

        filename = os.path.basename(filepath)
        # Sanitize filename: Replace all non-alphanumeric (except . and - and _) with _
        safe_filename = re.sub(r"[^\w\.-]", "_", filename)

        if safe_filename != filename:
            self.log(
                f"[*] Renamed '{filename}' to '{safe_filename}' (special chars removed)"
            )
            filename = safe_filename

        filesize = os.path.getsize(filepath)

        self.log(f"[*] Uploading {filename} ({filesize} bytes)...")

        try:
            with open(filepath, "rb") as f:
                data = f.read()

            res = self.tcp_send_command(f"PUT {filename} {filesize}", data)
            self.log(f"[Server] {res}")
            return True
        except Exception as e:
            self.log(f"[-] Upload Error: {e}")
            return False

    def delete_file(self, filename):
        self.log(f"[*] Deleting {filename}...")
        res = self.tcp_send_command(f"DELETE {filename}")
        self.log(f"[Server] {res}")
        return "SUCCESS" in (res or "")

    def _download_file_tcp_single(self, filename, save_path):
        """Single TCP download attempt (without retries)"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self.server_ip, OBJ_TCP_PORT))
            s.send(f"GET {filename}".encode("utf-8"))

            response = s.recv(BUFFER_SIZE).decode("utf-8")
            if response.startswith("OK"):
                size = int(response.split(" ")[1])
                self.log(f"[+] Server ready. Size: {size} bytes")
                s.send("READY".encode("utf-8"))

                start_time = time.time()
                received = 0
                with open(save_path, "wb") as f:
                    while received < size:
                        chunk = s.recv(min(size - received, BUFFER_SIZE))
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)

                elapsed = time.time() - start_time
                self.log(f"[+] Download complete: {save_path} ({elapsed:.2f}s)")
                s.close()
                return True
            else:
                self.log(f"[-] Server Error: {response}")
                s.close()
                return None
        except Exception as e:
            self.log(f"[-] TCP Download Error: {e}")
            return None

    def download_file_tcp(self, filename, save_path):
        """TCP download with automatic retries on connection failure"""
        self.log(f"[*] GET (TCP): Downloading {filename}...")
        return self._retry_with_interval(
            self._download_file_tcp_single, "TCP Download", filename, save_path
        )

    def _download_file_rudp_single(self, filename, save_path):
        """Single RUDP download attempt (without retries)"""
        try:
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client_socket.settimeout(5.0)
            server_addr = (self.server_ip, OBJ_RUDP_PORT)

            req_data = f"GET {filename}".encode("utf-8")
            syn_packet = rudp_lib.create_packet(
                0, 0, rudp_lib.FLAG_SYN, 64000, req_data
            )
            client_socket.sendto(syn_packet, server_addr)

            expected_seq = 1
            start_time = time.time()

            with open(save_path, "wb") as f:
                while True:
                    try:
                        packet_bytes, _ = client_socket.recvfrom(65535)
                        parsed = rudp_lib.parse_packet(packet_bytes)
                        if parsed is None:
                            continue
                        seq_num, ack_num, flags, _, data = parsed

                        if flags & rudp_lib.FLAG_FIN:
                            ack_packet = rudp_lib.create_packet(
                                0, seq_num, rudp_lib.FLAG_ACK, 64000
                            )
                            client_socket.sendto(ack_packet, server_addr)
                            self.log("[*] Received FIN. Transfer complete.")
                            break

                        if flags & rudp_lib.FLAG_DATA:
                            if seq_num == expected_seq:
                                f.write(data)
                                expected_seq += 1
                                ack_packet = rudp_lib.create_packet(
                                    0, expected_seq, rudp_lib.FLAG_ACK, 64000
                                )
                                client_socket.sendto(ack_packet, server_addr)
                            else:
                                # Simple Go-Back-N or Stop-and-Wait logic simulation (ACK expected)
                                ack_packet = rudp_lib.create_packet(
                                    0, expected_seq, rudp_lib.FLAG_ACK, 64000
                                )
                                client_socket.sendto(ack_packet, server_addr)
                    except socket.timeout:
                        self.log("[-] RUDP Timeout.")
                        break

            elapsed = time.time() - start_time
            self.log(f"[+] RUDP Download complete: {save_path} ({elapsed:.2f}s)")
            client_socket.close()
            return True
        except Exception as e:
            self.log(f"[-] RUDP Download Error: {e}")
            return None

    def download_file_rudp(self, filename, save_path):
        """RUDP download with automatic retries on connection failure"""
        self.log(f"[*] GET (RUDP): Downloading {filename}...")
        return self._retry_with_interval(
            self._download_file_rudp_single, "RUDP Download", filename, save_path
        )

    def _fetch_file_bytes_single(self, filename):
        """Single fetch attempt (without retries)"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self.server_ip, OBJ_TCP_PORT))
            s.send(f"GET {filename}".encode("utf-8"))

            response = s.recv(BUFFER_SIZE).decode("utf-8")
            if response.startswith("OK"):
                size = int(response.split(" ")[1])
                # Safety limit for preview: 2MB
                if size > 2 * 1024 * 1024:
                    s.close()
                    return None

                s.send("READY".encode("utf-8"))

                data = b""
                while len(data) < size:
                    chunk = s.recv(min(size - len(data), BUFFER_SIZE))
                    if not chunk:
                        break
                    data += chunk
                s.close()
                return data
            else:
                s.close()
                return None
        except Exception:
            return None

    def fetch_file_bytes(self, filename):
        """Fetch file bytes with automatic retries on connection failure"""
        # Helper to fetch file content in memory for preview
        return self._retry_with_interval(
            self._fetch_file_bytes_single, "Fetch File Bytes", filename
        )
