import os
import random
import shutil
import socket
import sqlite3
import sys
import threading
import time

# Add common directory to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../common")))

import rudp_lib  # Import our helper library

# --- Constants ---
OBJ_TCP_PORT = 2121
OBJ_RUDP_PORT = 2122
BUFFER_SIZE = 4096
RUDP_CHUNK_SIZE = 1024
STORAGE_DIR = "object_storage"
DB_FILE = "storage.db"
REPLICA_BUCKETS = ["node1", "node2", "node3"]


def setup_storage():
    """Ensures the object storage directory and database exist. Creates replica buckets."""
    if not os.path.exists(STORAGE_DIR):
        os.makedirs(STORAGE_DIR)
        print(f"[*] Created storage root '{STORAGE_DIR}'.")

    # Create the 3 replica buckets
    for bucket in REPLICA_BUCKETS:
        path = os.path.join(STORAGE_DIR, bucket)
        if not os.path.exists(path):
            os.makedirs(path)
            print(f"[*] Created replica bucket: {bucket}")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Metadata for the logical object
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            size INTEGER,
            content_type TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Track physical location (replicas)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS replicas (
            key TEXT,
            bucket TEXT,
            filepath TEXT,
            PRIMARY KEY (key, bucket),
            FOREIGN KEY (key) REFERENCES metadata (key) ON DELETE CASCADE
        )
    """
    )

    conn.commit()
    conn.close()
    print("[*] Database initialized.")


def handle_tcp_client(client_socket):
    """Handles a TCP client with Replication support."""
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Enable foreign keys
        cursor.execute("PRAGMA foreign_keys = ON")

        while True:
            try:
                request = client_socket.recv(BUFFER_SIZE).decode("utf-8").strip()
            except ConnectionResetError:
                break

            if not request:
                break

            print(f"[TCP] Received request: {request}")

            # --- System Info ---
            if request == "LIST_BUCKETS":
                # Show the system buckets
                resp = "\n".join(REPLICA_BUCKETS)
                client_socket.send(resp.encode("utf-8"))

            # --- Object Operations ---
            elif request == "LIST":
                cursor.execute("SELECT key, size FROM metadata")
                objects = cursor.fetchall()
                resp = (
                    "\n".join([f"{o[0]} ({o[1]} bytes)" for o in objects])
                    if objects
                    else "Storage is empty"
                )
                client_socket.send(resp.encode("utf-8"))

            elif request.startswith("GET ") or request.startswith("RETR "):
                # GET <key>
                parts = request.split(" ", 1)
                if len(parts) < 2:
                    client_socket.send("ERROR Usage: GET <key>".encode("utf-8"))
                    continue

                key = parts[1]

                # Check replicas
                cursor.execute("SELECT filepath FROM replicas WHERE key=?", (key,))
                replicas = cursor.fetchall()

                if replicas:
                    # Load balancing / random choice
                    chosen_path = random.choice(replicas)[0]
                    if os.path.exists(chosen_path):
                        filesize = os.path.getsize(chosen_path)
                        client_socket.send(f"OK {filesize}".encode("utf-8"))

                        ready_signal = (
                            client_socket.recv(BUFFER_SIZE).decode("utf-8").strip()
                        )
                        if ready_signal == "READY":
                            with open(chosen_path, "rb") as f:
                                while True:
                                    chunk = f.read(BUFFER_SIZE)
                                    if not chunk:
                                        break
                                    client_socket.send(chunk)
                            print(f"[TCP] Served '{key}' from '{chosen_path}'")
                    else:
                        client_socket.send(
                            "ERROR Replica missing on disk".encode("utf-8")
                        )
                else:
                    client_socket.send("ERROR Object not found".encode("utf-8"))

            elif request.startswith("PUT "):
                # PUT <key> <size>
                parts = request.split(" ")
                if len(parts) < 3:
                    client_socket.send("ERROR Usage: PUT <key> <size>".encode("utf-8"))
                    continue

                key = parts[1]
                filesize = int(parts[2])

                client_socket.send("READY".encode("utf-8"))

                # Receive to a temp staging area first
                temp_path = os.path.join(STORAGE_DIR, f"temp_{key}")
                received = 0
                try:
                    with open(temp_path, "wb") as f:
                        while received < filesize:
                            chunk = client_socket.recv(
                                min(filesize - received, BUFFER_SIZE)
                            )
                            if not chunk:
                                break
                            f.write(chunk)
                            received += len(chunk)

                    # Store Metadata
                    cursor.execute(
                        "INSERT OR REPLACE INTO metadata (key, size) VALUES (?, ?)",
                        (key, filesize),
                    )

                    # Replicate to all buckets
                    # First clear old replicas if overwrite
                    cursor.execute("DELETE FROM replicas WHERE key=?", (key,))

                    for bucket in REPLICA_BUCKETS:
                        dest_path = os.path.join(STORAGE_DIR, bucket, key)
                        shutil.copy2(temp_path, dest_path)
                        cursor.execute(
                            "INSERT INTO replicas (key, bucket, filepath) VALUES (?, ?, ?)",
                            (key, bucket, dest_path),
                        )

                    conn.commit()
                    os.remove(temp_path)  # cleanup temp

                    print(
                        f"[TCP] Stored object '{key}' and replicated to {REPLICA_BUCKETS}"
                    )
                    client_socket.send(
                        "SUCCESS Object stored and replicated".encode("utf-8")
                    )

                except Exception as e:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    client_socket.send(f"ERROR {str(e)}".encode("utf-8"))

            elif request.startswith("DELETE "):
                # DELETE <key>
                parts = request.split(" ", 1)
                if len(parts) < 2:
                    client_socket.send("ERROR Usage: DELETE <key>".encode("utf-8"))
                    continue

                key = parts[1]

                cursor.execute("SELECT filepath FROM replicas WHERE key=?", (key,))
                rows = cursor.fetchall()

                if rows:
                    for row in rows:
                        fpath = row[0]
                        if os.path.exists(fpath):
                            os.remove(fpath)

                    # Delete metadata (cascades to replicas table due to FK)
                    cursor.execute("DELETE FROM metadata WHERE key=?", (key,))
                    conn.commit()

                    print(f"[TCP] Deleted object '{key}' (all replicas).")
                    client_socket.send("SUCCESS Object deleted".encode("utf-8"))
                else:
                    client_socket.send("ERROR Object not found".encode("utf-8"))

            elif request == "QUIT":
                break

    except Exception as e:
        print(f"[-] TCP Client error: {e}")
    finally:
        if conn:
            conn.close()
        client_socket.close()


def start_tcp_server():
    """Starts the TCP Object Storage server."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("0.0.0.0", OBJ_TCP_PORT))
    server_socket.listen(5)
    print(f"[*] Object Storage TCP Server listening on port {OBJ_TCP_PORT}...")

    while True:
        client_sock, addr = server_socket.accept()
        threading.Thread(target=handle_tcp_client, args=(client_sock,)).start()


