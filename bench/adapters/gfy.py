import json
import shutil
import subprocess
import sys
from pathlib import Path

from adapters.base import AbstractGraphAdapter


_HOOK_SCRIPT = Path(__file__).parent / "gfy_hook_pretool.py"


# Copied verbatim from graphify/__main__.py:_CLAUDE_MD_SECTION as of 0.4.31.
# Upstream replaced the antigravity MCP-tools rule with a CLI-tools rule
# (`graphify query/path/explain` via Bash subprocess) — the design shift
# acknowledges that Sonnet on Claude Code reaches for Bash + Read more
# readily than for MCP tools. Our adapter now ships graphify-as-designed.
_CLAUDE_MD_SECTION = """\
## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `python -m graphify query "<question>"`, `python -m graphify path "<A>" "<B>"`, or `python -m graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `python -m graphify update .` to keep the graph current (AST-only, no API cost)
"""

_CLAUDE_MD_MARKER = "## graphify"

# PreToolUse hook — standalone script at gfy_hook_pretool.py.
# Docs clarify why we couldn't see hook output in claude_output.json:
# "For most events, stdout is written to the debug log but not shown in
#  the transcript. The exceptions are UserPromptSubmit and SessionStart,
#  where stdout is added as context that Claude can see and act on."
# i.e. stream-json output format does NOT surface PreToolUse hook content
# as a visible event. But the `hookSpecificOutput.additionalContext`
# field IS parsed and injected into the agent's context. Empirical
# confirmation: cost rose $0.33→$0.45 when we enabled the hook on I8
# (~35% token increase) even though no hook text appears in the log.
# The agent sees the nudge but may choose not to act on it for simple
# tasks (grep is enough for I8 email consolidation).
_SETTINGS_HOOK = {
    "matcher": "Glob|Grep",
    "hooks": [
        {
            "type": "command",
            "command": f'"{sys.executable}" "{_HOOK_SCRIPT}"',
        }
    ],
}


