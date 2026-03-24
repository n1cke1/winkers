# Cursor Setup Guide

## Step 1 — Install Winkers

```bash
pip install git+https://github.com/n1cke1/winkers.git
```

## Step 2 — Initialize in your project

```bash
cd your-project
winkers init --cursor
```

This will:
- Build `.winkers/graph.json`
- Create `.cursor/rules/winkers.mdc` (Cursor rule file)

## Step 3 — Configure MCP in Cursor

In Cursor settings → MCP → add server:

```json
{
  "winkers": {
    "command": "winkers",
    "args": ["serve", "/path/to/your-project"],
    "type": "stdio"
  }
}
```

Or add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "winkers": {
      "command": "winkers",
      "args": ["serve"],
      "type": "stdio"
    }
  }
}
```

## Step 4 — Use in Cursor

The Cursor rule (`.cursor/rules/winkers.mdc`) will instruct the agent to use Winkers before modifying functions. Winkers tools appear in the tool panel.

## Keeping the graph fresh

```bash
winkers init          # full rebuild
winkers analyze -f X  # incremental (after editing X)
```
