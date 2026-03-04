from dnslib import DNSRecord

print("Building DNS query for test.local...")

q = DNSRecord.question("test.local")

print("Sending to 127.0.0.1 on port 5053...")
try:

    answer_bytes = q.send("127.0.0.1", 5053, tcp=False, timeout=3)
    print("\n--- Success! Received Response ---")

    parsed_answer = DNSRecord.parse(answer_bytes)

    print(parsed_answer)

except TimeoutError:
    print("\n[!] Error: The server did not respond (Timeout).")
except ConnectionRefusedError:
    print("\n[!] Error: Connection refused. Is the server definitely running?")
except Exception as e:
    print(f"\n[!] Unexpected Error: {e}")
