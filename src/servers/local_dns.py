import socket
import struct
import http.server
import socketserver
import threading

DNS_PORT = 5053
DOH_PORT = 8053
DNS_HOST = "127.0.0.1"

LOCAL_RECORDS = {
    "ftp.local": "127.0.0.1",
    "object.store": "127.0.0.1",
    "database.local": "127.0.0.1",
    "router.local": "127.0.0.1",
}

DNS_HEADER_LENGTH = 12
FLAGS_RESPONSE_OK = 0x8180
FLAGS_RESPONSE_NXDOMAIN = 0x8183
POINTER_TO_QNAME = 0xC00C
TYPE_A = 1
CLASS_IN = 1
DEFAULT_TTL = 60


def parse_qname(data, offset):
    domain_parts = []
    try:
        while True:
            length = data[offset]
            if length == 0:
                offset += 1
                break
            offset += 1
            part = data[offset : offset + length].decode("utf-8")
            domain_parts.append(part)
            offset += length

        domain_name = ".".join(domain_parts)
        return domain_name, offset
    except Exception as e:
        print(f"[-] Error parsing QNAME: {e}")
        return None, offset


def build_dns_response(transaction_id, original_question_bytes, ip_address):
    if ip_address:
        flags = FLAGS_RESPONSE_OK
        ancount = 1
    else:
        flags = FLAGS_RESPONSE_NXDOMAIN
        ancount = 0

    header = struct.pack("!HHHHHH", transaction_id, flags, 1, ancount, 0, 0)
    response = header + original_question_bytes

    if ip_address:
        ip_bytes = socket.inet_aton(ip_address)
        answer = struct.pack(
            "!HHHLH4s", POINTER_TO_QNAME, TYPE_A, CLASS_IN, DEFAULT_TTL, 4, ip_bytes
        )
        response += answer

    return response


def start_udp_dns_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((DNS_HOST, DNS_PORT))
    print(f"[*] Starting UDP DNS server on {DNS_HOST}:{DNS_PORT}...")

    while True:
        try:
            data, addr = sock.recvfrom(512)
            if len(data) >= DNS_HEADER_LENGTH:
                header = struct.unpack("!HHHHHH", data[:DNS_HEADER_LENGTH])
                transaction_id = header[0]
                domain_name, next_offset = parse_qname(data, DNS_HEADER_LENGTH)

                if domain_name:
                    print(f"\n[UDP] Request from {addr} for: {domain_name}")
                    end_of_question = next_offset + 4
                    original_question_bytes = data[DNS_HEADER_LENGTH:end_of_question]
                    ip_address = LOCAL_RECORDS.get(domain_name)

                    response_data = build_dns_response(
                        transaction_id, original_question_bytes, ip_address
                    )
                    sock.sendto(response_data, addr)
        except Exception as e:
            print(f"[-] UDP error: {e}")


class DoHHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/dns-query":
            content_length = int(self.headers.get("Content-Length", 0))
            dns_query_data = self.rfile.read(content_length)

            if len(dns_query_data) >= DNS_HEADER_LENGTH:
                header = struct.unpack("!HHHHHH", dns_query_data[:DNS_HEADER_LENGTH])
                transaction_id = header[0]
                domain_name, next_offset = parse_qname(
                    dns_query_data, DNS_HEADER_LENGTH
                )

                if domain_name:
                    print(f"\n[DoH] Request for: {domain_name}")
                    end_of_question = next_offset + 4
                    original_question_bytes = dns_query_data[
                        DNS_HEADER_LENGTH:end_of_question
                    ]
                    ip_address = LOCAL_RECORDS.get(domain_name)

                    response_data = build_dns_response(
                        transaction_id, original_question_bytes, ip_address
                    )

                    if response_data:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/dns-message")
                        self.send_header("Content-Length", str(len(response_data)))
                        self.end_headers()
                        self.wfile.write(response_data)
                        return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def start_doh_server():
    Handler = DoHHandler
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((DNS_HOST, DOH_PORT), Handler) as httpd:
        print(f"[*] Starting DoH server on http://{DNS_HOST}:{DOH_PORT}/dns-query ...")
        httpd.serve_forever()


if __name__ == "__main__":

    udp_thread = threading.Thread(target=start_udp_dns_server, daemon=True)
    udp_thread.start()

    try:
        start_doh_server()
    except KeyboardInterrupt:
        print("\nStopping servers.")
