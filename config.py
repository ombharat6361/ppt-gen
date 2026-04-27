import os

MODEL_NAME = os.environ.get("LEXI_MODEL", "llama-3.1-8b-instant")
MAX_HISTORY_TURNS = int(os.environ.get("LEXI_MAX_HISTORY_TURNS", "20"))

PRESENTON_BASE_URL = os.environ.get("PRESENTON_BASE_URL", "http://localhost:5000")
PRESENTON_API_KEY = os.environ.get("PRESENTON_API_KEY", "")
