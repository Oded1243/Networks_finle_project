from dnslib import QTYPE, RR, A
from dnslib.server import BaseResolver, DNSLogger, DNSServer

# --- Constants to avoid "magic numbers" --- (Created/improved with AI)
DNS_PORT = 5053  # Standard DNS port
DNS_HOST = "127.0.0.1"  # Local address for testing
DEFAULT_TTL = 60  # Default TTL for record


class LocalResolver(BaseResolver):
    def __init__(self, records):
        """
        Initialize the resolver with a dictionary of domain -> IP mappings.
        """
        self.records = records

    def resolve(self, request, handler):
        reply = request.reply()
        qname = str(request.q.qname)
        clean_qname = qname.rstrip(".")

        if clean_qname in self.records:
            ip_address = self.records[clean_qname]
            # Use DEFAULT_TTL constant instead of magic number (this line was modified with AI)
            reply.add_answer(RR(qname, QTYPE.A, rdata=A(ip_address), ttl=DEFAULT_TTL))
            print(f"[+] Resolved {clean_qname} to {ip_address}")
        else:
            print(f"[-] No record for {clean_qname}")

        return reply


if __name__ == "__main__":
    # Mapping the domain to the project's FTP server (created/modified with AI)
    LOCAL_RECORDS = {
        "ftp.local": "127.0.0.1",
        "object.store": "127.0.0.1",
        "database.local": "127.0.0.1",
        "router.local": "127.0.0.1",
    }

    resolver = LocalResolver(LOCAL_RECORDS)
    logger = DNSLogger(log="request,reply,truncated,error")  # Logs DNS traffic

    # Use the constants we defined above (this line was modified with AI)
    server = DNSServer(resolver, port=DNS_PORT, address=DNS_HOST, logger=logger)

    print(f"Starting Local DNS server on {DNS_HOST}:{DNS_PORT}...")
    print("Press Ctrl+C to stop.")

    try:
        server.start()
    except KeyboardInterrupt:
        print("\nStopping DNS server.")
    finally:
        server.stop()
