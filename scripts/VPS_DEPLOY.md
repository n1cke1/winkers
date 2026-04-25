# VPS deployment of locally-built Winkers units

The expensive part of `winkers init --with-units` (~30 min of `claude --print`
subprocesses + embedding compute) ran locally. Resulting artifacts are fully
portable: relative paths, no machine-specific state.

## Files

```
scripts/winkers_artifacts.tar.gz   1.8 MB
├── .winkers/units.json            429 units (320 fn + 11 templates + 98 couplings)
├── .winkers/embeddings.npz        BGE-M3 vectors (1024-dim × 429)
└── .winkers/graph.json            structure + ast_hashes
```

## Pre-flight on VPS

```bash
# 1. Verify CHP source is identical to local (same git commit)
cd /path/to/CHP\ model\ web && git rev-parse HEAD     # compare to local

# 2. Verify Winkers ≥ this version is installed (with --with-units flag)
winkers --version

# 3. Verify claude CLI is authenticated for the deploy user
echo "ping" | claude --print --allowedTools ""        # should return non-empty
```

## Deploy

```bash
# Locally: copy
scp scripts/winkers_artifacts.tar.gz user@vps:/tmp/

# On VPS:
cd /path/to/CHP\ model\ web
tar -xzvf /tmp/winkers_artifacts.tar.gz                # extracts .winkers/

# Sanity: paths must be relative; no Windows backslashes after extract
grep '"id"' .winkers/units.json | head -3
# Should show: "engine/chp_model.py::solve_design", "templates/index.html#…"
# Must NOT show: "C:\\..." or "/c/Development/..."
```

## Verify on VPS

```bash
# This should be a NO-OP if source matches local — every unit's hash
# matches, every embedding cached. Completes in ~5s.
winkers init --with-units --no-semantic --no-llm
# Expected output:
#   0 function unit(s) need description, 0 template section(s) need description
#   couplings: ~98 cluster(s); embeddings: ~429 reused, 0 encoded
```

If the VPS source has diverged (e.g. recent commits not in local), the
mismatched files will be re-described. The rest stays cached. That's
the safe, incremental behaviour.

## Runtime: BGE-M3 model on VPS

The MCP tool `find_work_area` needs the BGE-M3 model loaded. On first
invocation it downloads ~2.3 GB from Hugging Face (~3-5 min on a
typical VPS, longer if HF rate-limits unauthenticated requests).

To pre-download (recommended):

```bash
# On VPS, before first MCP call:
python -c "from sentence_transformers import SentenceTransformer; \
           SentenceTransformer('BAAI/bge-m3')"
```

This caches under `~/.cache/huggingface/hub/`. Subsequent loads are
~10s warm.

Alternative: copy the cache from local:

```bash
# Locally:
tar -czf bge-m3-cache.tar.gz -C ~/.cache/huggingface/hub models--BAAI--bge-m3
# ~2.3 GB tar — only worth the bandwidth if VPS network to HF is slow.
scp bge-m3-cache.tar.gz user@vps:/tmp/

# On VPS:
mkdir -p ~/.cache/huggingface/hub
tar -xzf /tmp/bge-m3-cache.tar.gz -C ~/.cache/huggingface/hub
```

## After deploy — Phase 5 bench

With the index live on VPS, the production bench (`ticket_service` A/B
on real tickets) can run there. Phase 5 implementation uses the
existing `_execute()` subprocess pattern.
