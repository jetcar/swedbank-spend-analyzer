from typing import Any

from fastapi import APIRouter, Depends, Query

from app import dependencies, schemas
from app.services import transactions as transactions_service


router = APIRouter()


@router.get("/api/transactions")
async def list_transactions(
    limit: int = Query(default=100, ge=1, le=1000),
    month: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=1, le=30),
    owner_id: str = Depends(dependencies.current_owner_id),
    before_id: int | None = None,
    page_by_id: bool = False,
) -> list[dict[str, Any]]:
    return await transactions_service.list_transactions(limit=limit, month=month, days=days, owner_id=owner_id, before_id=before_id, page_by_id=page_by_id)


@router.patch("/api/transactions/{transaction_id}/statistics")
async def update_transaction_statistics(
    transaction_id: int, settings: schemas.TransactionStatisticsSettings, owner_id: str = Depends(dependencies.current_owner_id)
) -> dict[str, Any]:
    return await transactions_service.update_transaction_statistics(transaction_id=transaction_id, settings=settings, owner_id=owner_id)


@router.get("/api/expenses")
async def list_expenses(
    limit: int = Query(default=1000, ge=1, le=5000),
    month: str | None = Query(default=None),
    owner_id: str = Depends(dependencies.current_owner_id),
) -> list[dict[str, Any]]:
    return await transactions_service.list_expenses(limit=limit, month=month, owner_id=owner_id)


@router.patch("/api/expenses/{expense_id}/category")
async def assign_expense(
    expense_id: int, assignment: schemas.CategoryAssignment, owner_id: str = Depends(dependencies.current_owner_id)
) -> dict[str, Any]:
    return await transactions_service.assign_expense(expense_id=expense_id, assignment=assignment, owner_id=owner_id)


@router.put("/api/expenses/{expense_id}/splits")
async def split_expense(
    expense_id: int, assignment: schemas.ExpenseSplitAssignment, owner_id: str = Depends(dependencies.current_owner_id)
) -> dict[str, Any]:
    return await transactions_service.split_expense(expense_id=expense_id, assignment=assignment, owner_id=owner_id)
