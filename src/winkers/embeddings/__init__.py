"""Local-only embeddings via BGE-M3 (sentence-transformers).

Multilingual (1024-dim) — handles Russian/English/code domains without
an external API. Hardcoded model — alternatives discussed in Phase 0
spike were rejected: cloud APIs need a key, and lighter models lose
quality on domain-specific Russian queries (verified on T-FC3170-style
tickets).

The builder is incremental: each unit's `embed_text` is hashed and
re-embedded only when the hash changes. This makes a full project
re-init cheap on subsequent runs.
"""

from winkers.embeddings.builder import (
    DIMENSION,
    INDEX_FILENAME,
    MODEL_NAME,
    EmbeddingIndex,
    embed_units,
    load_index,
    save_index,
    search,
)

__all__ = [
    "DIMENSION",
    "EmbeddingIndex",
    "INDEX_FILENAME",
    "MODEL_NAME",
    "embed_units",
    "load_index",
    "save_index",
    "search",
]