class GraphifyAdapter(AbstractGraphAdapter):
    name = "Graphify"
    short_id = "GFY"
    version = "unknown"

    def install(self) -> None:
        # Verify graphify (the package is `graphifyy` on PyPI; module is
        # `graphify`) and the optional `mcp` extra are importable in the
        # bench Python. Installed editable from c:/Development/MCP graph
        # bench/graphify with `pip install --user -e .[mcp]`.
        check = subprocess.run(
            [sys.executable, "-c",
             "import importlib.metadata as m, graphify, mcp; "
             "print(m.version('graphifyy'))"],
            capture_output=True, text=True, timeout=15,
        )
        if check.returncode != 0:
            raise RuntimeError(
                "graphify or mcp not importable in bench Python "
                f"({sys.executable}). Install: "
                "pip install --user -e <repo>/graphify[mcp]\n"
                f"stderr: {check.stderr}"
            )
        self.version = check.stdout.strip()

    def init_graph(self, project_path: Path) -> None:
        # Strip winkers state that leaks from the source invoicekit copy —
        # both .claude/settings.json (hook registrations, fail on non-winkers
        # runs and silently abort Claude Code) AND CLAUDE.md (contains a ~50-
        # line winkers-snippet with "First call — before any Read, Grep,
        # Bash, or Agent" directive pointing at tools the GFY adapter has
        # NOT registered in allowed_tools). Without this wipe the agent
        # sees winkers instructions it cannot follow, and graphify's own
        # rules get pushed below that noise. Same pattern as wnk.py:38-39.
        settings = project_path / ".claude" / "settings.json"
        if settings.exists():
            settings.unlink()
        stale_claude_md = project_path / "CLAUDE.md"
        if stale_claude_md.exists():
            stale_claude_md.unlink()

        # Build the knowledge graph deterministically (AST-only, no LLM),
        # then also generate the wiki (index.md + community/god-node
        # articles). The wiki is the "agent-crawlable" artefact referenced
        # by CLAUDE.md rule 2 — skill.md normally only generates it on
        # `/graphify --wiki`, but we ship it by default so rule 2 is live.
        #
        # Pipeline: tree-sitter extract → networkx → Leiden cluster →
        # analyze → graph.json + GRAPH_REPORT.md (via _rebuild_code),
        # then to_wiki(G, communities, gods, cohesion) → wiki/.
        # Total on invoicekit (~100 files): ~3s.
        build_script = (
            "from pathlib import Path\n"
            "import json\n"
            "from networkx.readwrite import json_graph\n"
            "from graphify.watch import _rebuild_code\n"
            "from graphify.wiki import to_wiki\n"
            "from graphify.analyze import god_nodes\n"
            "from graphify.cluster import score_all\n"
            f"root = Path(r'{project_path}')\n"
            "if not _rebuild_code(root):\n"
            "    raise SystemExit(1)\n"
            "# Reconstruct in-memory graph + communities from graph.json\n"
            "data = json.loads((root/'graphify-out'/'graph.json').read_text(encoding='utf-8'))\n"
            "try:\n"
            "    G = json_graph.node_link_graph(data, edges='links')\n"
            "except TypeError:\n"
            "    G = json_graph.node_link_graph(data)\n"
            "communities = {}\n"
            "for nid, d in G.nodes(data=True):\n"
            "    cid = d.get('community')\n"
            "    if cid is not None:\n"
            "        communities.setdefault(int(cid), []).append(nid)\n"
            "cohesion = score_all(G, communities)\n"
            "gods = god_nodes(G)\n"
            "n = to_wiki(G, communities, root/'graphify-out'/'wiki',\n"
            "            cohesion=cohesion, god_nodes_data=gods)\n"
            "print(f'wiki: index.md + {n} articles')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", build_script],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"graphify build failed: {result.stderr or result.stdout}"
            )

        graph_json = project_path / "graphify-out" / "graph.json"
        if not graph_json.exists():
            raise RuntimeError(
                f"graphify build claimed success but {graph_json} missing"
            )
        wiki_index = project_path / "graphify-out" / "wiki" / "index.md"
        if not wiki_index.exists():
            raise RuntimeError(
                f"graphify wiki build missing {wiki_index}"
            )

        # CLAUDE.md section + PreToolUse hook (re-enabled after docs
        # clarification — hook content doesn't appear in stream-json but
        # IS injected into agent context, cost metric confirms it).
        self._write_claude_md_section(project_path)
        self._write_pretool_hook(project_path)

    def get_mcp_json(self) -> dict:
        # MCP stdio server reads graph.json once at startup (in-memory
        # NetworkX graph). Use sys.executable to pin the exact Python
        # where graphify is installed — Claude Code's subprocess env
        # may not have the same `python` on PATH.
        # NOTE: graph_path is interpreted by the MCP server relative to
        # the project_path (Claude Code runs the server with cwd=project),
        # so a relative path is correct here.
        return {
            "mcpServers": {
                "graphify": {
                    "command": sys.executable,
                    "args": ["-m", "graphify.serve",
                             "graphify-out/graph.json"],
                    "type": "stdio",
                }
            }
        }

    def get_allowed_tools(self) -> list[str]:
        return [
            "mcp__graphify__query_graph",
            "mcp__graphify__get_node",
            "mcp__graphify__get_neighbors",
            "mcp__graphify__get_community",
            "mcp__graphify__god_nodes",
            "mcp__graphify__graph_stats",
            "mcp__graphify__shortest_path",
        ]

    def get_prompt_addendum(self) -> str:
        return (
            "Graphify MCP is connected — the project has been pre-indexed into\n"
            "a NetworkX knowledge graph at graphify-out/graph.json with Leiden\n"
            "community detection. Tools to navigate it:\n"
            "- mcp__graphify__graph_stats: node/edge counts + EXTRACTED/INFERRED/AMBIGUOUS confidence breakdown\n"
            "- mcp__graphify__god_nodes: top-N most-connected nodes (core abstractions)\n"
            "- mcp__graphify__query_graph: BFS/DFS traversal seeded by a question, with token_budget\n"
            "- mcp__graphify__get_node: full details (source_file, line, community, degree) for one node\n"
            "- mcp__graphify__get_neighbors: direct neighbours with relation type and confidence\n"
            "- mcp__graphify__shortest_path: hop-trace between two concepts\n"
            "- mcp__graphify__get_community: all nodes in a community\n"
            "\n"
            "Edge confidence: EXTRACTED = explicit (import, direct call) — trust;\n"
            "INFERRED = second-pass deduction — verify with Read/Grep before relying;\n"
            "AMBIGUOUS = flagged uncertain — treat as a hint only.\n"
            "\n"
            "Read graphify-out/GRAPH_REPORT.md for an overview before deep navigation."
        )

    @staticmethod
    def _write_claude_md_section(project_path: Path) -> None:
        """Append the graphify CLAUDE.md block. Idempotent via marker check.

        If an existing CLAUDE.md is present, append; if not, create.
        Same logic as graphify/__main__.py:claude_install.
        """
        target = project_path / "CLAUDE.md"
        if target.exists():
            content = target.read_text(encoding="utf-8")
            if _CLAUDE_MD_MARKER in content:
                return  # already present
            new_content = content.rstrip() + "\n\n" + _CLAUDE_MD_SECTION
        else:
            new_content = _CLAUDE_MD_SECTION
        target.write_text(new_content, encoding="utf-8")

    @staticmethod
    def _write_pretool_hook(project_path: Path) -> None:
        """Write the PreToolUse hook spec to .claude/settings.json."""
        settings_path = project_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings = {"hooks": {"PreToolUse": [_SETTINGS_HOOK]}}
        settings_path.write_text(
            json.dumps(settings, indent=2), encoding="utf-8"
        )

    def save_artifacts(self, project_path: Path, run_dir: Path) -> None:
        """Preserve graph.json + GRAPH_REPORT.md + wiki/ so the run can be analysed later."""
        src = project_path / "graphify-out"
        if not src.exists():
            return
        dst = run_dir / "graphify"
        dst.mkdir(parents=True, exist_ok=True)
        for name in ("graph.json", "GRAPH_REPORT.md"):
            s = src / name
            if s.exists():
                try:
                    shutil.copy2(s, dst / name)
                except Exception:
                    pass
        wiki_src = src / "wiki"
        if wiki_src.exists():
            try:
                shutil.copytree(wiki_src, dst / "wiki", dirs_exist_ok=True)
            except Exception:
                pass

    def teardown(self, project_path: Path) -> None:
        out = project_path / "graphify-out"
        if out.exists():
            shutil.rmtree(out, ignore_errors=True)
