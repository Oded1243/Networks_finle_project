import pytest

try:
    from dnslib import DNSRecord
except ImportError:
    DNSRecord = None


@pytest.mark.skipif(DNSRecord is None, reason="dnslib not installed")
def test_dns_query():
    print("Building DNS query for test.local...")

    q = DNSRecord.question("test.local")

    print("Sending to 127.0.0.1 on port 5053...")
    try:
        # Note: This requires the DNS server to be running.
        # If this is a unit test, we should mock the socket or start the server.
        # For now, we keep the existing logic but wrapped in a test function.
        answer_bytes = q.send("127.0.0.1", 5053, tcp=False, timeout=3)
        print("\n--- Success! Received Response ---")

        parsed_answer = DNSRecord.parse(answer_bytes)

        print(parsed_answer)

    except TimeoutError:
        pytest.fail("The server did not respond (Timeout).")
    except ConnectionRefusedError:
        pytest.fail("Connection refused. Is the server definitely running?")
    except Exception as e:
        pytest.fail(f"Unexpected Error: {e}")


if __name__ == "__main__":
    if DNSRecord is None:
        print("dnslib not installed, cannot run test.")
        exit(1)
    test_dns_query()
