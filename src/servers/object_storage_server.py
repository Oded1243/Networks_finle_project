import os
import random
import shutil
import socket
import sqlite3
import sys
import threading
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../common")))

import rudp_lib

OBJ_TCP_PORT = 2121
OBJ_RUDP_PORT = 2122
BUFFER_SIZE = 4096
RUDP_CHUNK_SIZE = 1024
STORAGE_DIR = "object_storage"
DB_FILE = "storage.db"
REPLICA_BUCKETS = ["node1", "node2", "node3"]

# ---------------------------------------------------------------------------
# Server-side fault injection state (controlled via TEST_RUDP commands)
# ---------------------------------------------------------------------------
_rudp_test_lock = threading.Lock()
_rudp_test_config = {
    "loss_percent": 0,
    "delay_ms": 0,
    "drop_seqs": [],
    "delay_seqs": [],
    "corrupt_seqs": [],
}


def _rudp_should_drop(seq: int) -> bool:
    with _rudp_test_lock:
        if seq in _rudp_test_config["drop_seqs"]:
            return True
        lp = _rudp_test_config["loss_percent"]
    if lp and random.randint(1, 100) <= lp:
        return True
    return False


def _rudp_delay(seq: int):
    with _rudp_test_lock:
        base_ms = _rudp_test_config["delay_ms"]
        extra = seq in _rudp_test_config["delay_seqs"]
    delay_sec = base_ms / 1000.0
    if extra:
        delay_sec += 0.3  # 300 ms extra for targeted seqs
    if delay_sec > 0:
        time.sleep(delay_sec)


def _rudp_should_corrupt(seq: int) -> bool:
    with _rudp_test_lock:
        return seq in _rudp_test_config["corrupt_seqs"]


def _handle_test_rudp(request: str) -> str:
    """Process TEST_RUDP commands and return a response string."""
    parts = request.split()
    if len(parts) < 2:
        return "ERROR Usage: TEST_RUDP RESET|SHOW|CONFIG ..."

    sub = parts[1].upper()

    if sub == "RESET":
        with _rudp_test_lock:
            _rudp_test_config["loss_percent"] = 0
            _rudp_test_config["delay_ms"] = 0
            _rudp_test_config["drop_seqs"] = []
            _rudp_test_config["delay_seqs"] = []
            _rudp_test_config["corrupt_seqs"] = []
        return "OK RESET"

    if sub == "SHOW":
        with _rudp_test_lock:
            return f"OK {_rudp_test_config}"

    if sub == "CONFIG":
        with _rudp_test_lock:
            for token in parts[2:]:
                if "=" not in token:
                    continue
                key, val = token.split("=", 1)
                key = key.upper()
                if key == "LOSS_PERCENT":
                    _rudp_test_config["loss_percent"] = int(val)
                elif key == "DELAY_MS":
                    _rudp_test_config["delay_ms"] = int(val)
                elif key == "DROP_SEQS":
                    _rudp_test_config["drop_seqs"] = [
                        int(x) for x in val.split(",") if x
                    ]
                elif key == "DELAY_SEQS":
                    _rudp_test_config["delay_seqs"] = [
                        int(x) for x in val.split(",") if x
                    ]
                elif key == "CORRUPT_SEQS":
                    _rudp_test_config["corrupt_seqs"] = [
                        int(x) for x in val.split(",") if x
                    ]
        return "OK CONFIG"

    return "ERROR Unknown TEST_RUDP sub-command"


