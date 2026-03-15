import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../common")))

from network_manager import NetworkManager


def main():
    print("=== Starting Object Storage Client ===\n")

    client = NetworkManager()

    if not client.connect_sequence():
        print("[!] Connection failed (DHCP or DNS).")
        return

    print("[*] Storage Nodes (Buckets):")
    buckets = client.list_buckets()
    print("\n".join(buckets) + "\n")

    print("[*] LIST objects...")
    files = client.list_files()
    if files:
        print("-- Objects --")
        for name, size in files:
            print(f"{name} ({size} bytes)")
        print("-------------")
    else:
        print("Storage is empty")


if __name__ == "__main__":
    main()
