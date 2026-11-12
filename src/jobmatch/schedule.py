"""Run JobMatch once a day without the user touching cron.

One scheduled command per profile runs every stage in order and sends the
digest. The run lock (jobmatch.runlock) makes a late-running day skip rather
than overlap the next one, so there is no need for split discovery jobs.

Linux uses the user's crontab, macOS a LaunchAgent, Windows Task Scheduler.
Each entry is tagged so installing again replaces it instead of adding a copy.
"""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path

TAG = "# jobmatch-daily"
LABEL = "com.jobmatch.daily"
TASK = "JobMatch daily"


def parse_time(hhmm: str) -> tuple[int, int]:
    try:
        hour, minute = (int(x) for x in hhmm.strip().split(":"))
    except ValueError:
        raise ValueError("Use 24-hour time like 07:30.") from None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("Use 24-hour time like 07:30.")
    return hour, minute


def run_command(profile: str | None = None) -> list[str]:
    cmd = [sys.executable, "-m", "jobmatch"]
    if profile:
        cmd += ["--profile", profile]
    return cmd + ["run", "--notify"]


def _tag(profile: str | None) -> str:
    return f"{TAG}:{profile or 'default'}"


def cron_line(hhmm: str, profile: str | None = None, log: Path | None = None) -> str:
    hour, minute = parse_time(hhmm)
    cmd = " ".join(shlex.quote(c) for c in run_command(profile))
    if os.environ.get("JOBMATCH_DIR"):  # cron starts with a bare environment
        cmd = f"JOBMATCH_DIR={shlex.quote(os.environ['JOBMATCH_DIR'])} {cmd}"
    if log:
        cmd += f" >> {shlex.quote(str(log))} 2>&1"
    return f"{minute} {hour} * * * {cmd} {_tag(profile)}"


def merge_crontab(current: str, line: str, profile: str | None = None) -> str:
    kept = [x for x in current.splitlines() if not x.rstrip().endswith(_tag(profile))]
    return "\n".join([*kept, line]).strip("\n") + "\n"


def launchd_plist(hhmm: str, profile: str | None = None, log: Path | None = None) -> str:
    hour, minute = parse_time(hhmm)
    args = "\n".join(f"    <string>{a}</string>" for a in run_command(profile))
    log_xml = ""
    if log:
        log_xml = (f"  <key>StandardOutPath</key><string>{log}</string>\n"
                   f"  <key>StandardErrorPath</key><string>{log}</string>\n")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}.{profile or 'default'}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>
{log_xml}</dict>
</plist>
"""


def install(hhmm: str, profile: str | None = None, log: Path | None = None) -> str:
    """Install or replace the daily run. Returns a sentence for the user."""
    system = platform.system()
    if system == "Darwin":
        name = f"{LABEL}.{profile or 'default'}"
        path = Path.home() / "Library" / "LaunchAgents" / f"{name}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True, check=False)
        path.write_text(launchd_plist(hhmm, profile, log), encoding="utf-8")
        subprocess.run(["launchctl", "load", str(path)], capture_output=True, check=True)
        return f"Runs every day at {hhmm} while this Mac is awake."
    if system == "Windows":
        cmd = subprocess.list2cmdline(run_command(profile))
        name = f"{TASK} {profile or 'default'}"
        subprocess.run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", hhmm, "/TN", name, "/TR", cmd],
                       capture_output=True, check=True)
        return f"Runs every day at {hhmm} while this PC is on."
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False).stdout
    new = merge_crontab(current, cron_line(hhmm, profile, log), profile)
    subprocess.run(["crontab", "-"], input=new, text=True, check=True)
    return f"Runs every day at {hhmm}."
