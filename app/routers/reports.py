from typing import Any

from fastapi import APIRouter, Depends, Query

from app import dependencies
from app.services import reports as reports_service


router = APIRouter()


@router.get("/api/summary")
async def expense_summary(
    group_by: str = Query(default="description", pattern="^(description|month|category)$"),
    owner_id: str = Depends(dependencies.current_owner_id),
) -> list[dict[str, Any]]:
    return await reports_service.expense_summary(group_by=group_by, owner_id=owner_id)


@router.get("/api/daily-summary")
async def daily_summary(owner_id: str = Depends(dependencies.current_owner_id)) -> list[dict[str, Any]]:
    return await reports_service.daily_summary(owner_id=owner_id)


@router.get("/api/daily-breakdown")
async def daily_breakdown(
    month: str | None = Query(default=None), owner_id: str = Depends(dependencies.current_owner_id)
) -> list[dict[str, Any]]:
    return await reports_service.daily_breakdown(month=month, owner_id=owner_id)
