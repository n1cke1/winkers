import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from adapters import get_adapter, get_model
from adapters.base import AbstractGraphAdapter
from bench.tasks.definitions import get_task, Task


def _find_claude_cmd() -> str:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        npm_claude = os.path.join(appdata, "npm", "claude.cmd")
        if os.path.exists(npm_claude):
            return npm_claude
    return shutil.which("claude") or "claude"


@dataclass
class RunResult:
    task_id: str
    adapter_id: str
    model_id: str
    config_label: str
    timestamp: str
    tests_before: "TestResult"
    tests_after: "TestResult"
    claude_output: dict
    wall_time_seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    exit_code: int = 0
    error: str | None = None

    actual_cost: float = 0.0
    num_turns: int = 0

    @property
    def estimated_cost(self) -> float:
        if self.actual_cost > 0:
            return self.actual_cost
        model = get_model(self.model_id)
        input_cost = self.input_tokens * model["input_price"] / 1_000_000
        output_cost = self.output_tokens * model["output_price"] / 1_000_000
        return input_cost + output_cost


@dataclass
class TestResult:
    passed: int
    failed: int
    errors: int
    total: int
    exit_code: int
    output: str

    @property
    def all_passed(self) -> bool:
        return self.exit_code == 0 and self.failed == 0 and self.errors == 0


def load_config(config_path: Path | None = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def copy_project(source: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".pytest_cache", ".git", "*.db",
    ))
    return dest


def write_mcp_json(project_path: Path, mcp_config: dict) -> None:
    if not mcp_config:
        mcp_file = project_path / ".mcp.json"
        if mcp_file.exists():
            mcp_file.unlink()
        return
    with open(project_path / ".mcp.json", "w") as f:
        json.dump(mcp_config, f, indent=2)


