from pathlib import Path
import os


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "search.db"
REVENUE_CONFIG_PATH = ROOT_DIR / "revenue_config.json"
POLICY_CONFIG_PATH = ROOT_DIR / "policy_config.json"

SEARCH_BACKEND = os.getenv("UNSTOPPABLE_SEARCH_BACKEND", "sqlite").strip().lower()
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "").strip()
ELASTICSEARCH_INDEX = os.getenv("ELASTICSEARCH_INDEX", "unstoppable-pages").strip()
ADMIN_API_TOKEN = os.getenv(
    "UNSTOPPABLE_ADMIN_API_TOKEN", "change-me-admin-token"
).strip()
PAYMENT_EXECUTOR_MODE = (
    os.getenv("UNSTOPPABLE_PAYMENT_EXECUTOR_MODE", "mock").strip().lower()
)
PAYMENT_EXECUTOR_CMD = os.getenv("UNSTOPPABLE_PAYMENT_EXECUTOR_CMD", "").strip()
BTC_MEMPOOL_API = os.getenv(
    "UNSTOPPABLE_BTC_MEMPOOL_API", "https://mempool.space/api"
).strip()
EXECUTOR_WEBHOOK_SECRET = os.getenv(
    "UNSTOPPABLE_EXECUTOR_WEBHOOK_SECRET", "change-me-webhook-secret"
).strip()


def validate_runtime_secrets() -> None:
    weak_admin = (not ADMIN_API_TOKEN) or (ADMIN_API_TOKEN == "change-me-admin-token")
    weak_webhook = (not EXECUTOR_WEBHOOK_SECRET) or (
        EXECUTOR_WEBHOOK_SECRET == "change-me-webhook-secret"
    )
    if weak_admin:
        raise RuntimeError(
            "UNSTOPPABLE_ADMIN_API_TOKEN must be set to a strong non-default value"
        )
    if len(ADMIN_API_TOKEN) < 16:
        raise RuntimeError("UNSTOPPABLE_ADMIN_API_TOKEN must be at least 16 chars")
    if weak_webhook:
        raise RuntimeError(
            "UNSTOPPABLE_EXECUTOR_WEBHOOK_SECRET must be set to a strong non-default value"
        )