def start_rudp_server():
    """Starts the RUDP Object Storage server (GET only)."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.bind(("0.0.0.0", OBJ_RUDP_PORT))
    print(f"[*] Object Storage RUDP Server listening on port {OBJ_RUDP_PORT}...")

    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()

    while True:
        try:
            server_socket.settimeout(None)
            packet_bytes, client_addr = server_socket.recvfrom(65535)
            parsed = rudp_lib.parse_packet(packet_bytes)
            if parsed is None:
                continue
            seq, ack, flags, _, data = parsed

            if flags & rudp_lib.FLAG_SYN:
                request = data.decode("utf-8")
                # GET <key>
                if request.startswith("GET "):
                    parts = request.split(" ", 1)
                    if len(parts) >= 2:
                        key = parts[1]

                        cursor.execute(
                            "SELECT filepath FROM replicas WHERE key=?", (key,)
                        )
                        replicas = cursor.fetchall()

                        if replicas:
                            filepath = random.choice(replicas)[0]
                            if os.path.exists(filepath):
                                chunks = []
                                with open(filepath, "rb") as f:
                                    while True:
                                        chunk = f.read(RUDP_CHUNK_SIZE)
                                        if not chunk:
                                            break
                                        chunks.append(chunk)

                                total_packets = len(chunks)
                                print(
                                    f"\n[*] RUDP: Sending '{key}' to {client_addr}. {total_packets} packets."
                                )

                                base = 1
                                next_seq_num = 1
                                window_size = 2.0  # Use float for congestion control
                                slow_start_threshold = 16
                                dup_ack_count = 0
                                last_ack_recvd = 0
                                client_rwnd = 1000  # Initial assumption

                                server_socket.settimeout(0.5)

                                while base <= total_packets:
                                    # Explicit Flow Control using received window size
                                    effective_window = min(
                                        int(window_size), client_rwnd
                                    )

                                    while (
                                        next_seq_num < base + effective_window
                                        and next_seq_num <= total_packets
                                    ):
                                        chunk_data = chunks[next_seq_num - 1]
                                        packet = rudp_lib.create_packet(
                                            next_seq_num,
                                            0,
                                            rudp_lib.FLAG_DATA,
                                            64000,  # My receive window
                                            chunk_data,
                                        )

                                        if random.randint(1, 100) < 5:
                                            print(
                                                f"  [X] LOSS: Dropped {next_seq_num}."
                                            )
                                        else:
                                            server_socket.sendto(packet, client_addr)
                                            print(f"  [>] Sent {next_seq_num}")
                                        next_seq_num += 1

                                    try:
                                        ack_bytes, recv_addr = server_socket.recvfrom(
                                            65535
                                        )
                                        if recv_addr != client_addr:
                                            continue

                                        parsed = rudp_lib.parse_packet(ack_bytes)

                                        # Checksum verification handling
                                        if parsed is None:
                                            print("  [!] Corrupted ACK. Iconsoring.")
                                            continue

                                        _, ack_num, ack_flags, r_window, _ = parsed

                                        # Update client flow window
                                        # If window=0, we should treat it carefully (persist timer), but here just min(1, ..)
                                        client_rwnd = max(1, r_window)

                                        if ack_flags & rudp_lib.FLAG_ACK:
                                            if ack_num > base:
                                                base = ack_num
                                                dup_ack_count = 0
                                                last_ack_recvd = ack_num

                                                # Congestion Control
                                                if window_size < slow_start_threshold:
                                                    window_size += 1
                                                else:
                                                    window_size += 1.0 / window_size

                                            elif ack_num == last_ack_recvd:
                                                dup_ack_count += 1
                                                if dup_ack_count == 3:
                                                    # Fast Retransmit
                                                    print(
                                                        f"  [!] Fast Retransmit: Resending {base}"
                                                    )
                                                    # Resend the packet that is missing (base)
                                                    if base <= total_packets:
                                                        chunk_data = chunks[base - 1]
                                                        packet = rudp_lib.create_packet(
                                                            base,
                                                            0,
                                                            rudp_lib.FLAG_DATA,
                                                            64000,
                                                            chunk_data,
                                                        )
                                                        server_socket.sendto(
                                                            packet, client_addr
                                                        )

                                                    # Fast Recovery adjustment
                                                    slow_start_threshold = max(
                                                        int(window_size) // 2, 2
                                                    )
                                                    window_size = slow_start_threshold
                                                    # Do not reset next_seq_num fully, just retransmit missing

                                    except socket.timeout:
                                        print(f"  [!] Timeout! Resending from {base}.")
                                        slow_start_threshold = max(
                                            int(window_size) // 2, 2
                                        )
                                        window_size = 1
                                        next_seq_num = base
                                        dup_ack_count = 0

                                print("[*] RUDP: Transfer complete. Sending FIN.")
                                fin_packet = rudp_lib.create_packet(
                                    0, 0, rudp_lib.FLAG_FIN, 0
                                )
                                server_socket.sendto(fin_packet, client_addr)
                            else:
                                print(f"[-] RUDP: File error on disk.")
                        else:
                            print(f"[-] RUDP: Object '{key}' not found.")
                    else:
                        print(f"[-] RUDP: Invalid request format: {request}")

        except Exception as e:
            print(f"[-] RUDP Server error: {e}")


if __name__ == "__main__":
    setup_storage()

    tcp_thread = threading.Thread(target=start_tcp_server)
    tcp_thread.daemon = True
    tcp_thread.start()

    rudp_thread = threading.Thread(target=start_rudp_server)
    rudp_thread.daemon = True
    rudp_thread.start()

    print("\n[V] Replicated Object Storage Server is running.")
    print("Press Ctrl+C to exit.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[-] Shutting down servers.")
