"""Test couplings CLI end-to-end with synthetic data.

Builds a temporary .winkers/units.json with overlapping artifacts,
invokes the CLI, verifies couplings are detected and (with --save)
written back to the file.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Synthetic units: two functions and one template section with overlap.
TEST_UNITS = {
    "units": [
        {
            "id": "engine/equations.py::build_constraints",
            "kind": "function_unit",
            "name": "build_constraints",
            "anchor": {"file": "engine/equations.py", "fn": "build_constraints"},
            "description": "Builds MILP constraint matrix...",
            "hardcoded_artifacts": [
                {"value": "33", "kind": "count", "context": "len(IDX) — vars"},
                {"value": "8", "kind": "count", "context": "constraint groups"},
                {"value": ["PT1","PT2","PT6","R3","R4","T5"], "kind": "id_list",
                 "context": "turbine names"},
                {"value": "K_regen", "kind": "identifier",
                 "context": "regeneration share"},
            ],
        },
        {
            "id": "template:templates/index.html#calc-sub-approach",
            "kind": "traceability_unit",
            "name": "Approach tab",
            "source_files": ["templates/index.html"],
            "description": "Approach methodology pipeline...",
            "hardcoded_artifacts": [
                {"value": "33", "kind": "count", "context": "var counter",
                 "surface": "33 переменных"},
                {"value": "8", "kind": "count", "context": "groups in step 2"},
                {"value": ["T5","PT2","R3","PT1","PT6","R4"], "kind": "id_list",
                 "context": "6 turbines (different order!)"},
                {"value": "K_regen", "kind": "identifier",
                 "context": "regeneration mention"},
                {"value": "0.265", "kind": "threshold",
                 "context": "typical K_regen"},
            ],
        },
        {
            "id": "ui_tab_results",
            "kind": "traceability_unit",
            "name": "Results tab",
            "source_files": ["static/js/tab_results.js"],
            "description": "Results panel rendering...",
            "hardcoded_artifacts": [
                {"value": ["PT1","PT2","PT6","R3","R4","T5"], "kind": "id_list",
                 "context": "turbList in renderResults"},
            ],
        },
    ]
}


def main() -> int:
    # Use a sandbox dir to avoid touching real CHP data
    sandbox = Path("C:/Development/Winkers/scripts/_sandbox_couplings")
    if sandbox.exists():
        shutil.rmtree(sandbox)
    (sandbox / ".winkers").mkdir(parents=True)
    units_path = sandbox / ".winkers" / "units.json"
    units_path.write_text(json.dumps(TEST_UNITS, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    venv_python = Path("C:/Development/Winkers/.venv/Scripts/python.exe")

    # 1) Run couplings (no save) — should list clusters
    print("=== run 1: list-only ===")
    res = subprocess.run(
        [str(venv_python), "-c",
         "import sys, io; "
         "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8'); "
         f"sys.argv = ['winkers', 'couplings', '--root', '{sandbox}']; "
         "from winkers.cli.main import cli; cli(standalone_mode=False)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr[:300])

    # 2) Run with --save
    print("\n=== run 2: --save ===")
    res2 = subprocess.run(
        [str(venv_python), "-c",
         "import sys, io; "
         "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8'); "
         f"sys.argv = ['winkers', 'couplings', '--root', '{sandbox}', '--save']; "
         "from winkers.cli.main import cli; cli(standalone_mode=False)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(res2.stdout[-500:] if len(res2.stdout) > 500 else res2.stdout)
    if res2.stderr:
        print("STDERR:", res2.stderr[:300])

    # 3) Verify units.json now contains coupling traceability_units
    print("\n=== verifying saved units.json ===")
    saved = json.loads(units_path.read_text(encoding="utf-8"))
    coupling_units = [u for u in saved["units"]
                      if u.get("id", "").startswith("coupling:")]
    print(f"  saved {len(coupling_units)} coupling units, ids:")
    for u in coupling_units:
        print(f"    - {u['id']}  ({u['meta']['file_count']} files, "
              f"{u['meta']['hit_count']} hits)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
