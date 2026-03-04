import socket
import os
import threading
import random
import rudp_lib  # מייבאים את ספריית העזר שלנו

# --- קבועים למניעת מספרי קסם --- (קוד זה נוצר/שופר בעזרת AI)
FTP_TCP_PORT = 2121
FTP_RUDP_PORT = 2122
BUFFER_SIZE = 4096
RUDP_CHUNK_SIZE = 1024  # נחלק את הקובץ לחתיכות קטנות כדי לראות את החלון זז
SERVER_DIR = "server_files"  # מסלול יחסי


def setup_server_directory():
    """מוודא שתיקיית השרת קיימת, ואם לא - יוצר אותה."""
    if not os.path.exists(SERVER_DIR):
        os.makedirs(SERVER_DIR)
        with open(os.path.join(SERVER_DIR, "test_file.txt"), "w") as f:
            f.write("Balex fag\n" * 50)
        print(f"[*] Created directory '{SERVER_DIR}' with a dummy file.")


def handle_tcp_client(client_socket):
    """מטפל בלקוח TCP רגיל (LIST ו-RETR)."""
    try:
        while True:
            request = client_socket.recv(BUFFER_SIZE).decode('utf-8').strip()
            if not request: break

            if request == "LIST":
                files = os.listdir(SERVER_DIR)
                files_str = "\n".join(files) if files else "Empty directory"
                client_socket.send(files_str.encode('utf-8'))

            elif request.startswith("RETR "):
                filename = request.split(" ")[1]
                filepath = os.path.join(SERVER_DIR, filename)

                if os.path.exists(filepath):
                    filesize = os.path.getsize(filepath)
                    client_socket.send(f"OK {filesize}".encode('utf-8'))
                    client_socket.recv(BUFFER_SIZE)  # מקבל READY

                    with open(filepath, "rb") as f:
                        bytes_read = f.read(BUFFER_SIZE)
                        while bytes_read:
                            client_socket.send(bytes_read)
                            bytes_read = f.read(BUFFER_SIZE)
                else:
                    client_socket.send("ERROR File not found".encode('utf-8'))
            elif request == "QUIT":
                break
    except Exception as e:
        print(f"[-] TCP Client error: {e}")
    finally:
        client_socket.close()


def start_tcp_server():
    """מפעיל את שרת ה-TCP בהאזנה."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(('0.0.0.0', FTP_TCP_PORT))
    server_socket.listen(5)
    print(f"[*] TCP Server listening on port {FTP_TCP_PORT}...")

    while True:
        client_sock, addr = server_socket.accept()
        # פותח ת'רד חדש לכל לקוח TCP
        threading.Thread(target=handle_tcp_client, args=(client_sock,)).start()


def start_rudp_server():
    """
    מפעיל את שרת ה-RUDP.
    מממש מנגנון חלון דינמי (Congestion Control) ואמינות (Go-Back-N).
    (פונקציה זו נכתבה בעזרת AI כדי לענות על דרישות פרויקט הגמר)
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.bind(('0.0.0.0', FTP_RUDP_PORT))
    print(f"[*] RUDP Server listening on port {FTP_RUDP_PORT}...")

    while True:
        # 1. המתנה לבקשת התחברות מהלקוח (SYN)
        try:
            # מנקה timeout לטובת המתנה ללקוח חדש
            server_socket.settimeout(None)
            packet_bytes, client_addr = server_socket.recvfrom(65535)
            seq, ack, flags, data = rudp_lib.parse_packet(packet_bytes)

            if flags & rudp_lib.FLAG_SYN:
                request = data.decode('utf-8')
                if request.startswith("RETR "):
                    filename = request.replace("RETR ", "")
                    filepath = os.path.join(SERVER_DIR, filename)

                    if not os.path.exists(filepath):
                        print(f"[-] RUDP: File '{filename}' not found.")
                        continue

                    # קריאת הקובץ וחלוקתו לחבילות קטנות
                    chunks = []
                    with open(filepath, 'rb') as f:
                        while True:
                            chunk = f.read(RUDP_CHUNK_SIZE)
                            if not chunk: break
                            chunks.append(chunk)

                    total_packets = len(chunks)
                    print(f"\n[*] RUDP: Starting to send '{filename}' to {client_addr}. Total packets: {total_packets}")

                    # --- משתני חלון דינמי (Sliding Window & Congestion Control) ---
                    base = 1
                    next_seq_num = 1
                    window_size = 2  # גודל חלון התחלתי
                    max_window_size = 10  # גודל חלון מקסימלי

                    # 2. לולאת השליחה (Go-Back-N)
                    while base <= total_packets:
                        # שליחת חבילות כל עוד אנחנו בתוך החלון המותר
                        while next_seq_num < base + window_size and next_seq_num <= total_packets:
                            chunk_data = chunks[next_seq_num - 1]  # אינדקס המערך קטן ב-1 ממספר הרצף
                            packet = rudp_lib.create_packet(next_seq_num, 0, rudp_lib.FLAG_DATA, chunk_data)
                            # --- מנגנון יצירת שגיאות מכוונות לבדיקה (20% איבוד) ---
                            if random.randint(1, 100) < 20:
                                print(f"  [X] SIMULATING PACKET LOSS: Dropped packet {next_seq_num} intentionally.")
                            else:
                                server_socket.sendto(packet, client_addr)
                                print(f"  [>] Sent packet {next_seq_num}")
                            next_seq_num += 1

                        # 3. המתנה לאישור (ACK)
                        server_socket.settimeout(0.5)  # הגדרת זמן פקיעה (Timeout) לאמינות
                        try:
                            ack_bytes, _ = server_socket.recvfrom(65535)
                            _, ack_num, ack_flags, _ = rudp_lib.parse_packet(ack_bytes)

                            if ack_flags & rudp_lib.FLAG_ACK:
                                if ack_num > base:
                                    print(f"  [<] Received ACK {ack_num}. Moving window forward.")
                                    base = ack_num

                                    # Congestion Control: הגדלת החלון (Additive Increase)
                                    if window_size < max_window_size:
                                        window_size += 1

                        except socket.timeout:
                            # 4. הטיפול ב-Timeout (Go-Back-N)
                            print(f"  [!] Timeout! Resending from {base}. Decreasing window size.")
                            # Congestion Control: הקטנת החלון בחצי (Multiplicative Decrease)
                            window_size = max(1, window_size // 2)
                            next_seq_num = base  # מחזירים את סמן השליחה לאחור כדי לשדר שוב

                    # 5. סיום: שליחת דגל FIN
                    print("[*] RUDP: File sent completely. Sending FIN.")
                    fin_packet = rudp_lib.create_packet(0, 0, rudp_lib.FLAG_FIN)
                    server_socket.sendto(fin_packet, client_addr)

        except Exception as e:
            print(f"[-] RUDP Server error: {e}")


if __name__ == "__main__":
    setup_server_directory()

    # שימוש ב-Threading כדי להריץ את ה-TCP ואת ה-RUDP במקביל
    tcp_thread = threading.Thread(target=start_tcp_server)
    tcp_thread.daemon = True  # יסיים את הת'רד כשהתוכנית הראשית מסתיימת
    tcp_thread.start()

    rudp_thread = threading.Thread(target=start_rudp_server)
    rudp_thread.daemon = True
    rudp_thread.start()

    print("\n[V] Both FTP (TCP) and RUDP servers are running in parallel.")
    print("Press Ctrl+C to exit.\n")

    # משאיר את התוכנית הראשית פועלת
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print("\n[-] Shutting down servers.")