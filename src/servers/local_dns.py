from dnslib import QTYPE, RR, A
from dnslib.server import BaseResolver, DNSLogger, DNSServer

DNS_PORT = 5053
DNS_HOST = "127.0.0.1"
DEFAULT_TTL = 60


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
            reply.add_answer(RR(qname, QTYPE.A, rdata=A(ip_address), ttl=DEFAULT_TTL))
            print(f"[+] Resolved {clean_qname} to {ip_address}")
        else:
            print(f"[-] No record for {clean_qname}")

        return reply


if __name__ == "__main__":
    LOCAL_RECORDS = {
        "ftp.local": "127.0.0.1",
        "object.store": "127.0.0.1",
        "database.local": "127.0.0.1",
        "router.local": "127.0.0.1",
    }

    resolver = LocalResolver(LOCAL_RECORDS)
    logger = DNSLogger(log="request,reply,truncated,error")

    server = DNSServer(resolver, port=DNS_PORT, address=DNS_HOST, logger=logger)

    print(f"Starting Local DNS server on {DNS_HOST}:{DNS_PORT}...")
    print("Press Ctrl+C to stop.")

    try:
        server.start()
    except KeyboardInterrupt:
        print("\nStopping DNS server.")
    finally:
        server.stop()
