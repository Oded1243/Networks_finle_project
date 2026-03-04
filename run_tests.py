import subprocess
import time
import sys
import os
import signal


def install_dependencies():
    print("[*] Checking dependencies...")
    try:
        import dnslib

        print("[+] dnslib is installed.")
    except ImportError:
        print("[-] dnslib not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "dnslib"])


def run_process(script_name, new_console=True):
    # Use 'start' on Windows to open in new console window
    if sys.platform == "win32" and new_console:
        return subprocess.Popen(f"start python {script_name}", shell=True)
    else:
        return subprocess.Popen([sys.executable, script_name])


def main():
    print("=== Starting Network Project Test ===")
    install_dependencies()

    print(
        "\n[!] NOTE: DHCP Server (port 67) and Client (port 68) often require Admin privileges."
    )
    print(
        "[!] Ensure you are running this script as Administrator if you encounter permission errors.\n"
    )

    processes = []

    try:
        print("[*] Starting DHCP Server...")
        dhcp = run_process("src/servers/dhcp_server.py")
        processes.append(dhcp)
        time.sleep(1)

        print("[*] Starting Local DNS Server...")
        dns = run_process("src/servers/local_dns.py")
        processes.append(dns)
        time.sleep(1)

        print("[*] Starting FTP/RUDP Server...")
        ftp = run_process("src/servers/ftp_server.py")
        processes.append(ftp)
        time.sleep(2)

        print("\n[*] Starting Client...")
        # Client we want to see the output of in THIS window potentially,
        # but the other servers log to their own windows.
        # Let's run client in this window to see the "flow".
        subprocess.call([sys.executable, "src/client/client.py"])

    except KeyboardInterrupt:
        print("\n[-] Stopping test...")
    except Exception as e:
        print(f"[-] Error: {e}")
    finally:
        print("\n[*] Cleaning up...")
        # Since we used 'start' shell=True on Windows, we can't easily kill the spawned windows from here
        # without more complex logic, but we can tell the user to close them.
        print("[!] Please manually close the opened server windows (DHCP, DNS, FTP).")


if __name__ == "__main__":
    main()
