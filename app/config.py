import os

from app import passi_auth


database_url = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://spend_analyzer:change-me@postgres:5432/spend_analyzer",
)
enablebanking_api_url = os.getenv("ENABLE_BANKING_API_URL", "https://api.enablebanking.com").rstrip("/")
redirect_url = os.getenv("ENABLE_BANKING_REDIRECT_URL", "http://localhost:8000/callback")
BANK_AUTH_STATE_TTL_SECONDS = 600
BANK_AUTH_BROWSER_FLOW_LIMIT = 4
legacy_owner_id = os.getenv(
    "PASSI_LEGACY_OWNER_ID", "admin@passi.cloud" if passi_auth.AUTH_ENABLED else "local"
).strip().lower()
