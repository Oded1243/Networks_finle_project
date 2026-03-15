import subprocess
import time
import sys
import os
import signal


def install_dependencies():
    print("[*] Checking dependencies...")
    needed = []

    try:
        import PIL

        print("[+] Pillow (PIL) is installed.")
    except ImportError:
        needed.append("Pillow")

    if needed:
        print(f"[-] Missing dependencies: {', '.join(needed)}. Installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install"] + needed)
            print("[+] Dependencies installed successfully.")
        except subprocess.CalledProcessError as e:
            print(f"[-] Failed to install dependencies: {e}")
            sys.exit(1)


def kill_existing_servers():
    print("[*] Killing existing server processes to free up ports...")
    targets = ["dhcp_server.py", "local_dns.py", "object_storage_server.py"]

    if sys.platform == "win32":
        for target in targets:
            ps_cmd = (
                f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{target}*' }}"
                f" | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
            )
            subprocess.run(
                ["powershell", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    else:
        # Linux/Mac implementation (pkill -f)
        for target in targets:
            subprocess.run(
                ["pkill", "-f", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def run_process(script_name, new_console=True):
    if sys.platform == "win32" and new_console:
        cmd = f'start "Server" cmd /k "{sys.executable} {script_name}"'
        return subprocess.Popen(cmd, shell=True)
    else:
        return subprocess.Popen([sys.executable, script_name])


def main():
    print("=== Starting Project with GUI Client ===")

    kill_existing_servers()
    time.sleep(1)

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

        print("[*] Starting Object Storage Server (RUDP)...")
        ftp = run_process("src/servers/object_storage_server.py")
        processes.append(ftp)
        time.sleep(2)

        print("\n[*] Starting GUI Client...")
        subprocess.call([sys.executable, "src/client/file_manager_gui.py"])

    except KeyboardInterrupt:
        print("\n[-] Stopping...")
    except Exception as e:
        print(f"[-] Error: {e}")
    finally:
        print("\n[*] Cleaning up...")
        print(
            "[!] Please manually close the opened server windows (DHCP, DNS, Object Storage)."
        )


if __name__ == "__main__":
    main()
