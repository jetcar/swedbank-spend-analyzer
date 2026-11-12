from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any
import os

from fastapi import HTTPException
import httpx
import jwt

from app import config, demo


def make_jwt(demo_mode: bool = False) -> str:
    prefix = "DEMO_ENABLE_BANKING" if demo_mode and os.getenv("DEMO_ENABLE_BANKING_APP_ID") else "ENABLE_BANKING"
    app_id = os.environ[f"{prefix}_APP_ID"]
    key_path = os.environ[f"{prefix}_PRIVATE_KEY_PATH"]
    with open(key_path, "rb") as key_file:
        private_key = key_file.read()

    now = int(datetime.now(timezone.utc).timestamp())
    return jwt.encode(
        {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600},
        private_key,
        algorithm="RS256",
        headers={"kid": app_id},
    )


def api_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_jwt()}", "Accept": "application/json"}


async def bank_api_headers(owner_id: str) -> dict[str, str]:
    if not owner_id.startswith(demo.PREFIX):
        return api_headers()
    demo.store.get(owner_id)
    try:
        headers = {"Authorization": f"Bearer {make_jwt(demo_mode=True)}", "Accept": "application/json"}
    except (KeyError, OSError, ValueError) as error:
        raise HTTPException(503, "Enable Banking sandbox credentials are not configured") from error
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{config.enablebanking_api_url}/application", headers=headers)
    if response.is_error or response.json().get("environment") != "SANDBOX":
        raise HTTPException(503, "Demo requires an Enable Banking SANDBOX application")
    return headers


def bank_aspsp(owner_id: str) -> dict[str, str]:
    if owner_id.startswith(demo.PREFIX):
        return {"country": os.getenv("DEMO_BANK_COUNTRY", "EE"), "name": os.getenv("DEMO_BANK_NAME", "Swedbank")}
    return {"country": "EE", "name": "Swedbank"}


async def start_authorization(owner_id: str, state: str, headers: dict[str, str]) -> str:
    valid_until = datetime.now(timezone.utc) + timedelta(days=30)
    payload = {
        "access": {"balances": True, "transactions": True, "valid_until": valid_until.isoformat()},
        "aspsp": bank_aspsp(owner_id),
        "psu_type": "personal",
        "redirect_url": config.redirect_url,
        "state": state,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{config.enablebanking_api_url}/auth", json=payload, headers=headers)
    if response.is_error:
        raise HTTPException(response.status_code, "Enable Banking authorization could not be started")
    return response.json()["url"]


async def create_session(owner_id: str, code: str) -> dict[str, Any]:
    headers = await bank_api_headers(owner_id)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{config.enablebanking_api_url}/sessions",
            json={"code": code},
            headers=headers,
        )
    if response.is_error:
        raise HTTPException(response.status_code, "Enable Banking session could not be created")
    return response.json()


async def transaction_pages(
    bank_account_id: str, params: dict[str, str], headers: dict[str, str],
) -> AsyncIterator[dict[str, Any]]:
    continuation_key: str | None = None
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            if continuation_key:
                params["continuation_key"] = continuation_key
            response = await client.get(
                f"{config.enablebanking_api_url}/accounts/{bank_account_id}/transactions",
                params=params,
                headers=headers,
            )
            if response.is_error:
                raise HTTPException(response.status_code, "Could not fetch bank transactions")
            payload = response.json()
            yield payload
            continuation_key = payload.get("continuation_key")
            if not continuation_key:
                return
