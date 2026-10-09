"""Container healthcheck: exit non-zero unless the worker heartbeat is recent."""

import sys

from .__main__ import heartbeat_is_fresh, heartbeat_path


def main() -> int:
    return 0 if heartbeat_is_fresh(heartbeat_path()) else 1


if __name__ == "__main__":
    sys.exit(main())
