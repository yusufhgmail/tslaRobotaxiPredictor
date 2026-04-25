"""
Write the current unsupervised count into `data/last_emailed.json`.

Called by the daily workflow as the final step (only on auto runs, never
on manual admin runs). After notify.py + newsletter.py have run, this
records "we have now emailed everyone about this count" so tomorrow's run
can compare against it.
"""
from __future__ import annotations

import sys

import email_state


def main() -> int:
    count = email_state.get_current_count()
    if count is None:
        print("mark_emailed: no current count, skipping", file=sys.stderr)
        return 0
    email_state.mark_emailed(count)
    print(f"mark_emailed: recorded {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
