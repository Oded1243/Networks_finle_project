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


def is_admin():
    try:
        import ctypes

        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_process(script_name, env=None, new_console=True):
    # Use 'start' on Windows to open in new console window
    final_env = os.environ.copy()
    if env:
        final_env.update(env)

    if sys.platform == "win32" and new_console:
        # Pass environment variables to the new console window
        # Note: 'start' command inherits environment variables of current process
        # so we need to set them in current process or use a different approach.
        # However, subprocess.Popen with shell=True inherits current env if env is None or merged.
        # But 'start' launches a separate cmd.exe which inherits from parent.
        # Let's set the env vars in the current process temporarily or use a wrapper.
        # Actually, simpler is to set os.environ for the duration of run_tests.py if needed.
        return subprocess.Popen(
            f"start python {script_name}", shell=True, env=final_env
        )
    else:
        return subprocess.Popen([sys.executable, script_name], env=final_env)


def main():
    print("=== Starting Network Project Test ===")
    install_dependencies()

    if not is_admin():
        print("[!] Not running as Administrator. Switching to non-privileged ports.")
        os.environ["DHCP_SERVER_PORT"] = "6700"
        os.environ["DHCP_CLIENT_PORT"] = "6800"
        print("[*] DHCP Server Port: 6700")
        print("[*] DHCP Client Port: 6800")
    else:
        print("[+] Running as Administrator. Using standard ports (67/68).")

    print(
        "\n[!] NOTE: DHCP Server (port 67) and Client (port 68) often require Admin privileges."
    )
    print(
        "[!] Ensure you are running this script as Administrator if you encounter permission errors.\n"
    )

    processes = []

    try:
        print("[*] Starting DHCP Server...")
        dhcp = run_process("src/servers/dhcp_server.py", env=os.environ)
        processes.append(dhcp)
        time.sleep(1)

        print("[*] Starting Local DNS Server...")
        dns = run_process("src/servers/local_dns.py")
        processes.append(dns)
        time.sleep(1)

        print("[*] Starting Object Storage Server...")
        ftp = run_process("src/servers/object_storage_server.py")
        processes.append(ftp)
        time.sleep(2)

        print("\n[*] Starting Client...")
        subprocess.call([sys.executable, "src/client/client.py"])

    except KeyboardInterrupt:
        print("\n[-] Stopping test...")
    except Exception as e:
        print(f"[-] Error: {e}")
    finally:
        print("\n[*] Cleaning up...")
        print(
            "[!] Please manually close the opened server windows (DHCP, DNS, Object Storage)."
        )


if __name__ == "__main__":
    main()
