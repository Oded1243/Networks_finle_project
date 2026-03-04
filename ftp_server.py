import os
import random
import socket
import threading

import rudp_lib  # Import our helper library

# --- Constants to avoid magic numbers --- (This code was created/improved with AI)
FTP_TCP_PORT = 2121
FTP_RUDP_PORT = 2122
BUFFER_SIZE = 4096
RUDP_CHUNK_SIZE = 1024  # Divide the file into small chunks to see the window move
SERVER_DIR = "server_files"  # Relative path


def setup_server_directory():
    """Ensures the server directory exists, and creates it if it doesn't."""
    if not os.path.exists(SERVER_DIR):
        os.makedirs(SERVER_DIR)
        with open(os.path.join(SERVER_DIR, "test_file.txt"), "w") as f:
            f.write("Balex fag\n" * 50)
        print(f"[*] Created directory '{SERVER_DIR}' with a dummy file.")


def handle_tcp_client(client_socket):
    """Handles a regular TCP client (LIST and RETR)."""
    try:
        while True:
            request = client_socket.recv(BUFFER_SIZE).decode("utf-8").strip()
            if not request:
                break

            if request == "LIST":
                files = os.listdir(SERVER_DIR)
                files_str = "\n".join(files) if files else "Empty directory"
                client_socket.send(files_str.encode("utf-8"))

            elif request.startswith("RETR "):
                filename = request.split(" ")[1]
                filepath = os.path.join(SERVER_DIR, filename)

                if os.path.exists(filepath):
                    filesize = os.path.getsize(filepath)
                    client_socket.send(f"OK {filesize}".encode("utf-8"))
                    client_socket.recv(BUFFER_SIZE)  # Receives READY

                    with open(filepath, "rb") as f:
                        bytes_read = f.read(BUFFER_SIZE)
                        while bytes_read:
                            client_socket.send(bytes_read)
                            bytes_read = f.read(BUFFER_SIZE)
                else:
                    client_socket.send("ERROR File not found".encode("utf-8"))
            elif request == "QUIT":
                break
    except Exception as e:
        print(f"[-] TCP Client error: {e}")
    finally:
        client_socket.close()


def start_tcp_server():
    """Starts the TCP server listening."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("0.0.0.0", FTP_TCP_PORT))
    server_socket.listen(5)
    print(f"[*] TCP Server listening on port {FTP_TCP_PORT}...")

    while True:
        client_sock, addr = server_socket.accept()
        # Opens a new thread for each TCP client
        threading.Thread(target=handle_tcp_client, args=(client_sock,)).start()


def start_rudp_server():
    """
    Starts the RUDP server.
    Implements dynamic window mechanism (Congestion Control) and reliability (Go-Back-N).
    (This function was written with AI to meet final project requirements)
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.bind(("0.0.0.0", FTP_RUDP_PORT))
    print(f"[*] RUDP Server listening on port {FTP_RUDP_PORT}...")

    while True:
        # 1. Waiting for connection request from client (SYN)
        try:
            # Clear timeout to wait for a new client
            server_socket.settimeout(None)
            packet_bytes, client_addr = server_socket.recvfrom(65535)
            seq, ack, flags, data = rudp_lib.parse_packet(packet_bytes)

            if flags & rudp_lib.FLAG_SYN:
                request = data.decode("utf-8")
                if request.startswith("RETR "):
                    filename = request.replace("RETR ", "")
                    filepath = os.path.join(SERVER_DIR, filename)

                    if not os.path.exists(filepath):
                        print(f"[-] RUDP: File '{filename}' not found.")
                        continue

                    # Reading the file and dividing it into small packets
                    chunks = []
                    with open(filepath, "rb") as f:
                        while True:
                            chunk = f.read(RUDP_CHUNK_SIZE)
                            if not chunk:
                                break
                            chunks.append(chunk)

                    total_packets = len(chunks)
                    print(
                        f"\n[*] RUDP: Starting to send '{filename}' to {client_addr}. Total packets: {total_packets}"
                    )

                    # --- Dynamic window variables (Sliding Window & Congestion Control) ---
                    base = 1
                    next_seq_num = 1
                    window_size = 2  # Initial window size
                    max_window_size = 10  # Maximum window size

                    # 2. Sending loop (Go-Back-N)
                    while base <= total_packets:
                        # Send packets as long as we're within the allowed window
                        while (
                            next_seq_num < base + window_size
                            and next_seq_num <= total_packets
                        ):
                            chunk_data = chunks[
                                next_seq_num - 1
                            ]  # Array index is 1 less than the sequence number
                            packet = rudp_lib.create_packet(
                                next_seq_num, 0, rudp_lib.FLAG_DATA, chunk_data
                            )
                            # --- Mechanism for creating intentional errors for testing (20% loss) ---
                            if random.randint(1, 100) < 20:
                                print(
                                    f"  [X] SIMULATING PACKET LOSS: Dropped packet {next_seq_num} intentionally."
                                )
                            else:
                                server_socket.sendto(packet, client_addr)
                                print(f"  [>] Sent packet {next_seq_num}")
                            next_seq_num += 1

                        # 3. Waiting for confirmation (ACK)
                        server_socket.settimeout(
                            0.5
                        )  # Setting expiration time (Timeout) for reliability
                        try:
                            ack_bytes, _ = server_socket.recvfrom(65535)
                            _, ack_num, ack_flags, _ = rudp_lib.parse_packet(ack_bytes)

                            if ack_flags & rudp_lib.FLAG_ACK:
                                if ack_num > base:
                                    print(
                                        f"  [<] Received ACK {ack_num}. Moving window forward."
                                    )
                                    base = ack_num

                                    # Congestion Control: increasing the window (Additive Increase)
                                    if window_size < max_window_size:
                                        window_size += 1

                        except socket.timeout:
                            # 4. Timeout handling (Go-Back-N)
                            print(
                                f"  [!] Timeout! Resending from {base}. Decreasing window size."
                            )
                            # Congestion Control: reducing the window by half (Multiplicative Decrease)
                            window_size = max(1, window_size // 2)
                            next_seq_num = (
                                base  # Move the send pointer back to retransmit
                            )

                    # 5. Ending: Sending FIN flag
                    print("[*] RUDP: File sent completely. Sending FIN.")
                    fin_packet = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_FIN)
                    server_socket.sendto(fin_packet, client_addr)

        except Exception as e:
            print(f"[-] RUDP Server error: {e}")


if __name__ == "__main__":
    setup_server_directory()

    # Using Threading to run TCP and RUDP in parallel
    tcp_thread = threading.Thread(target=start_tcp_server)
    tcp_thread.daemon = True  # Terminates the thread when the main program ends
    tcp_thread.start()

    rudp_thread = threading.Thread(target=start_rudp_server)
    rudp_thread.daemon = True
    rudp_thread.start()

    print("\n[V] Both FTP (TCP) and RUDP servers are running in parallel.")
    print("Press Ctrl+C to exit.\n")

    # Keep the main program running
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print("\n[-] Shutting down servers.")
