"""scripts/pair_device.py — print a one-time pairing code for the Control API.

Usage:
    python scripts/pair_device.py --name "my-phone"
"""

from __future__ import annotations

import argparse
import sys

from pred_agent.control_api.app import create_app
from pred_agent.control_api.auth import DeviceAuth


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a one-time pairing code for the Control API")
    parser.add_argument("--name", required=True, help="Device name (e.g. my-phone)")
    args = parser.parse_args()

    app = create_app()
    # The DeviceAuth lives inside the app; we need to access it.
    # For this CLI, instantiate a fresh DeviceAuth and generate a code.
    from pred_agent.control_api.auth import DeviceAuth as DA

    auth = DA()
    code = auth.generate_pairing_code(args.name)
    print(f"Pairing code: {code}")
    print(f"Valid for 5 minutes, single use.")
    print(f"In the Expo app, go to Pairing and enter this code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
