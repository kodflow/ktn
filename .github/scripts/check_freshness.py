#!/usr/bin/env python3
"""Report how much life the roster it is about to replace had left.

The signing workflow runs on an hourly cron, and GitHub's scheduler is best
effort: it delays runs and skips them under load. A single skip is harmless —
the roster's window is 24 hours — but nothing ever noticed a run of them.

That is exactly how this repository went down: a ruleset started refusing the
bot's push, twelve consecutive runs failed, and the first symptom anybody saw
was every licensed binary refusing to start, thirteen hours later. A failing
hourly cron on a quiet repository looks precisely like a quiet repository.

So the workflow reports what it found. Anything under the alert threshold
means signatures have been missing for most of a day, and the run that
notices it is the last chance to say so before the window closes.
"""
import datetime
import json
import os
import pathlib

# How much remaining life counts as "something is wrong". The window is 24h
# and signing is hourly, so a healthy roster is always near-full when it is
# replaced; six hours left means roughly eighteen consecutive misses.
ALERT_HOURS = 6


def licenses_dir() -> pathlib.Path:
    """Where licence state lives; see build_roster.py for the same helper."""
    return pathlib.Path(os.environ.get("LICENSES_DIR", "licenses"))


def emit(name: str, value: str) -> None:
    """Publish a step output when running under Actions, else print it."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        print(f"{name}={value}")
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main() -> None:
    path = licenses_dir() / "roster.json"
    # No roster yet is the first-ever run, not a stale one.
    if not path.exists():
        print("no previous roster — first signature")
        emit("stale", "false")
        emit("hours_left", "")
        return

    previous = json.loads(path.read_text())
    expires = datetime.datetime.fromisoformat(previous["exp"].replace("Z", "+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)
    hours_left = (expires - now).total_seconds() / 3600

    print(f"previous roster had {hours_left:.1f}h left (alert below {ALERT_HOURS}h)")
    emit("hours_left", f"{hours_left:.1f}")
    # Negative means the window had already closed: clients were refusing.
    emit("stale", "true" if hours_left < ALERT_HOURS else "false")


if __name__ == "__main__":
    main()
