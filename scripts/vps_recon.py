"""Read-only reconnaissance of CHP VPS — pre-deploy state check.

Does NOT modify anything. Verifies connection, checks app dir, current
git commit, winkers presence, disk, python version. Output guides the
deploy plan.

Credentials read from `data/wip/deploy-vps.md` in the CHP repo (not
hardcoded here).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import paramiko

VPS_HOST = "***REDACTED-VPS***"
VPS_USER = "root"
APP_DIR = "/opt/tespy-chp-web"
WIP_FILE = Path("C:/Development/CHP model web/data/wip/deploy-vps.md")


def _password() -> str:
    """Read VPS password from the deploy-vps wip note (avoids hardcoding)."""
    text = WIP_FILE.read_text(encoding="utf-8")
    m = re.search(r"root\s*/\s*(\S+)", text)
    if not m:
        raise RuntimeError("can't extract root password from deploy-vps.md")
    return m.group(1)


CHECKS = [
    ("connection", "echo ok && whoami && hostname && uname -a"),
    ("app_dir", f"ls -la {APP_DIR} 2>&1 | head -15"),
    ("git_status", f"cd {APP_DIR} 2>/dev/null && git rev-parse HEAD && "
                   f"git status --short | head -20 || echo '(no git repo at {APP_DIR})'"),
    ("winkers_dir", f"ls -la {APP_DIR}/.winkers 2>&1 | head -10"),
    ("winkers_bin", "which winkers && winkers --version 2>&1 || echo '(winkers not installed)'"),
    ("python", "python3 --version && pip3 --version 2>&1 | head -1"),
    ("claude_cli", "which claude 2>&1 && claude --version 2>&1 | head -1 || echo '(claude CLI not installed)'"),
    ("disk", "df -h /opt /tmp /root 2>&1 | head -8"),
    ("hf_cache", f"ls -la /root/.cache/huggingface/hub/ 2>&1 | head -10 || echo '(no HF cache yet)'"),
    ("data_files", f"ls -la {APP_DIR}/data/*.json 2>&1 | head -10"),
]


def main() -> int:
    pwd = _password()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {VPS_USER}@{VPS_HOST}...")
    client.connect(VPS_HOST, username=VPS_USER, password=pwd, timeout=20)

    for name, cmd in CHECKS:
        print(f"\n=== {name} ===")
        stdin, stdout, stderr = client.exec_command(cmd, timeout=20)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        if out:
            for line in out.splitlines()[:15]:
                print(f"  {line}")
        if err:
            for line in err.splitlines()[:5]:
                print(f"  [stderr] {line}")

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
