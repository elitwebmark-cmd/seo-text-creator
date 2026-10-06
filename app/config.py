import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    """Значення змінної без пробілів і лапок (часта помилка при копіюванні в Railway)."""
    return os.getenv(name, default).strip().strip('"').strip("'").strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "claude-sonnet-4-5")
SERPER_API_KEY = _env("SERPER_API_KEY")
APP_PASSWORD = _env("APP_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
MAX_COMPETITORS = _int("MAX_COMPETITORS", 6)
PARALLEL_CLUSTERS = _int("PARALLEL_CLUSTERS", 2)
MOCK = os.getenv("MOCK", "0") == "1"

DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "jobs").mkdir(exist_ok=True)

# Домени, які не беремо як конкурентів (довідники, біржі, соцмережі, відео)
SKIP_DOMAINS = {
    "wikipedia.org", "youtube.com", "facebook.com", "instagram.com", "linkedin.com",
    "reddit.com", "olx.ua", "freelancehunt.com", "freelance.ua", "kabanchik.ua",
    "yaprofi.ua", "tiktok.com", "t.me", "twitter.com", "x.com", "google.com",
    "support.google.com", "play.google.com", "apps.apple.com", "gov.ua", "rada.gov.ua",
    "dou.ua", "pinterest.com", "quora.com",
}

DATAFORSEO_LOGIN = _env("DATAFORSEO_LOGIN")
DATAFORSEO_PASSWORD = _env("DATAFORSEO_PASSWORD")
UA_LOCATION_CODE = 2804  # Україна в Google Ads / DataForSEO
