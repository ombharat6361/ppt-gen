import os

MODEL_NAME = os.environ.get("LEXI_MODEL", "llama-3.1-8b-instant")
MAX_HISTORY_TURNS = int(os.environ.get("LEXI_MAX_HISTORY_TURNS", "20"))
