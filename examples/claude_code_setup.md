# Claude Code Setup Guide

## Step 1 — Install Winkers

```bash
pip install git+https://github.com/n1cke1/winkers.git
```

## Step 2 — Initialize in your project

```bash
cd your-project
winkers init --claude-code
```

This will:
- Build `.winkers/graph.json`
- Install `.claude/skills/winkers/SKILL.md`
- Install `.claude/agents/winkers-advisor.yaml`
- Configure `.claude/settings.json` with the MCP server
- Append a Winkers snippet to `CLAUDE.md`

## Step 3 — Start the MCP server

```bash
winkers serve
```

Or configure it to start automatically in `.claude/settings.json`:

```json
{
  "mcpServers": {
    "winkers": {
      "command": "winkers",
      "args": ["serve", "/path/to/your-project"],
      "type": "stdio"
    }
  }
}
```

## Step 4 — Use in Claude Code

The agent now has 5 tools available: `init`, `map`, `scope`, `inspect`, `analyze`.

### Typical workflow

```
You: Modify calculate_price to support bulk discounts

Claude:
1. mcp__winkers__map(detail="zones")           → sees modules, api zones
2. mcp__winkers__scope(function="calculate_price")
   → locked=true, 2 callers (inventory, api)
   → constraints: don't change param types or return type
3. mcp__winkers__inspect(function="calculate_price")
   → reads source
4. Edits function body (safe — no signature change)
5. mcp__winkers__analyze(files=["modules/pricing.py"])
   → violations: [] (clean)
```

## Step 5 — Install git hooks (optional)

```bash
winkers init-hooks
```

- `pre-commit`: runs `winkers analyze --staged --fail-on-error`
- `post-commit`: runs `winkers snapshot` (saves graph history)

## Keeping the graph fresh

```bash
# After adding new files or functions:
winkers init

# After editing a file (faster — only reparses that file):
winkers analyze -f path/to/changed.py
```
