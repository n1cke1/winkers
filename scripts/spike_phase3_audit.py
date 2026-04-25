"""Phase 3 mini-spike: simulate stop-audit on the topology JSON fix.

Builds an audit packet from:
  - the real change (4 nodes added to data/tespy_topology.json)
  - coupling units in .winkers/units.json that touch the changed file

Then spawns `claude --print --allowedTools "Read,Grep,Glob"` (cwd=tmp,
matching what we learned about hook recursion) with a draft audit
prompt. Outputs the response — to evaluate quality of TODO list and
whether full Phase 3 pipeline is worth building.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("C:/Development/CHP model web")
UNITS_PATH = ROOT / ".winkers" / "units.json"
CHANGED_FILE = "data/tespy_topology.json"
DIFF_SUMMARY = """\
File modified: data/tespy_topology.json

Added 4 nodes that exist in TESPy live network but were missing from
the static JSON layout (causing them to render at coord (0,0) with NaN
edges). Patch lines below:

    {"id":"rou9_spray_src","label":"Хим. вода РОУ-9","type":"Source","group":"ROU9","formulas":[],"x":60,"y":440,"w":100,"h":26},
    {"id":"rou9_mix","label":"Смес. РОУ-9","type":"Merge","group":"ROU9","formulas":[],"x":290,"y":494,"w":90,"h":30},
    {"id":"brou16_split","label":"Разд. БРОУ-16","type":"Splitter","group":"BROU16","formulas":[],"x":290,"y":554,"w":90,"h":30},
    {"id":"brou16_industry","label":"Промпотр. БРОУ","type":"Sink","group":"BROU16","formulas":[],"x":440,"y":614,"w":90,"h":28}

These 4 nodes connect via 6 existing edges (already in TESPy live):
  rou9 → rou9_mix (rou9_throttled)
  rou9_spray_src → rou9_mix (rou9_spray_in)
  rou9_mix → prod_merge (rou9_out)
  brou16 → brou16_split (brou16_red)
  brou16_split → brou16_hx (brou16_peak_in)
  brou16_split → brou16_industry (brou16_industry_out)
"""


def find_relevant_couplings(units: list[dict], changed_file: str) -> list[dict]:
    """Couplings whose consumers touch the changed file."""
    out = []
    for u in units:
        if not u.get("id", "").startswith("coupling:"):
            continue
        if changed_file in u.get("source_files", []):
            out.append(u)
            continue
        for cn in u.get("consumers", []):
            if cn.get("file") == changed_file:
                out.append(u)
                break
    return out


def find_units_anchored_to_file(units: list[dict], file: str) -> list[dict]:
    """function_units / template_units whose source_files include the changed file."""
    out = []
    for u in units:
        if u.get("kind") == "function_unit":
            anchor = u.get("anchor", {}) or {}
            if anchor.get("file") == file:
                out.append(u)
        elif file in u.get("source_files", []):
            out.append(u)
    return out


def build_audit_prompt(packet: dict) -> str:
    return f"""You are auditing a code change for cross-file coherence drift.

CHANGE SUMMARY
---
{packet['diff_summary']}

UNITS THAT REFERENCE THE CHANGED FILE ({len(packet['units'])} found):
{packet['units_summary']}

COUPLED ARTIFACTS THAT MAY BE STALE ({len(packet['couplings'])} found):
{packet['couplings_summary']}

YOUR TASK
---
For each coupled artifact above, decide whether the change to the JSON
layout requires a synchronized update elsewhere. Consider:

1. Does the new node need a corresponding entry in any Python/JS code?
2. Are the labels/types/groups consistent with what other code expects?
3. Are there data-flow descriptions (in approach docs, README, etc.)
   that mention these nodes and need updating?
4. Are there UI/visualization features that filter or special-case
   these node types?

You have read-only tools (Read, Grep, Glob) — use them sparingly to
verify only the items you're least sure about.

OUTPUT
---
A markdown checklist of TODO items in the format:
  - [ ] file:line — action — rationale

If nothing needs synchronization, output exactly:
  - (no coherence drift detected)

Output ONLY the checklist. No preamble, no postscript.
"""


def main() -> int:
    units = json.loads(UNITS_PATH.read_text(encoding="utf-8"))["units"]

    relevant_units = find_units_anchored_to_file(units, CHANGED_FILE)
    relevant_couplings = find_relevant_couplings(units, CHANGED_FILE)

    units_summary = "\n".join(
        f"  - [{u.get('kind','?')}] {u['id']}: "
        f"{(u.get('description') or '')[:120]}..."
        for u in relevant_units[:5]
    ) or "  (no units anchored to this file in index)"

    coupling_summary_lines = []
    for c in relevant_couplings[:8]:
        meta = c.get("meta", {})
        coupling_summary_lines.append(
            f"  - {c['id']}: value={meta.get('canonical_value')!r} "
            f"({meta.get('primary_kind')}, {meta.get('file_count')} files)"
        )
        for cn in c.get("consumers", [])[:3]:
            coupling_summary_lines.append(
                f"      • {cn['file']}::{cn.get('anchor','')}  "
                f"— {cn.get('what_to_check','')[:90]}"
            )
    couplings_summary = ("\n".join(coupling_summary_lines)
                         or "  (no couplings touch this file)")

    packet = {
        "diff_summary": DIFF_SUMMARY,
        "units": relevant_units,
        "couplings": relevant_couplings,
        "units_summary": units_summary,
        "couplings_summary": couplings_summary,
    }
    prompt = build_audit_prompt(packet)

    print(f"=== Audit packet ===")
    print(f"  units anchored to file: {len(relevant_units)}")
    print(f"  couplings touching file: {len(relevant_couplings)}")
    print(f"  prompt length: {len(prompt)} chars")
    print()

    # Run claude --print (read-only, cwd=tmp to dodge project hooks)
    claude_bin = shutil.which("claude.cmd") or shutil.which("claude")
    if not claude_bin:
        print("ERROR: claude not found", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    print("=== Running claude --print (read-only audit, ~60-120s)... ===\n")
    result = subprocess.run(
        [claude_bin, "--print",
         "--allowedTools", "Read,Grep,Glob"],
        input=prompt,
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        cwd=tempfile.gettempdir(),
        env=env,
        timeout=300,
    )
    print("=== AUDIT OUTPUT ===")
    print(result.stdout or "(empty)")
    if result.stderr.strip():
        print("\n=== STDERR ===")
        print(result.stderr.strip()[:500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
