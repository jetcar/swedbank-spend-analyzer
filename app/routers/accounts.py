from typing import Any

from fastapi import APIRouter, Depends

from app import dependencies, schemas
from app.services import accounts as accounts_service


router = APIRouter()


@router.get("/api/accounts")
async def list_accounts(owner_id: str = Depends(dependencies.current_owner_id)) -> list[dict[str, Any]]:
    return await accounts_service.list_accounts(owner_id=owner_id)


@router.patch("/api/accounts/{account_id}/balance")
async def set_account_balance(
    account_id: int, settings: schemas.AccountBalanceSettings, owner_id: str = Depends(dependencies.current_owner_id)
) -> dict[str, Any]:
    return await accounts_service.set_account_balance(account_id=account_id, settings=settings, owner_id=owner_id)


@router.patch("/api/accounts/{account_id}/currency")
async def set_account_currency(
    account_id: int, settings: schemas.AccountCurrencySettings, owner_id: str = Depends(dependencies.current_owner_id)
) -> dict[str, Any]:
    return await accounts_service.set_account_currency(account_id=account_id, settings=settings, owner_id=owner_id)


@router.patch("/api/accounts/{account_id}/monitor")
async def set_account_monitoring(
    account_id: int, monitored: bool, owner_id: str = Depends(dependencies.current_owner_id)
) -> dict[str, Any]:
    return await accounts_service.set_account_monitoring(account_id=account_id, monitored=monitored, owner_id=owner_id)
