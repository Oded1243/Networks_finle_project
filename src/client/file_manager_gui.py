import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import socket
import sys
import os
import threading
import struct
import time
import random
import io
import re

# Add common directory to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../common")))

try:
    from dnslib import DNSRecord
    import rudp_lib
    from PIL import Image, ImageTk, ImageDraw
except ImportError as e:
    messagebox.showerror(
        "Dependency Error",
        f"Failed to import required modules: {e}\nPlease ensure 'dnslib' and 'Pillow' are installed.",
    )
    sys.exit(1)

# --- Constants (Copied from client.py) ---
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


class NetworkClient:
    def __init__(self, log_callback):
        self.log = log_callback
        self.server_ip = None
        self.my_ip = None
        self.connected = False

    def _create_dhcp_request(self, xid, mac_bytes, message_type):
        header = struct.pack("!BBBB", OP_BOOTREQUEST, HTYPE_ETHERNET, HLEN_MAC, 0)
        xid_secs_flags = struct.pack("!IHH", xid, 0, 0x8000)
        zero_ip = socket.inet_aton("0.0.0.0")
        ips = struct.pack("!4s4s4s4s", zero_ip, zero_ip, zero_ip, zero_ip)
        chaddr = mac_bytes + b"\x00" * 10
        sname_file_cookie = b"\x00" * 192 + struct.pack("!I", 0x63825363)
        options = struct.pack("!BBB", OPT_MESSAGE_TYPE, 1, message_type)
        options += struct.pack("!B", OPT_END)
        return header + xid_secs_flags + ips + chaddr + sname_file_cookie + options

    def perform_dhcp_handshake(self):
        self.log("--- Phase 1: DHCP Handshake ---")
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
                offered_ip = socket.inet_ntoa(offer_data[16:20])
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

    def resolve_domain(self, domain="object.store"):
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

    def connect_sequence(self):
        ip = self.perform_dhcp_handshake()
        if not ip:
            return False

        server_ip = self.resolve_domain()
        if not server_ip:
            return False

        self.connected = True
        return True

    def tcp_send_command(self, cmd, data=None):
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

            # Handle large responses (like file lists) if necessary,
            # but current server implementation just sends everything in one go mostly,
            # or we should loop recv. The server uses one send for lists.
            # But specific commands like GET are different.

            s.close()
            return response
        except Exception as e:
            self.log(f"[-] TCP Error: {e}")
            return None

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
        # This regex \w matches [a-zA-Z0-9_] and unicode alphanumerics (e.g. Hebrew)
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

    def download_file_tcp(self, filename, save_path):
        self.log(f"[*] GET (TCP): Downloading {filename}...")
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
            else:
                self.log(f"[-] Server Error: {response}")
            s.close()
        except Exception as e:
            self.log(f"[-] TCP Download Error: {e}")

    def download_file_rudp(self, filename, save_path):
        self.log(f"[*] GET (RUDP): Downloading {filename}...")
        try:
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client_socket.settimeout(5.0)
            server_addr = (self.server_ip, OBJ_RUDP_PORT)

            req_data = f"GET {filename}".encode("utf-8")
            syn_packet = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_SYN, req_data)
            client_socket.sendto(syn_packet, server_addr)

            expected_seq = 1
            start_time = time.time()

            with open(save_path, "wb") as f:
                while True:
                    try:
                        packet_bytes, _ = client_socket.recvfrom(65535)
                        seq_num, ack_num, flags, data = rudp_lib.parse_packet(
                            packet_bytes
                        )

                        if flags & rudp_lib.FLAG_FIN:
                            ack_packet = rudp_lib.create_packet(
                                0, seq_num, rudp_lib.FLAG_ACK
                            )
                            client_socket.sendto(ack_packet, server_addr)
                            self.log("[*] Received FIN. Transfer complete.")
                            break

                        if flags & rudp_lib.FLAG_DATA:
                            if seq_num == expected_seq:
                                f.write(data)
                                expected_seq += 1
                                ack_packet = rudp_lib.create_packet(
                                    0, expected_seq, rudp_lib.FLAG_ACK
                                )
                                client_socket.sendto(ack_packet, server_addr)
                            else:
                                # Simple Go-Back-N or Stop-and-Wait logic simulation (ACK expected)
                                ack_packet = rudp_lib.create_packet(
                                    0, expected_seq, rudp_lib.FLAG_ACK
                                )
                                client_socket.sendto(ack_packet, server_addr)
                    except socket.timeout:
                        self.log("[-] RUDP Timeout.")
                        break

            elapsed = time.time() - start_time
            self.log(f"[+] RUDP Download complete: {save_path} ({elapsed:.2f}s)")

        except Exception as e:
            self.log(f"[-] RUDP Download Error: {e}")

    def fetch_file_bytes(self, filename):
        # Helper to fetch file content in memory for preview
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


class StorageGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Object Storage Manager")
        self.root.geometry("850x600")

        self.client = NetworkClient(self.log_message)

        # --- Modern Clean Theme ---
        style = ttk.Style()
        style.theme_use("clam")

        # Define clean colors
        bg_color = "#f5f5f5"
        fg_color = "#333333"
        panel_bg = "#ffffff"
        accent_color = "#0078d7"  # Standard Windows Blue

        self.root.configure(bg=bg_color)

        # General Styles
        style.configure("TFrame", background=bg_color)
        style.configure(
            "TLabelframe",
            background=bg_color,
            foreground=fg_color,
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background=bg_color,
            foreground="#555555",
            font=("Segoe UI", 10, "bold"),
        )

        # Button Style
        style.configure(
            "TButton",
            font=("Segoe UI", 10),
            borderwidth=0,
            background=panel_bg,
            foreground=fg_color,
        )
        style.map(
            "TButton",
            background=[("active", "#e1e1e1")],
            foreground=[("active", "#000000")],
        )

        # Label Style
        style.configure(
            "TLabel", background=bg_color, foreground=fg_color, font=("Segoe UI", 10)
        )

        # Treeview (File List) Style
        style.configure(
            "Treeview",
            background=panel_bg,
            fieldbackground=panel_bg,
            foreground=fg_color,
            font=("Segoe UI", 10),
            rowheight=25,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#e1e1e1",
            foreground="#333333",
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Treeview",
            background=[("selected", accent_color)],
            foreground=[("selected", "white")],
        )

        # --- Top Bar (Header) ---
        top_frame = ttk.Frame(root, padding=(20, 20, 20, 10))
        top_frame.pack(fill=tk.X)

        # Title
        title_lbl = ttk.Label(
            top_frame,
            text="Object Storage Client",
            font=("Segoe UI", 18, "bold"),
            foreground="#333333",
        )
        title_lbl.pack(side=tk.TOP, anchor="w", pady=(0, 5))

        # Status & Connect
        status_frame = ttk.Frame(top_frame)
        status_frame.pack(fill=tk.X, pady=5)

        self.lbl_status = ttk.Label(
            status_frame,
            text="● Disconnected",
            foreground="#dc3545",
            font=("Segoe UI", 10),
        )
        self.lbl_status.pack(side=tk.LEFT, padx=(0, 15))

        self.btn_connect = ttk.Button(
            status_frame, text="Connect to Network", command=self.start_connection
        )
        self.btn_connect.pack(side=tk.LEFT)

        # --- Icons ---
        self.icons = {}

        def simple_icon(color):
            img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            # Draw a document shape: main body
            draw.polygon(
                [(3, 1), (9, 1), (13, 5), (13, 14), (3, 14)],
                fill=color,
                outline="#666666",
            )
            # Fold corner
            draw.polygon([(9, 1), (9, 5), (13, 5)], fill="#FFFFFF80")
            return ImageTk.PhotoImage(img)

        self.icons["image"] = simple_icon("#9C27B0")  # Purple
        self.icons["text"] = simple_icon("#2196F3")  # Blue
        self.icons["code"] = simple_icon("#FF9800")  # Orange
        self.icons["archive"] = simple_icon("#F44336")  # Red
        self.icons["default"] = simple_icon("#9E9E9E")  # Gray

        self.ext_map = {
            "png": "image",
            "jpg": "image",
            "jpeg": "image",
            "gif": "image",
            "bmp": "image",
            "webp": "image",
            "txt": "text",
            "md": "text",
            "log": "text",
            "csv": "text",
            "py": "code",
            "js": "code",
            "html": "code",
            "css": "code",
            "json": "code",
            "xml": "code",
            "java": "code",
            "c": "code",
            "cpp": "code",
            "zip": "archive",
            "tar": "archive",
            "gz": "archive",
            "rar": "archive",
            "7z": "archive",
        }

        # --- Middle Area ---
        content_frame = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))

        # Files Area (Left Pane)
        left_pane = ttk.LabelFrame(content_frame, text="Files", padding=10)
        content_frame.add(left_pane, weight=2)

        # Updated columns: Filename is in the tree column (#0) with icon
        columns = ("Size",)
        self.tree_files = ttk.Treeview(
            left_pane, columns=columns, show="tree headings", style="Treeview"
        )
        self.tree_files.heading("#0", text="Name", anchor="w")
        self.tree_files.heading("Size", text="Size", anchor="e")
        self.tree_files.column("#0", width=300)
        self.tree_files.column("Size", width=80, anchor="e")

        self.tree_files.bind("<<TreeviewSelect>>", self.on_file_select)

        scrollbar = ttk.Scrollbar(
            left_pane, orient=tk.VERTICAL, command=self.tree_files.yview
        )
        self.tree_files.configure(yscroll=scrollbar.set)

        self.tree_files.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Refresh Button (files pane)
        btn_refresh = ttk.Button(
            left_pane, text="↻ Refresh List", command=self.refresh_lists
        )
        btn_refresh.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))

        # Preview Area (Right Pane)
        right_pane = ttk.LabelFrame(content_frame, text="Preview", padding=10)
        content_frame.add(right_pane, weight=1)

        self.lbl_preview_info = ttk.Label(
            right_pane, text="Select a file to preview", anchor="center"
        )
        self.lbl_preview_info.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))

        self.preview_container = ttk.Frame(right_pane)
        self.preview_container.pack(fill=tk.BOTH, expand=True)

        self.lbl_image_preview = tk.Label(self.preview_container, bg="white")
        self.txt_text_preview = tk.Text(
            self.preview_container, wrap="none", state="disabled", font=("Consolas", 8)
        )

        # --- Actions Bar ---
        action_frame = ttk.Frame(root, padding=(20, 10))
        action_frame.pack(fill=tk.X)

        # Styled Buttons
        ttk.Button(action_frame, text="⬆ Upload", command=self.on_upload).pack(
            side=tk.LEFT, padx=(0, 10)
        )
        ttk.Button(
            action_frame,
            text="⬇ Download (TCP)",
            command=lambda: self.on_download("TCP"),
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(
            action_frame,
            text="⬇ Download (RUDP)",
            command=lambda: self.on_download("RUDP"),
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(action_frame, text="🗑 Delete", command=self.on_delete).pack(
            side=tk.RIGHT
        )

        # --- Log Console ---
        log_frame = ttk.Frame(root, padding=(20, 0, 20, 20))
        log_frame.pack(fill=tk.X)

        lbl_log = ttk.Label(
            log_frame,
            text="Activity Log",
            font=("Segoe UI", 9, "bold"),
            foreground="#666666",
        )
        lbl_log.pack(anchor="w", pady=(5, 2))

        self.txt_log = scrolledtext.ScrolledText(
            log_frame,
            height=6,
            state="disabled",
            font=("Consolas", 9),
            bg="#ffffff",
            fg="#333333",
            relief="flat",
            borderwidth=1,
        )
        self.txt_log.pack(fill=tk.X)

        # Add a subtle border to log
        log_border = tk.Frame(log_frame, background="#cccccc")
        self.txt_log.pack(in_=log_border, padx=1, pady=1, fill=tk.X)
        log_border.pack(fill=tk.X)

    def log_message(self, msg):
        self.txt_log.config(state="normal")
        self.txt_log.insert(tk.END, msg + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state="disabled")
        print(msg)  # verify in terminal too

    def start_connection(self):
        self.btn_connect.config(state="disabled")
        self.lbl_status.config(text="Status: Connecting...", foreground="orange")
        threading.Thread(target=self._connect_thread, daemon=True).start()

    def _connect_thread(self):
        success = self.client.connect_sequence()
        if success:
            self.root.after(0, lambda: self._on_connect_success())
        else:
            self.root.after(0, lambda: self._on_connect_fail())

    def _on_connect_success(self):
        self.lbl_status.config(
            text=f"● Connected ({self.client.server_ip})", foreground="#28a745"
        )
        self.btn_connect.config(state="normal", text="Reconnect")
        self.refresh_lists()

    def _on_connect_fail(self):
        self.lbl_status.config(text="● Connection Failed", foreground="#dc3545")
        self.btn_connect.config(state="normal")
        messagebox.showerror(
            "Connection Error",
            "Could not connect to DHCP or DNS server.\nEnsure servers are running.",
        )

    def refresh_lists(self):
        threading.Thread(target=self._refresh_thread, daemon=True).start()

    def _refresh_thread(self):
        if not self.client.connected:
            return

        files = self.client.list_files()

        self.root.after(0, lambda: self._update_lists(files))

    def _update_lists(self, files):
        for item in self.tree_files.get_children():
            self.tree_files.delete(item)

        for name, size in files:
            self.tree_files.insert("", tk.END, values=(name, size))

    def on_file_select(self, event):
        selected = self.tree_files.selection()
        if not selected:
            return

        # Changed to fetch from 'text' (tree column) due to icon support
        filename = self.tree_files.item(selected[0])["text"]
        # Clear previous preview
        self.lbl_image_preview.pack_forget()
        self.txt_text_preview.pack_forget()
        self.lbl_preview_info.config(text=f"Loading preview for {filename}...")

        # Debounce/Async load
        threading.Thread(
            target=lambda: self._load_preview(filename), daemon=True
        ).start()

    def _load_preview(self, filename):
        data = self.client.fetch_file_bytes(filename)
        self.root.after(0, lambda: self._show_preview_data(filename, data))

    def _show_preview_data(self, filename, data):
        if data is None:
            self.lbl_preview_info.config(
                text="Preview not available (Too large or Error)"
            )
            return

        self.lbl_preview_info.config(text=f"Preview: {filename}")

        # Try as Image (using Pillow for support of JPG, PNG, etc.)
        try:
            image_stream = io.BytesIO(data)
            pil_image = Image.open(image_stream)

            # Resize for preview if needed
            pil_image.thumbnail((300, 300))

            img = ImageTk.PhotoImage(pil_image)
            self.lbl_image_preview.config(image=img)
            self.lbl_image_preview.image = img  # Keep reference
            self.lbl_image_preview.pack(fill=tk.BOTH, expand=True)
            return
        except Exception:
            # Not an image or error loading, try as text
            pass

        # Try as Text
        try:
            text_content = data.decode("utf-8")
            # Limit text length
            if len(text_content) > 2000:
                text_content = text_content[:2000] + "\n...[Truncated]"

            self.txt_text_preview.config(state="normal")
            self.txt_text_preview.delete("1.0", tk.END)
            self.txt_text_preview.insert("1.0", text_content)
            self.txt_text_preview.config(state="disabled")
            self.txt_text_preview.pack(fill=tk.BOTH, expand=True)
            return
        except UnicodeDecodeError:
            self.lbl_preview_info.config(text="Binary file (No preview)")

    def _refresh_thread(self):
        if not self.client.connected:
            return

        files = self.client.list_files()

        self.root.after(0, lambda: self._update_lists(files))

    def _update_lists(self, files):
        for item in self.tree_files.get_children():
            self.tree_files.delete(item)

        for name, size in files:
            # Determine icon based on extension
            ext = name.split(".")[-1].lower() if "." in name else ""
            # Generic mapping logic if needed beyond direct map
            if ext in ["png", "jpg", "jpeg", "gif", "bmp"]:
                icon_type = "image"
            elif ext in ["txt", "md", "log", "csv"]:
                icon_type = "text"
            elif ext in ["py", "js", "html", "css", "json", "xml", "java", "c", "cpp"]:
                icon_type = "code"
            elif ext in ["zip", "tar", "gz", "rar", "7z"]:
                icon_type = "archive"
            else:
                icon_type = "default"

            # Use pre-generated icons
            icon = self.icons.get(icon_type, self.icons["default"])

            # Insert: text=name goes to tree column (#0), image goes to #0, values go to other columns
            self.tree_files.insert("", tk.END, text=name, image=icon, values=(size,))

    def on_upload(self):
        path = filedialog.askopenfilename()
        if path:
            threading.Thread(
                target=lambda: self._upload_thread(path), daemon=True
            ).start()

    def _upload_thread(self, path):
        if self.client.upload_file(path):
            self.refresh_lists()

    def on_delete(self):
        selected = self.tree_files.selection()
        if not selected:
            messagebox.showwarning("Select File", "Please select a file to delete.")
            return

        filename = self.tree_files.item(selected[0])["text"]
        if messagebox.askyesno("Confirm Delete", f"Delete {filename}?"):
            threading.Thread(
                target=lambda: self.client.delete_file(filename)
                and self.refresh_lists(),
                daemon=True,
            ).start()

    def on_download(self, protocol):
        selected = self.tree_files.selection()
        if not selected:
            messagebox.showwarning("Select File", "Please select a file to download.")
            return

        filename = self.tree_files.item(selected[0])["text"]
        save_path = filedialog.asksaveasfilename(initialfile=filename)

        if save_path:
            if protocol == "TCP":
                threading.Thread(
                    target=lambda: self.client.download_file_tcp(filename, save_path),
                    daemon=True,
                ).start()
            else:
                threading.Thread(
                    target=lambda: self.client.download_file_rudp(filename, save_path),
                    daemon=True,
                ).start()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--no-gui":
        print("This script is designed to run with a GUI.")
        sys.exit(0)

    root = tk.Tk()
    app = StorageGUI(root)
    root.mainloop()
