"""Application configuration — env vars + cached DB settings."""
import os
from pathlib import Path
from dotenv import load_dotenv
from cryptography.fernet import Fernet

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
if SECRET_KEY == "dev-secret-change-me":
    raise RuntimeError(
        "SECRET_KEY is still the default value. Set a secure SECRET_KEY in your .env file "
        "or Railway environment variables before deploying."
    )
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'varshini.db'}")

# DeepSeek (LLM) — OpenAI-compatible API
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

_enc_key = os.getenv("ENCRYPTION_KEY")
_fernet = None
if _enc_key:
    try:
        _fernet = Fernet(_enc_key.encode() if isinstance(_enc_key, str) else _enc_key)
    except Exception:
        import logging
        logging.getLogger("varshini").warning("Invalid ENCRYPTION_KEY — passwords will not be encrypted at rest")


def encrypt_value(plaintext: str) -> str:
    if _fernet is None:
        import logging
        logging.getLogger("varshini").warning(
            "ENCRYPTION_KEY not set — sensitive values will be stored as plaintext in the database"
        )
        return plaintext
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    if _fernet is None or not ciphertext:
        return ciphertext
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except Exception:
        import logging
        logging.getLogger("varshini").warning(
            "Failed to decrypt a stored value — returning empty string"
        )
        return ""


# In-memory cache of settings from DB — refreshed on writes
settings_cache: dict[str, str] = {}

DEFAULT_SETTINGS: dict[str, str] = {
    "email_time": "08:00",
    "email_recipient": os.getenv("EMAIL_RECIPIENT", ""),
    "smtp_host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
    "smtp_port": os.getenv("SMTP_PORT", "587"),
    "smtp_user": os.getenv("SMTP_USER", ""),
    "smtp_password": "",  # loaded (and encrypted) from .env via ENV_BACKED_SETTINGS
    "smtp_use_tls": os.getenv("SMTP_USE_TLS", "true"),
    "email_provider": os.getenv("EMAIL_PROVIDER", "smtp"),  # "smtp" or "resend_api"
    "instagram_username": os.getenv("INSTAGRAM_USERNAME", ""),
    "instagram_password": "",  # loaded (and encrypted) from .env via ENV_BACKED_SETTINGS
    "scrape_keywords": '["open call for artists UK","artist residency UK","grant for artists","art competition UK","exhibition opportunity","call for entries art","artist fellowship","public art commission","open call art 2025","international open call artists"]',
    "instagram_hashtags": '["opencallforartists","artistopportunity","callforartists","artresidency","artistgrant","opencall","artcompetition","callforentries","artopportunity","artistresidency","contemporaryart","ukartist","ukart","artcommission","publicart"]',
    "max_results_per_source": "50",
    "relevance_threshold": "0.2",
    "scrape_hour": "2",
    "uk_only": "true",
    # AI / DeepSeek
    "ai_enabled": os.getenv("AI_ENABLED", "true"),
    "deepseek_model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
    "deepseek_api_key": "",  # loaded (and encrypted) from .env via ENV_BACKED_SETTINGS
}

# Settings whose initial value comes from environment variables (.env).
# On startup these fill any setting still blank in the database, so values
# added to .env after the first run still show up in the dashboard.
ENV_BACKED_SETTINGS: dict[str, str] = {
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_user": "SMTP_USER",
    "smtp_password": "SMTP_PASSWORD",
    "smtp_use_tls": "SMTP_USE_TLS",
    "instagram_username": "INSTAGRAM_USERNAME",
    "instagram_password": "INSTAGRAM_PASSWORD",
    "email_recipient": "EMAIL_RECIPIENT",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_model": "DEEPSEEK_MODEL",
}

# Settings encrypted at rest (and decrypted for display / use)
SENSITIVE_SETTINGS = ("smtp_password", "instagram_password", "deepseek_api_key")
