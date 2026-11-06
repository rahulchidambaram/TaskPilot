"""Runtime configuration, read from environment variables on every call so tests can override it."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (KEY=VALUE, '#' comments). Real environment variables always win."""
    f = Path(path)
    if not f.is_file():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #")[0].strip().strip("\"'")
        os.environ.setdefault(key.strip(), value)


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # LLM
    llm_provider: str          # "gemini" | "groq"
    gemini_api_key: str
    gemini_model: str
    groq_api_key: str
    groq_model: str
    # Access / abuse protection
    access_code: str           # if set, every API call needs X-Access-Code
    rate_limit_per_hour: int
    cors_origins: list[str]
    # Safety
    allow_private_hosts: bool  # only for local dev/tests; blocks SSRF when False
    require_approval_default: bool
    # Limits
    max_steps: int
    max_runtime_s: int
    approval_timeout_s: int
    max_concurrent_runs: int
    keep_runs: int
    # Browser
    chrome_path: str           # optional path to a system Chrome/Chromium
    headless: bool
    block_heavy_resources: bool
    # Storage / hosting
    data_dir: Path
    public_base_url: str       # used to build demo-site URLs, e.g. https://taskpilot.onrender.com
    static_dir: Path | None    # built frontend, served by FastAPI when present


def get_settings() -> Settings:
    static = os.getenv("STATIC_DIR", "")
    static_path = Path(static) if static else None
    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "gemini").strip().lower(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        access_code=os.getenv("ACCESS_CODE", ""),
        rate_limit_per_hour=_int("RATE_LIMIT_PER_HOUR", 30),
        cors_origins=origins or ["*"],
        allow_private_hosts=_bool("ALLOW_PRIVATE_HOSTS", False),
        require_approval_default=_bool("REQUIRE_APPROVAL_DEFAULT", True),
        max_steps=_int("MAX_STEPS", 15),
        max_runtime_s=_int("MAX_RUNTIME_S", 180),
        approval_timeout_s=_int("APPROVAL_TIMEOUT_S", 120),
        max_concurrent_runs=_int("MAX_CONCURRENT_RUNS", 1),
        keep_runs=_int("KEEP_RUNS", 100),
        chrome_path=os.getenv("CHROME_PATH", ""),
        headless=_bool("HEADLESS", True),
        block_heavy_resources=_bool("BLOCK_HEAVY_RESOURCES", False),
        data_dir=Path(os.getenv("DATA_DIR", "./data")),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        static_dir=static_path if static_path and static_path.is_dir() else None,
    )