def run_pytest(project_path: Path, config: dict | None = None) -> TestResult:
    test_cmd = "python -m pytest tests/ -x -q"
    if config:
        project_name = _detect_project_name(project_path)
        project_cfg = config.get("projects", {}).get(project_name, {})
        test_cmd = project_cfg.get("test_command", test_cmd)

    try:
        result = subprocess.run(
            test_cmd.split(),
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return TestResult(passed=0, failed=0, errors=1, total=0, exit_code=1, output="pytest timed out")

    output = result.stdout + result.stderr
    passed, failed, errors = _parse_pytest_output(output)

    return TestResult(
        passed=passed,
        failed=failed,
        errors=errors,
        total=passed + failed + errors,
        exit_code=result.returncode,
        output=output,
    )


def build_prompt(task: Task, prompt_addendum: str, config: dict) -> str:
    template = config.get("prompt_template", "Task: {task_prompt}\n{prompt_addendum}")
    return template.format(
        task_prompt=task.prompt,
        prompt_addendum=prompt_addendum,
    )


def run_claude(
    prompt: str,
    model_flag: str,
    allowed_tools: list[str],
    project_path: Path,
    config: dict,
    mcp_config_path: Path | None = None,
    debug_log_path: Path | None = None,
) -> dict:
    max_turns = config.get("max_turns", 40)
    timeout = config.get("timeout_seconds", 600)

    claude_bin = _find_claude_cmd()
    cmd = [
        claude_bin, "-p",
        "--model", model_flag,
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(max_turns),
        "--dangerously-skip-permissions",
        # Load project-scoped settings (.claude/settings.json) in the temp
        # project copy so winkers hooks (UserPromptSubmit, PreToolUse,
        # PostToolUse, Stop) are picked up in headless mode.
        "--setting-sources", "project,local",
    ]
    # Hook forensics: --debug hooks → stderr, but stream-json swallows it
    # (verified empty _stderr). --debug-file writes the full lifecycle to
    # disk including hook matcher counts per event.
    if debug_log_path is not None:
        # Resolve to absolute: claude runs with cwd=project_path (temp),
        # so a relative path lands inside the temp dir and gets wiped
        # along with it during cleanup.
        debug_log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--debug-file", str(debug_log_path.resolve())])
    else:
        cmd.extend(["--debug", "hooks"])
    if mcp_config_path and mcp_config_path.exists():
        cmd.extend(["--mcp-config", str(mcp_config_path.resolve())])

    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=project_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            stdout_data, stderr_data = proc.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            return {"error": "claude timed out", "exit_code": 1}
        result = type("R", (), {
            "stdout": stdout_data, "stderr": stderr_data,
            "returncode": proc.returncode,
        })()
    except Exception as e:
        return {"error": f"claude failed: {e}", "exit_code": 1}

    if result.returncode != 0 and not result.stdout.strip():
        return {"error": result.stderr, "exit_code": result.returncode}

    # Parse stream-json: multiple JSON lines, last "result" line is the summary
    stdout = result.stdout.strip()
    events = []
    output = {}
    for line in stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
            events.append(evt)
            if evt.get("type") == "result":
                output = evt
        except json.JSONDecodeError:
            continue

    if not output:
        output = {"raw_stdout": result.stdout, "raw_stderr": result.stderr}

    # Store events separately to avoid circular reference
    non_result_events = [e for e in events if e.get("type") != "result"]
    output["_events"] = non_result_events
    output["tool_calls"] = _extract_tool_calls_from_events(events)
    output["exit_code"] = result.returncode
    # Always preserve stderr so --debug hooks lifecycle is inspectable later.
    output["_stderr"] = result.stderr or ""
    return output


def extract_token_usage(claude_output: dict) -> tuple[int, int, float]:
    usage = claude_output.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_creation = usage.get("cache_creation_input_tokens", 0)
    input_tokens += cache_read + cache_creation

    if not input_tokens:
        model_usage = claude_output.get("modelUsage", {})
        for model_data in model_usage.values():
            input_tokens += model_data.get("inputTokens", 0)
            input_tokens += model_data.get("cacheReadInputTokens", 0)
            input_tokens += model_data.get("cacheCreationInputTokens", 0)
            output_tokens += model_data.get("outputTokens", 0)

    cost = claude_output.get("total_cost_usd", 0.0)

    return input_tokens, output_tokens, cost


def _extract_tool_calls_from_events(events: list[dict]) -> list[dict]:
    calls = []
    for evt in events:
        if evt.get("type") != "assistant":
            continue
        msg = evt.get("message", {})
        for content in msg.get("content", []):
            if content.get("type") == "tool_use":
                calls.append({
                    "name": content.get("name", ""),
                    "id": content.get("id", ""),
                    "input": content.get("input", {}),
                })
    return calls


def extract_tool_calls(claude_output: dict) -> list[dict]:
    if "tool_calls" in claude_output:
        return claude_output["tool_calls"]
    calls = []
    messages = claude_output.get("messages", [])
    for msg in messages:
        if msg.get("role") == "assistant":
            for content in msg.get("content", []):
                if content.get("type") == "tool_use":
                    calls.append({
                        "name": content.get("name", ""),
                        "input": content.get("input", {}),
                    })
    return calls


def run_single(
    task_id: str,
    adapter_id: str,
    model_id: str,
    project_source: Path,
    config: dict | None = None,
    results_dir: Path | None = None,
) -> RunResult:
    if config is None:
        config = load_config()
    if results_dir is None:
        results_dir = Path("results")

    task = get_task(task_id)
    adapter = get_adapter(adapter_id)
    model = get_model(model_id)
    config_label = f"{model_id}-{adapter_id}"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    run_dir = results_dir / f"{timestamp}_{task_id}_{config_label}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Adapters with fixed workdir (e.g. CBM) use a persistent path
    # instead of temp to preserve pre-built index
    use_fixed = getattr(adapter, "uses_fixed_workdir", False)
    if use_fixed:
        fixed_dir = adapter.get_fixed_workdir()
        if fixed_dir.exists():
            _reset_fixed_workdir(fixed_dir, project_source)
        else:
            copy_project(project_source, fixed_dir)
        project_path = fixed_dir
        tmpdir = None
    else:
        tmpdir = tempfile.mkdtemp(prefix="gmb_")
        project_path = copy_project(project_source, Path(tmpdir) / "project")

    try:
        adapter.init_graph(project_path)
    except Exception as e:
        if tmpdir:
            _cleanup_tmpdir(tmpdir)
        return RunResult(
            task_id=task_id,
            adapter_id=adapter_id,
            model_id=model_id,
            config_label=config_label,
            timestamp=timestamp,
            tests_before=TestResult(0, 0, 0, 0, 1, ""),
            tests_after=TestResult(0, 0, 0, 0, 1, ""),
            claude_output={},
            wall_time_seconds=0,
            error=f"Graph init failed: {e}",
        )

    write_mcp_json(project_path, adapter.get_mcp_json())
    mcp_json_path = project_path / ".mcp.json"

    _git_init_snapshot(project_path)

    tests_before = run_pytest(project_path, config)

    prompt = build_prompt(task, adapter.get_prompt_addendum(), config)
    allowed_tools = adapter.get_allowed_tools()

    start_time = time.time()
    claude_output = run_claude(
        prompt=prompt,
        model_flag=model["flag"],
        allowed_tools=allowed_tools,
        project_path=project_path,
        config=config,
        mcp_config_path=mcp_json_path if mcp_json_path.exists() else None,
        debug_log_path=run_dir / "claude_debug.log",
    )
    wall_time = time.time() - start_time

    tests_after = run_pytest(project_path, config)

    input_tokens, output_tokens, actual_cost = extract_token_usage(claude_output)
    tool_calls = extract_tool_calls(claude_output)
    num_turns = claude_output.get("num_turns", 0)

    git_diff = _capture_git_diff(project_path)

    _save_run_artifacts(
        run_dir, claude_output, tests_before, tests_after,
        git_diff, prompt, config_label, task,
    )

    _kill_mcp_processes()
    # Give adapter a chance to preserve its own traces (winkers sessions etc.)
    # into run_dir BEFORE we wipe the temp workdir.
    try:
        adapter.save_artifacts(project_path, run_dir)
    except Exception:
        pass
    adapter.teardown(project_path)
    if tmpdir:
        _cleanup_tmpdir(tmpdir)

    return RunResult(
        task_id=task_id,
        adapter_id=adapter_id,
        model_id=model_id,
        config_label=config_label,
        timestamp=timestamp,
        tests_before=tests_before,
        tests_after=tests_after,
        claude_output=claude_output,
        wall_time_seconds=wall_time,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=tool_calls,
        exit_code=claude_output.get("exit_code", 0),
        error=claude_output.get("error"),
        actual_cost=actual_cost,
        num_turns=num_turns,
    )


def _reset_fixed_workdir(workdir: Path, source: Path) -> None:
    """Reset fixed workdir to match source, preserving .git"""
    try:
        # If git repo exists, use git to reset
        result = subprocess.run(
            ["git", "status"], cwd=workdir,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            subprocess.run(
                ["git", "checkout", "."], cwd=workdir,
                capture_output=True, text=True, timeout=30,
            )
            subprocess.run(
                ["git", "clean", "-fd"], cwd=workdir,
                capture_output=True, text=True, timeout=30,
            )
            return
    except Exception:
        pass
    # Fallback: remove and re-copy
    shutil.rmtree(workdir, ignore_errors=True)
    copy_project(source, workdir)


def _cleanup_tmpdir(tmpdir: str) -> None:
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


def _kill_mcp_processes() -> None:
    mcp_names = ["winkers", "cgc", "codebase-memory-mcp"]
    if sys.platform == "win32":
        for name in mcp_names:
            try:
                subprocess.run(
                    ["cmd.exe", "/c", "taskkill", "/IM", f"{name}.exe", "/F"],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
        try:
            subprocess.run(
                ["cmd.exe", "/c", "taskkill", "/IM", "node.exe", "/F"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
    else:
        for name in mcp_names:
            try:
                subprocess.run(
                    ["pkill", "-f", name],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
    time.sleep(2)


def _parse_pytest_output(output: str) -> tuple[int, int, int]:
    passed = failed = errors = 0
    for line in output.split("\n"):
        line = line.strip()
        if "passed" in line or "failed" in line or "error" in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if part == "passed" and i > 0:
                    try:
                        passed = int(parts[i - 1])
                    except ValueError:
                        pass
                elif part == "failed" and i > 0:
                    try:
                        failed = int(parts[i - 1])
                    except ValueError:
                        pass
                elif part in ("error", "errors") and i > 0:
                    try:
                        errors = int(parts[i - 1])
                    except ValueError:
                        pass
    return passed, failed, errors


def _detect_project_name(project_path: Path) -> str:
    name = project_path.name
    if name == "project":
        name = project_path.parent.name
    return name


def _git_init_snapshot(project_path: Path) -> None:
    """Snapshot the temp project so subsequent diffs are attributable to the agent.

    Writes a .gitignore first so pytest/winkers-generated bytecode and cache
    files don't pollute the diff. Previously runs produced 90+ spurious .pyc
    entries that swamped the signal.
    """
    try:
        # Ignore noise that's created during test execution, not by the agent.
        gitignore = project_path / ".gitignore"
        ignore_rules = "\n".join([
            "__pycache__/",
            "*.pyc",
            "*.pyo",
            ".pytest_cache/",
            ".mypy_cache/",
            ".ruff_cache/",
            "*.egg-info/",
            ".winkers/history/",
            ".winkers/sessions/",
            ".winkers/mcp.log",
            "",
        ])
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        if "__pycache__/" not in existing:
            gitignore.write_text(existing + ignore_rules, encoding="utf-8")
        subprocess.run(
            ["git", "init"], cwd=project_path,
            capture_output=True, text=True, timeout=10,
        )
        subprocess.run(
            ["git", "add", "-A"], cwd=project_path,
            capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            ["git", "-c", "user.name=gmb", "-c", "user.email=gmb@bench",
             "commit", "-m", "baseline"],
            cwd=project_path,
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        pass


def _capture_git_diff(project_path: Path) -> str:
    """Capture a full diff of agent edits, INCLUDING new files.

    Plain `git diff` only compares worktree vs index and misses untracked files
    (the agent's Write calls create untracked files until we stage them).
    Staging everything first via `git add -A` then diffing HEAD captures
    modifications, new files, renames, and deletions alike. Without this,
    runs like CBM-I1 (which created `app/domain/late_fee.py`) looked like
    they had fewer files than they actually did.
    """
    try:
        subprocess.run(
            ["git", "add", "-A"], cwd=project_path,
            capture_output=True, text=True, timeout=30,
        )
        result = subprocess.run(
            ["git", "diff", "--cached", "HEAD"], cwd=project_path,
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout
    except Exception:
        return ""


def _save_run_artifacts(
    run_dir: Path, claude_output: dict, tests_before: TestResult,
    tests_after: TestResult, git_diff: str, prompt: str,
    config_label: str, task: Task,
) -> None:
    with open(run_dir / "claude_output.json", "w", encoding="utf-8") as f:
        json.dump(claude_output, f, indent=2, default=str)

    with open(run_dir / "tests_before.txt", "w", encoding="utf-8") as f:
        f.write(tests_before.output)

    with open(run_dir / "tests_after.txt", "w", encoding="utf-8") as f:
        f.write(tests_after.output)

    with open(run_dir / "diff.patch", "w", encoding="utf-8") as f:
        f.write(git_diff)

    with open(run_dir / "prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt)

    tool_calls = claude_output.get("tool_calls", [])
    tool_stats = {}
    for tc in tool_calls:
        name = tc.get("name", "unknown")
        tool_stats[name] = tool_stats.get(name, 0) + 1

    summary = {
        "config": config_label,
        "task_id": task.id,
        "task_title": task.title,
        "task_category": task.category,
        "tests_before_passed": tests_before.passed,
        "tests_after_passed": tests_after.passed,
        "tests_after_failed": tests_after.failed,
        "cost_usd": claude_output.get("total_cost_usd", 0),
        "num_turns": claude_output.get("num_turns", 0),
        "duration_ms": claude_output.get("duration_ms", 0),
        "session_id": claude_output.get("session_id", ""),
        "total_tool_calls": len(tool_calls),
        "tool_stats": tool_stats,
    }
    with open(run_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
