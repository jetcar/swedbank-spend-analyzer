from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from app import dependencies, schemas
from app.services import planning


router = APIRouter()


@router.get("/api/plan")
async def list_plan(
    month: str | None = Query(default=None), language: Literal["ru", "en", "et"] = Query(default="ru"),
    owner_id: str = Depends(dependencies.current_owner_id),
) -> dict[str, Any]:
    return await planning.list_plan(month=month, language=language, owner_id=owner_id)


@router.post("/api/plan/items")
async def create_item(item_input: schemas.NewPlanItem, owner_id: str = Depends(dependencies.current_owner_id)) -> dict[str, Any]:
    return await planning.create_item(item_input=item_input, owner_id=owner_id)


@router.patch("/api/plan/items/{item_id}")
async def update_item(item_id: int, settings: schemas.PlanItemSettings, owner_id: str = Depends(dependencies.current_owner_id)) -> dict[str, Any]:
    return await planning.update_item(item_id=item_id, settings=settings, owner_id=owner_id)


@router.delete("/api/plan/items/{item_id}")
async def delete_item(item_id: int, owner_id: str = Depends(dependencies.current_owner_id)) -> dict[str, Any]:
    return await planning.delete_item(item_id=item_id, owner_id=owner_id)
