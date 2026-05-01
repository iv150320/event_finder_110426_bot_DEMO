#!/usr/bin/env python3
"""Real event search — wrapper that calls event_search.py with defaults.

This replaces the old hardcoded test output. It runs the actual event search
and prints human-readable results suitable for Telegram.
"""

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent
EVENT_SEARCH = SKILL_DIR / "event_search.py"


def main():
    # Default search: конференции in Москва
    cmd = [sys.executable, str(EVENT_SEARCH)]
    # Pass through any CLI args
    if len(sys.argv) > 1:
        cmd.extend(sys.argv[1:])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"Ошибка при поиске мероприятий:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    print(result.stdout)


if __name__ == "__main__":
    main()
