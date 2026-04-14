# Intent Eval Skill

Optimize the intent generation prompt for better function search quality.

## Workflow

### Step 1 — Baseline
Run eval on a sample of functions with the current prompt:
```bash
winkers intent eval --sample 20 --json > /tmp/baseline.json
```

### Step 2 — Judge quality
Read the baseline JSON. For each function, assess:
- **Specificity**: Does the intent mention domain-specific terms?
- **Accuracy**: Does the intent correctly describe what the function does?
- **Searchability**: Would searching for this intent find the function?
- **Conciseness**: Is it one clear sentence?

Rate each intent: GOOD / OK / BAD. Note common failure patterns.

### Step 3 — Revise prompt
Based on failure patterns, draft an improved prompt template.
Common improvements:
- Add "mention the data types and domain objects"
- Add "describe the algorithm or approach used"
- Remove "do not repeat the function name" if names are informative

### Step 4 — A/B test
Run eval with the new prompt:
```bash
winkers intent eval --prompt "YOUR NEW PROMPT" --sample 20 --json > /tmp/revised.json
```

Compare: are BAD intents now GOOD? Are GOOD intents still GOOD?

### Step 5 — Apply
If the revised prompt is better, update `.winkers/config.toml`:
```toml
[intent]
prompt_template = "YOUR NEW PROMPT"
```

Then regenerate all intents:
```bash
winkers init --force
```

### Step 6 — Verify search
Test that search quality improved:
```bash
winkers search "YOUR TEST QUERY"
```

Functions with cryptic names should now appear in results thanks to better intents.