def setup_storage():
    """Ensures the object storage directory and database exist. Creates replica buckets."""
    if not os.path.exists(STORAGE_DIR):
        os.makedirs(STORAGE_DIR)
        print(f"[*] Created storage root '{STORAGE_DIR}'.")

    for bucket in REPLICA_BUCKETS:
        path = os.path.join(STORAGE_DIR, bucket)
        if not os.path.exists(path):
            os.makedirs(path)
            print(f"[*] Created replica bucket: {bucket}")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

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
        cursor.execute("PRAGMA foreign_keys = ON")

        while True:
            try:
                request = client_socket.recv(BUFFER_SIZE).decode("utf-8").strip()
            except ConnectionResetError:
                break

            if not request:
                break

            print(f"[TCP] Received request: {request}")

            if request == "LIST_BUCKETS":
                resp = "\n".join(REPLICA_BUCKETS)
                client_socket.send(resp.encode("utf-8"))

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
                parts = request.split(" ", 1)
                if len(parts) < 2:
                    client_socket.send("ERROR Usage: GET <key>".encode("utf-8"))
                    continue

                key = parts[1]

                cursor.execute("SELECT filepath FROM replicas WHERE key=?", (key,))
                replicas = cursor.fetchall()

                if replicas:
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
                parts = request.split(" ")
                if len(parts) < 3:
                    client_socket.send("ERROR Usage: PUT <key> <size>".encode("utf-8"))
                    continue

                key = parts[1]
                filesize = int(parts[2])

                client_socket.send("READY".encode("utf-8"))

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

                    cursor.execute(
                        "INSERT OR REPLACE INTO metadata (key, size) VALUES (?, ?)",
                        (key, filesize),
                    )

                    cursor.execute("DELETE FROM replicas WHERE key=?", (key,))

                    for bucket in REPLICA_BUCKETS:
                        dest_path = os.path.join(STORAGE_DIR, bucket, key)
                        shutil.copy2(temp_path, dest_path)
                        cursor.execute(
                            "INSERT INTO replicas (key, bucket, filepath) VALUES (?, ?, ?)",
                            (key, bucket, dest_path),
                        )

                    conn.commit()
                    os.remove(temp_path)

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

                    cursor.execute("DELETE FROM metadata WHERE key=?", (key,))
                    conn.commit()

                    print(f"[TCP] Deleted object '{key}' (all replicas).")
                    client_socket.send("SUCCESS Object deleted".encode("utf-8"))
                else:
                    client_socket.send("ERROR Object not found".encode("utf-8"))

            elif request.startswith("TEST_RUDP"):
                resp = _handle_test_rudp(request)
                client_socket.send(resp.encode("utf-8"))

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
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("0.0.0.0", OBJ_TCP_PORT))
    server_socket.listen(5)
    print(f"[*] Object Storage TCP Server listening on port {OBJ_TCP_PORT}...")

    while True:
        client_sock, addr = server_socket.accept()
        threading.Thread(target=handle_tcp_client, args=(client_sock,)).start()


def start_rudp_server():
    """Starts the RUDP Object Storage server (GET only)."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
                                window_size = 2.0
                                slow_start_threshold = 16
                                dup_ack_count = 0
                                last_ack_recvd = 1
                                client_rwnd = 1000

                                server_socket.settimeout(0.5)

                                while base <= total_packets:
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

                                        # ---- fault injection (TEST_RUDP) ----
                                        if _rudp_should_drop(next_seq_num):
                                            print(
                                                f"  [X] TEST DROP: Dropped {next_seq_num}."
                                            )
                                        elif _rudp_should_corrupt(next_seq_num):
                                            ba = bytearray(packet)
                                            if len(ba) > rudp_lib.HEADER_SIZE + 1:
                                                ba[rudp_lib.HEADER_SIZE] ^= 0xFF
                                            server_socket.sendto(bytes(ba), client_addr)
                                            print(f"  [!] TEST CORRUPT: {next_seq_num}")
                                        else:
                                            _rudp_delay(next_seq_num)
                                            # original random 5% loss kept as fallback
                                            if random.randint(1, 100) < 5:
                                                print(
                                                    f"  [X] LOSS: Dropped {next_seq_num}."
                                                )
                                            else:
                                                server_socket.sendto(
                                                    packet, client_addr
                                                )
                                            print(f"  [>] Sent {next_seq_num}")
                                        next_seq_num += 1

                                    try:
                                        ack_bytes, recv_addr = server_socket.recvfrom(
                                            65535
                                        )
                                        if recv_addr != client_addr:
                                            continue

                                        parsed = rudp_lib.parse_packet(ack_bytes)

                                        if parsed is None:
                                            print("  [!] Corrupted ACK. Ignoring.")
                                            continue

                                        _, ack_num, ack_flags, r_window, _ = parsed

                                        client_rwnd = max(1, r_window)

                                        if ack_flags & rudp_lib.FLAG_ACK:
                                            if ack_num > base:
                                                base = ack_num
                                                dup_ack_count = 0
                                                last_ack_recvd = ack_num

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

                                                    slow_start_threshold = max(
                                                        int(window_size) // 2, 2
                                                    )
                                                    window_size = slow_start_threshold

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
                            fin_packet = rudp_lib.create_packet(
                                0, 0, rudp_lib.FLAG_FIN, 0
                            )
                            server_socket.sendto(fin_packet, client_addr)
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
