"""
Convenience runner for the edge-case test suite.

Starts the three servers (DHCP, DNS, Object Storage) in the background,
waits for them to be ready, then runs the pytest suite.

Usage:
    python run_edge_tests.py            # normal run
    python run_edge_tests.py -v -s      # verbose with stdout
"""

import subprocess
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    print("=" * 60)
    print("  Edge-Case / Wireshark Test Suite Runner")
    print("=" * 60)
    print()
    print("Servers will be started automatically by the test fixtures.")
    print("To monitor in Wireshark, capture on the Loopback adapter")
    print("and use one of these display filters:")
    print()
    print('    frame contains "TEST_CASE"          (all tests)')
    print('    frame contains "TEST_CASE:packet_loss"  (specific test)')
    print()
    print("-" * 60)

    # Pass through any extra args (e.g. -v, -s, -k)
    extra = sys.argv[1:] if len(sys.argv) > 1 else ["-v", "-s"]

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        os.path.join(PROJECT_ROOT, "tests", "test_edge_cases_live.py"),
        "--tb=short",
    ] + extra

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
