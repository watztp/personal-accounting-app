from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_ENV_PATH = Path(__file__).resolve().parent / ".env"
ENV_PATH = Path(os.getenv("CATEGORY_ENV_FILE") or DEFAULT_ENV_PATH).expanduser().resolve()


def ensure_env_loaded() -> None:
    """Load category-helper defaults without overriding the process environment."""
    load_dotenv(dotenv_path=ENV_PATH, override=False)
