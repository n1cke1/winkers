"""Commit binding, debt delta computation, and session scoring."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from winkers.git import AUTO_COMMIT_MSG_MARKER, run_git
from winkers.models import (
    CommitBinding,
    DebtDelta,
    Graph,
    ScoredSession,
    SessionRecord,
)


def score_session(
    session: SessionRecord, project_path: Path,
    graph_before: Graph | None, graph_after: Graph | None,
) -> ScoredSession:
    """Full scoring pipeline: commit binding + debt delta + score."""
    commit = bind_to_commit(session, project_path)
    if commit.hash and graph_after:
        commit.modified_functions = _find_modified_functions(
            project_path, commit.hash, graph_after,
        )
    debt = compute_debt_delta(session, graph_before, graph_after)
    sc = estimate_score(session, commit, debt)
    return ScoredSession(
        session=session, commit=commit, debt=debt, score=sc,
    )


# ---------------------------------------------------------------------------
# Commit binding
# ---------------------------------------------------------------------------

def bind_to_commit(session: SessionRecord, project_path: Path) -> CommitBinding:
    """Find git commits made during the session window."""
    try:
        start = datetime.fromisoformat(session.started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(session.completed_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return CommitBinding(status="uncommitted")

    after = (start - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    before = (end + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    stdout = run_git(
        ["log", f"--after={after}", f"--before={before}",
         "--format=%H|%s", "--no-merges"],
        cwd=project_path,
    )
    if not stdout or not stdout.strip():
        return CommitBinding(status="uncommitted")

    # Prefer a meaningful commit over auto-commits (wip: auto-commit ...)
    lines = stdout.strip().splitlines()
    first_line = next(
        (line for line in lines if AUTO_COMMIT_MSG_MARKER not in line.split("|", 1)[-1]),
        lines[0],  # all auto-commits — still better than "uncommitted"
    )
    parts = first_line.split("|", 1)
    commit_hash = parts[0]
    commit_msg = parts[1] if len(parts) > 1 else ""

    # Get diff stats
    files_changed, insertions, deletions = _diff_stat(project_path, commit_hash)

    # Check if commit was reverted
    status = "committed"
    if _is_reverted(project_path, commit_hash):
        status = "reverted"

    return CommitBinding(
        status=status,
        hash=commit_hash,
        message=commit_msg,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
    )


def _diff_stat(project_path: Path, commit_hash: str) -> tuple[list[str], int, int]:
    """Get files changed, insertions, deletions for a commit."""
    stdout = run_git(
        ["diff", "--stat", "--numstat", f"{commit_hash}~1", commit_hash],
        cwd=project_path,
    )
    if not stdout:
        return [], 0, 0

    files: list[str] = []
    total_ins = 0
    total_del = 0
    for line in stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            try:
                ins = int(parts[0]) if parts[0] != "-" else 0
                dels = int(parts[1]) if parts[1] != "-" else 0
                total_ins += ins
                total_del += dels
                files.append(parts[2])
            except ValueError:
                continue
    return files, total_ins, total_del


def _is_reverted(project_path: Path, commit_hash: str) -> bool:
    """Check if a commit was later reverted (simple heuristic)."""
    stdout = run_git(
        ["log", "--oneline", "--grep", f"Revert.*{commit_hash[:7]}", "-1"],
        cwd=project_path,
    )
    return bool(stdout and stdout.strip())


def _find_modified_functions(
    project_path: Path, commit_hash: str, graph: Graph,
) -> list[str]:
    """Find function IDs whose line ranges overlap with changed lines."""
    stdout = run_git(
        ["diff", "--unified=0", f"{commit_hash}~1", commit_hash],
        cwd=project_path,
    )
    if not stdout:
        return []

    # Parse unified diff to get changed lines per file
    changed: dict[str, set[int]] = {}
    current_file = ""
    for line in stdout.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("@@ ") and current_file:
            # @@ -old,count +new,count @@
            parts = line.split(" ")
            for part in parts:
                if part.startswith("+") and "," in part:
                    start, count = part[1:].split(",", 1)
                    try:
                        s, c = int(start), int(count)
                        lines = set(range(s, s + c))
                        changed.setdefault(current_file, set()).update(lines)
                    except ValueError:
                        pass
                elif part.startswith("+") and part[1:].isdigit():
                    s = int(part[1:])
                    changed.setdefault(current_file, set()).add(s)

    # Cross-reference with graph functions
    modified_fns: list[str] = []
    for fn in graph.functions.values():
        file_norm = fn.file.replace("\\", "/")
        if file_norm not in changed:
            continue
        fn_lines = set(range(fn.line_start, fn.line_end + 1))
        if fn_lines & changed[file_norm]:
            modified_fns.append(fn.id)

    return modified_fns


# ---------------------------------------------------------------------------
# Debt delta
# ---------------------------------------------------------------------------

def compute_debt_delta(
    session: SessionRecord,
    graph_before: Graph | None,
    graph_after: Graph | None,
) -> DebtDelta:
    """Compare graph before/after session to compute debt changes."""
    if not graph_before or not graph_after:
        return DebtDelta(
            files_created=len(session.files_created),
            files_modified=len(session.files_modified),
        )

    modified_files = set(session.files_modified) | set(session.files_created)

    # Complexity delta for touched functions
    complexity_before = _sum_complexity(graph_before, modified_files)
    complexity_after = _sum_complexity(graph_after, modified_files)

    # Max function lines after
    max_lines = _max_function_lines(graph_after, modified_files)

    # Biggest file growth
    biggest_growth = _biggest_file_growth(graph_before, graph_after, modified_files)

    # Import edges delta
    edges_before = len(graph_before.import_edges)
    edges_after = len(graph_after.import_edges)

    return DebtDelta(
        complexity_delta=complexity_after - complexity_before,
        max_function_lines=max_lines,
        biggest_file_growth=biggest_growth,
        import_edges_delta=edges_after - edges_before,
        files_created=len(session.files_created),
        files_modified=len(session.files_modified),
    )


def _sum_complexity(graph: Graph, files: set[str]) -> int:
    total = 0
    for fn in graph.functions.values():
        if _normalize(fn.file) in files or not files:
            total += fn.complexity
    return total


def _max_function_lines(graph: Graph, files: set[str]) -> int:
    max_lines = 0
    for fn in graph.functions.values():
        if _normalize(fn.file) in files:
            max_lines = max(max_lines, fn.lines)
    return max_lines


def _biggest_file_growth(
    before: Graph, after: Graph, files: set[str],
) -> int:
    biggest = 0
    for path in files:
        old = before.files.get(path)
        new = after.files.get(path)
        old_loc = old.lines_of_code if old else 0
        new_loc = new.lines_of_code if new else 0
        growth = new_loc - old_loc
        biggest = max(biggest, growth)
    return biggest


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


# ---------------------------------------------------------------------------
# Session scoring
# ---------------------------------------------------------------------------

SCORE_LABELS = {
    "good": (0.8, 1.0),
    "ok": (0.6, 0.8),
    "weak": (0.4, 0.6),
    "poor": (0.0, 0.4),
}


def score_label(value: float) -> str:
    """Return human-readable label for a score value."""
    if value >= 0.8:
        return "good"
    if value >= 0.6:
        return "ok"
    if value >= 0.4:
        return "weak"
    return "poor"


def estimate_score(
    session: SessionRecord, commit: CommitBinding, debt: DebtDelta,
) -> float:
    """Estimate session effectiveness score (0.0-1.0)."""
    score = 0.5

    # Git signals
    if commit.status == "committed":
        score += 0.2
    if commit.status == "reverted":
        score -= 0.3

    # Test signals
    if session.tests_passed is True:
        score += 0.15
    if session.tests_passed is False:
        score -= 0.25

    # User signals
    if session.session_end == "user_killed":
        score -= 0.15
    if len(session.user_corrections) > 1:
        score -= 0.15

    # Technical debt — heaviest weight
    if debt.complexity_delta <= 0:
        score += 0.15
    if debt.complexity_delta > 10:
        score -= 0.2
    if debt.complexity_delta > 20:
        score -= 0.2  # cumulative -0.4

    if debt.max_function_lines > 100:
        score -= 0.15
    if debt.biggest_file_growth > 150:
        score -= 0.15

    # Modular + clean
    if debt.files_created > 0 and debt.complexity_delta <= 5:
        score += 0.15

    # Coupling growth
    if debt.import_edges_delta > 5:
        score -= 0.1
    if debt.import_edges_delta <= 0:
        score += 0.05

    return max(0.0, min(1.0, score))
