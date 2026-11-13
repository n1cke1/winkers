from adapters.baseline import BaselineAdapter
from adapters.cbm import CodebaseMemoryAdapter
from adapters.cgc import CodeGraphContextAdapter
from adapters.gfy import GraphifyAdapter
from adapters.wnk import WinkersAdapter

ADAPTERS = {
    "BL": BaselineAdapter,
    "CGC": CodeGraphContextAdapter,
    "CBM": CodebaseMemoryAdapter,
    "GFY": GraphifyAdapter,
    "WNK": WinkersAdapter,
}

MODELS = {
    "H": {"id": "claude-haiku-4-5", "flag": "claude-haiku-4-5", "input_price": 0.80, "output_price": 4.00},
    "S": {"id": "claude-sonnet-4-6", "flag": "claude-sonnet-4-6", "input_price": 3.00, "output_price": 15.00},
    "O": {"id": "claude-opus-4-7", "flag": "claude-opus-4-7", "input_price": 15.00, "output_price": 75.00},
}


def get_adapter(short_id: str):
    cls = ADAPTERS.get(short_id)
    if cls is None:
        raise ValueError(f"Unknown adapter: {short_id}. Available: {list(ADAPTERS.keys())}")
    return cls()


def get_model(short_id: str) -> dict:
    model = MODELS.get(short_id)
    if model is None:
        raise ValueError(f"Unknown model: {short_id}. Available: {list(MODELS.keys())}")
    return model
