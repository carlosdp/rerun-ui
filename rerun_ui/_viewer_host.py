from __future__ import annotations

import argparse

from . import _rerun_ui


def main() -> int:
    parser = argparse.ArgumentParser(description="Run rerun_ui custom viewer process")
    parser.add_argument("--grpc-port", type=int, default=9876)
    parser.add_argument("--control-port", type=int, default=9877)
    args = parser.parse_args()

    _rerun_ui.run_viewer_process(args.grpc_port, args.control_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
