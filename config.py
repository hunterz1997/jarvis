"""Central configuration for Jarvis — all constants, model names, and settings."""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field

BASE_DIR = Path(__file__).parent

# Force-load .env before pydantic-settings reads env vars — this overrides any
# empty/stale system-level env vars (e.g. ANTHROPIC_API_KEY='') with .env values.
try:
    from dotenv import load_dotenv
    load_dotenv(str(BASE_DIR / ".env"), override=True)
except ImportError:
    pass

# ── SECRETS — Windows Credential Manager (encrypted-at-rest) overrides .env ──
# If a secret is stored in the Windows Credential Vault under service "Jarvis",
# it overrides whatever value .env has. Use `python deploy/migrate_secrets.py`
# to move existing .env keys into the vault. Falls back silently to .env if
# keyring isn't installed or vault is unavailable.
KEYRING_SERVICE = "Jarvis"
KEYRING_KEYS = ("ANTHROPIC_API_KEY", "GROQ_API_KEY")

try:
    import keyring as _keyring
    for _k in KEYRING_KEYS:
        try:
            _v = _keyring.get_password(KEYRING_SERVICE, _k)
            if _v and _v.strip():
                os.environ[_k] = _v.strip()
        except Exception:
            pass
except ImportError:
    pass

MEMORY_DIR = BASE_DIR / "memory"
LOGS_DIR = BASE_DIR / "logs"
UI_DIR = BASE_DIR / "ui"

MEMORY_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    # LLM Backend — "groq" | "ollama" | "anthropic"
    llm_backend: str = Field("groq", env="LLM_BACKEND")

    # Groq (free, fast — recommended)
    groq_api_key: str = Field("", env="GROQ_API_KEY")
    groq_model: str = Field("llama-3.3-70b-versatile", env="GROQ_MODEL")

    # Anthropic (only needed when llm_backend=anthropic)
    anthropic_api_key: str = Field("not-set", env="ANTHROPIC_API_KEY")

    # Ollama (only needed when llm_backend=ollama)
    ollama_url: str = Field("http://localhost:11434", env="OLLAMA_URL")
    ollama_model: str = Field("qwen2.5:0.5b", env="OLLAMA_MODEL")

    # Server
    host: str = Field("127.0.0.1", env="HOST")
    port: int = Field(8000, env="PORT")

    # Models — both point to Sonnet; Opus routing disabled to conserve credits
    opus_model: str = "claude-sonnet-4-5"
    sonnet_model: str = "claude-sonnet-4-5"
    opus_max_tokens: int = 8192
    sonnet_max_tokens: int = 8192

    # WhatsApp bridge
    whatsapp_bridge_url: str = Field("http://localhost:3001", env="WHATSAPP_BRIDGE_URL")

    # Google MCPs
    google_drive_mcp_url: str = Field("", env="GOOGLE_DRIVE_MCP_URL")
    google_gmail_mcp_url: str = Field("", env="GOOGLE_GMAIL_MCP_URL")
    google_calendar_mcp_url: str = Field("", env="GOOGLE_CALENDAR_MCP_URL")

    # Zomato
    zomato_mcp_url: str = Field("https://mcp-server.zomato.com/mcp", env="ZOMATO_MCP_URL")

    # Location
    user_city: str = Field("Ahmedabad", env="USER_CITY")
    user_location_lat: float = Field(23.0225, env="USER_LOCATION_LAT")
    user_location_lon: float = Field(72.5714, env="USER_LOCATION_LON")

    # Memory
    db_path: Path = MEMORY_DIR / "jarvis.db"
    max_history_messages: int = 50

    # Logging
    log_level: str = Field("INFO", env="LOG_LEVEL")
    log_file: Path = LOGS_DIR / "jarvis.log"
    log_max_bytes: int = 10 * 1024 * 1024  # 10MB
    log_backup_count: int = 5

    # Agent
    max_tool_iterations: int = 15
    tool_timeout_seconds: int = 30

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"


# Singleton settings instance
settings = Settings()


# Model router thresholds
OPUS_TRIGGER_KEYWORDS = {
    "analyze", "analysis", "report", "write a", "write me", "draft",
    "strategy", "compare", "research", "summarize", "summarise",
    "audit", "plan", "create a", "create an", "generate", "deep dive",
    "code", "build", "develop", "explain in detail", "comprehensive",
    "document", "proposal", "script", "automation", "investigate",
    "review", "assess", "evaluate", "breakdown", "break down",
    "translate", "convert", "extract", "process", "design",
}

OPUS_MESSAGE_LENGTH_THRESHOLD = 500
OPUS_HISTORY_TURN_THRESHOLD = 20
