"""Heartbeat files: cron jobs touch RUN_DIR/<name>.heartbeat after each successful run.

The console dashboard reads the file's modified time to show when a job last ran.
(CloudWatch alarms on the jobs' log lines instead; see deploy/MONITORING.md.)
"""

import datetime
from pathlib import Path

from django.conf import settings


def path(name):
    return Path(settings.RUN_DIR) / f"{name}.heartbeat"


def beat(name):
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


def last_beat(name):
    """When the job last beat, as an aware datetime, or None if it never has."""
    try:
        mtime = path(name).stat().st_mtime
    except FileNotFoundError:
        return None
    return datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc)
