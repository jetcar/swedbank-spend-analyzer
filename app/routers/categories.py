from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from app import dependencies, schemas
from app.services import categories as categories_service


router = APIRouter()


@router.get("/api/categories")
async def list_categories(
    month: str = Query(default=None), language: Literal["ru", "en", "et"] = Query(default="ru"), owner_id: str = Depends(dependencies.current_owner_id)
) -> list[dict[str, Any]]:
    return await categories_service.list_categories(month=month, language=language, owner_id=owner_id)


@router.post("/api/categories")
async def create_category(
    category_input: schemas.NewCategory, owner_id: str = Depends(dependencies.current_owner_id)
) -> dict[str, Any]:
    return await categories_service.create_category(category_input=category_input, owner_id=owner_id)


@router.patch("/api/categories/{category_id}")
async def update_category(
    category_id: int, settings: schemas.CategorySettings, owner_id: str = Depends(dependencies.current_owner_id)
) -> dict[str, Any]:
    return await categories_service.update_category(category_id=category_id, settings=settings, owner_id=owner_id)


@router.delete("/api/categories/{category_id}")
async def delete_category(
    category_id: int, owner_id: str = Depends(dependencies.current_owner_id)
) -> dict[str, Any]:
    return await categories_service.delete_category(category_id=category_id, owner_id=owner_id)
