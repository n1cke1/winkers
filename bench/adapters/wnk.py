import shutil
import subprocess
from pathlib import Path

from adapters.base import AbstractGraphAdapter
from adapters._env import winkers_env


class WinkersAdapter(AbstractGraphAdapter):
    name = "Winkers"
    short_id = "WNK"
    version = "unknown"

    def install(self) -> None:
        if not shutil.which("winkers"):
            raise RuntimeError(
                "winkers CLI not found. Install: pip install winkers"
            )
        result = subprocess.run(
            ["winkers", "--version"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            self.version = result.stdout.strip().split()[-1]

    def init_graph(self, project_path: Path) -> None:
        # Wipe any copied .claude/settings.json and CLAUDE.md so winkers
        # reinstalls hooks with absolute paths pointing at THIS working copy
        # (not the source). Dedup inside winkers is marker-based and buggy
        # across versions, so a clean start is safer than relying on it.
        claude_dir = project_path / ".claude"
        stale_settings = claude_dir / "settings.json"
        if stale_settings.exists():
            stale_settings.unlink()
        claude_dir.mkdir(parents=True, exist_ok=True)
        stale_settings.write_text("{}", encoding="utf-8")
        stale_claude_md = project_path / "CLAUDE.md"
        if stale_claude_md.exists():
            stale_claude_md.unlink()
        # --no-semantic reuses any semantic.json from the copy; --no-llm
        # skips LLM intent generation (fast, no API calls).
        result = subprocess.run(
            ["winkers", "init", "-y", "--no-semantic", "--no-llm", str(project_path)],
            capture_output=True,
            text=True,
            timeout=300,
            env=winkers_env(),
        )
        if result.returncode != 0:
            raise RuntimeError(f"winkers init failed: {result.stderr}")

        # Ensure .winkers/config.toml has an [intent] section pinning the provider.
        # If source project already has one (e.g. provider="api" with pre-generated
        # intents), keep it — that way impact_check runs fast via Haiku for the
        # handful of agent-created functions, while avoiding the default 'auto'
        # behaviour which silently picks Ollama (30s/call on CPU).
        config_path = project_path / ".winkers" / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        if "[intent]" not in existing:
            with open(config_path, "a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write('\n[intent]\nprovider = "none"\n')

        # v0.8.1 CLAUDE.md snippet already uses JSON-style `orient with
        # include: [...]` syntax + wrong_approach one-liners in rules_list.
        # Our _patch_claude_md is redundant for v0.8.1 but harmless
        # (regex won't match new format). Keep as defense-in-depth in
        # case the snippet is regenerated in old format.
        _patch_claude_md(project_path / "CLAUDE.md")

        # Strip costly SessionEnd hooks: autocommit (Haiku API) +
        # stop-audit-spawn (claude --print subscription) — both fire per
        # bench run, neither used by bench scoring.
        _strip_unwanted_hooks(stale_settings)

    def get_mcp_json(self) -> dict:
        # Align server name with winkers hooks & permissions (mcp__winkers__*).
        # Inject ANTHROPIC_API_KEY so impact_check's _generate_incremental_intents
        # can use ApiProvider (Haiku ~1s/call) instead of Ollama (~30s/call).
        # Falls back to empty string if .wnk_env is absent — winkers will then
        # see NoneProvider and skip intent generation entirely.
        api_key = winkers_env().get("ANTHROPIC_API_KEY", "")
        return {
            "mcpServers": {
                "winkers": {
                    "command": "winkers",
                    "args": ["serve"],
                    "type": "stdio",
                    "env": {"ANTHROPIC_API_KEY": api_key} if api_key else {},
                }
            }
        }

    def get_allowed_tools(self) -> list[str]:
        return [
            "mcp__winkers__orient",
            "mcp__winkers__scope",
            "mcp__winkers__convention_read",
            "mcp__winkers__rule_read",
            "mcp__winkers__before_create",
            # v0.8.2: new browse tool — mid-level inventory between
            # orient (zone-level) and scope (single-fn deep dive).
            "mcp__winkers__browse",
            "mcp__winkers__impact_check",
            # post-0.8.4 main (Phase 2): BGE-M3 semantic search MCP
            # tool. Adaptive top-K + threshold over function/section
            # descriptions. Only useful when units pipeline ran via
            # `winkers init --with-units` (we don't enable it in the
            # bench yet — keep tool exposed so the agent CAN try it
            # if it ever decides BGE search is the right move).
            "mcp__winkers__find_work_area",
            "mcp__winkers__session_done",
        ]

    def get_prompt_addendum(self) -> str:
        # Keep this short — winkers v0.8.1 CLAUDE.md snippet already spells
        # out the 4-step workflow and JSON syntax. We:
        # 1) Reinforce that before_create is required for ALL changes,
        #    not just new-function creation (destructive-refactor gap).
        # 2) Tell the agent to skip session_done in headless mode — it
        #    always returns PASS in our setup (warnings_total=0 because
        #    impact_check rules don't fire) yet costs ~$0.20-0.25 per call
        #    (60-80K input tokens of accumulated context).
        return (
            "Winkers MCP is connected (see CLAUDE.md for the full 4-step workflow).\n"
            "Reminder: before_create is required before ANY code change, including\n"
            "Edit, Write-that-replaces-a-file, refactoring locked functions, signature\n"
            "changes, or removal/renaming. Not just for brand-new functions.\n"
            "\n"
            "Do NOT call mcp__winkers__session_done in this run — it always returns PASS\n"
            "here (no actionable info) while costing ~80K input tokens per call."
        )

    def save_artifacts(self, project_path: Path, run_dir: Path) -> None:
        """Copy winkers session recordings / logs into run_dir/winkers/ so they
        survive temp-dir cleanup. Gives a second lens on the run alongside
        claude_output.json (winkers records its own view of tool calls,
        coherence warnings, session audit state, etc.).
        """
        src = project_path / ".winkers"
        if not src.exists():
            return
        dst = run_dir / "winkers"
        dst.mkdir(parents=True, exist_ok=True)

        # What to preserve — skip bulky/ephemeral artefacts like full graph.json.
        to_copy = ["sessions", "history", "insights", "mcp.log", "debt.json",
                   "config.toml", "rules"]
        for name in to_copy:
            s = src / name
            if not s.exists():
                continue
            d = dst / name
            try:
                if s.is_dir():
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)
            except Exception:
                pass

        # Also preserve .claude/settings.json so we can verify which hooks
        # were actually wired by winkers + which survived our stripping.
        settings = project_path / ".claude" / "settings.json"
        if settings.exists():
            try:
                shutil.copy2(settings, run_dir / "claude_settings.json")
            except Exception:
                pass

        # stop-audit-spawn hook (when not stripped) detaches a `claude --print`
        # subprocess to run a coherence audit (~30s) which writes to project
        # root .winkers_pending.md. Bench timing: SessionEnd fires → audit
        # spawned → bench returns immediately, runs pytest, calls save_artifacts.
        # By now the audit may still be writing. Wait briefly so we capture it.
        import os as _os, time as _time
        if _os.environ.get("WNK_KEEP_AUDIT_HOOK", "").lower() in ("1", "true"):
            pending = project_path / ".winkers_pending.md"
            for _ in range(90):  # up to ~90s
                if pending.exists():
                    break
                _time.sleep(1)
            if pending.exists():
                try:
                    shutil.copy2(pending, run_dir / "winkers_pending.md")
                except Exception:
                    pass

    def teardown(self, project_path: Path) -> None:
        winkers_dir = project_path / ".winkers"
        if winkers_dir.exists():
            shutil.rmtree(winkers_dir, ignore_errors=True)


def _strip_unwanted_hooks(settings_path: Path) -> None:
    """Remove SessionEnd autocommit + stop-audit-spawn hooks from settings.json.

    Both fire after every bench run and burn budget we don't want spent:
      - autocommit:        winkers autocommit  (Haiku API, also mutates git)
      - stop-audit-spawn:  winkers hook stop-audit-spawn  (claude --print sub)

    Preserves all other hooks (PreToolUse, PostToolUse, UserPromptSubmit,
    SessionStart, SessionEnd record). Idempotent — safe to call repeatedly.
    """
    import json
    if not settings_path.exists():
        return
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    import os as _os
    if _os.environ.get("WNK_KEEP_AUDIT_HOOK", "").lower() in ("1", "true"):
        # Diagnostic mode: keep stop-audit-spawn to inspect what
        # `.winkers_pending.md` looks like after a real bench run.
        drop_markers = ("autocommit",)
    else:
        drop_markers = ("autocommit", "stop-audit-spawn")
    hooks = data.get("hooks", {})
    se_list = hooks.get("SessionEnd", [])
    filtered: list = []
    for entry in se_list:
        kept = [h for h in entry.get("hooks", [])
                if not any(m in h.get("command", "") for m in drop_markers)]
        if kept:
            entry["hooks"] = kept
            filtered.append(entry)
    if filtered:
        hooks["SessionEnd"] = filtered
    elif "SessionEnd" in hooks:
        del hooks["SessionEnd"]
    settings_path.write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


_FIRST_CALL_OLD = "**First call.**"
_FIRST_CALL_NEW = "**First call — before any Read, Grep, Bash, or Agent.**"

# Upstream workflow step 2 mentions "or safe alternatives" as part of
# before_create output. We strip that fragment — empirically the
# safe_alternative field is generic for value-migration traps and
# pushes Sonnet toward wrong low-migration_cost paths on non-trap
# tasks (observed regression on I7).
# v0.8.2 changed wording from "with expressions, or safe alternatives" to
# "(expressions + risk), or safe alternatives". Support both historical forms.
_SAFE_ALT_MENTION_OLD = "(expressions + risk), or safe alternatives."
_SAFE_ALT_MENTION_NEW = "(expressions + risk)."
# Back-compat for older snippet wording
_SAFE_ALT_MENTION_LEGACY_OLD = "with expressions, or safe alternatives."
_SAFE_ALT_MENTION_LEGACY_NEW = "with expressions."

# Semantic summary block injected by winkers _install_semantic_summary.
# We strip it because its content (data_flow + domain_context) is
# byte-identical to what orient returns in map.data_flow and
# conventions.domain_context. Duplicating ~400 tokens in CLAUDE.md
# pushes rules_list out of the 2K orient token budget (making every
# session require a 2nd orient call) and gives the agent a "teaser"
# that sometimes substitutes for the orient call entirely.
_SEMANTIC_BLOCK_RE = r"\n*<!-- winkers-semantic-start -->.*?<!-- winkers-semantic-end -->\n*"

# Optional experiment: strip "rules_list" from the workflow step-1
# suggested include set, and demote it to the On-demand table.
# Hypothesis: pulling 20 rule one-liners into the FIRST orient response
# (with its categories like "Service-repo separation") may push the
# model toward service-layer mixin for audit tasks. Toggle via env var
# WNK_STRIP_RULES_LIST_FIRST=1 for differential runs.
_STEP1_WITH_RULES = '`orient` with `include: ["map", "conventions", "rules_list"]` — zones, hotspots, data flow, zone intents, and coding rules with `title` + `wrong_approach` one-liner per rule.'
_STEP1_NO_RULES = '`orient` with `include: ["map", "conventions"]` — zones, hotspots, data flow, zone intents.'
# Add rules_list to On-demand table (insert before "| `session_done`" line)
_ON_DEMAND_INSERT_ANCHOR = "| `session_done` | optional cross-file audit |"
_ON_DEMAND_RULES_ROW = "| `orient` with `rules_list` | full coding-rules list with wrong_approach one-liners |"

# Promote find_work_area to Step 2 (was: browse).
# Rationale: the new BGE-M3 semantic-search tool's own MCP description
# says "CALL THIS FIRST when given a natural-language task, before
# grepping or guessing", but the shipped CLAUDE.md workflow lists
# `browse` at Step 2 and never mentions find_work_area. Sonnet follows
# the structural workflow and ignores the tool-schema hint. We move
# find_work_area into the workflow as Step 2 and demote browse to the
# On-demand table (it's already there) so the agent gets a clearer
# entry point for "where do I look" questions on this task.
_STEP2_BROWSE_OLD = (
    "2. `browse` with `zone` or `file` — mid-level inventory: "
    "function list with LLM intents (`\"file::fn (callers) — intent\"`)."
    " With `file=`, caller call-sites are inlined under each fn "
    "(`\"  ← caller_file:line  expression\"`) so you see who invokes what "
    "before editing. Use to pick a target before deep-dive."
)
_STEP2_FIND_WORK_AREA = (
    "2. `find_work_area` with `query` (a 1-2 sentence natural-language "
    "task description) — BGE-M3 semantic search over function "
    "descriptions and traceability_units. Returns top-K relevant units "
    "with verdict (`OK` = top match is the place to start; "
    "`NO_CLEAR_MATCH` = likely new feature or vocabulary outside the "
    "index). Plain prose, any language. Use this to **locate** the "
    "work area before deep-dive."
)


def _patch_claude_md(claude_md: Path) -> None:
    """Tune CLAUDE.md for the benchmark:

    1. Reinforce the "First call" cue — without this Sonnet treats the
       auto-generated data_flow teaser as "enough context" and proceeds
       to Agent(Explore)/Read instead of calling orient first.

    2. Strip the "or safe alternatives" mention from the before_create
       step. We tested a dedicated rule block for safe_alternative
       earlier; it didn't change trap-task outcomes (Sonnet keeps
       ignoring the field on I5/I6) and caused a regression on I7
       (model picked low-migration_cost service-layer mixin over the
       correct middleware approach).

    3. Strip the auto-generated semantic summary block. Its content is
       a byte-identical duplicate of orient.map.data_flow +
       orient.conventions.domain_context. Removing it: (a) forces the
       agent to actually call orient to get that context, (b) frees
       ~400 tokens in orient's 2K response budget so rules_list is no
       longer truncated, (c) saves one redundant orient round-trip
       per session.

    Idempotent: all edits no-op when already applied.
    """
    if not claude_md.exists():
        return
    import re as _re
    text = claude_md.read_text(encoding="utf-8")

    if _FIRST_CALL_NEW not in text and _FIRST_CALL_OLD in text:
        text = text.replace(_FIRST_CALL_OLD, _FIRST_CALL_NEW)
    if _SAFE_ALT_MENTION_OLD in text:
        text = text.replace(_SAFE_ALT_MENTION_OLD, _SAFE_ALT_MENTION_NEW)
    if _SAFE_ALT_MENTION_LEGACY_OLD in text:
        text = text.replace(_SAFE_ALT_MENTION_LEGACY_OLD, _SAFE_ALT_MENTION_LEGACY_NEW)
    if "winkers-semantic-start" in text:
        text = _re.sub(_SEMANTIC_BLOCK_RE, "\n", text, flags=_re.DOTALL)
    # find_work_area patch DISABLED — diagnostics: any mention in CLAUDE.md
    # (replacement OR insertion) hangs claude on startup. Mystery bug.
    pass

    # Differential experiment toggle
    import os as _os
    if _os.environ.get("WNK_STRIP_RULES_LIST_FIRST", "").lower() in ("1", "true"):
        if _STEP1_WITH_RULES in text:
            text = text.replace(_STEP1_WITH_RULES, _STEP1_NO_RULES)
        if _ON_DEMAND_INSERT_ANCHOR in text and _ON_DEMAND_RULES_ROW not in text:
            text = text.replace(
                _ON_DEMAND_INSERT_ANCHOR,
                _ON_DEMAND_RULES_ROW + "\n" + _ON_DEMAND_INSERT_ANCHOR,
            )

    claude_md.write_text(text, encoding="utf-8")
