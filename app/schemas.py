from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class NewCategory(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    icon: str = Field(default="🏷️", min_length=1, max_length=20)
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    budget: Decimal = Field(default=Decimal(0), max_digits=18, decimal_places=2, allow_inf_nan=False)


class CategorySettings(BaseModel):
    name: str | None = None
    budget: Decimal | None = Field(default=None, max_digits=18, decimal_places=2, allow_inf_nan=False)
    enabled: bool | None = None
    icon: str | None = Field(default=None, min_length=1, max_length=20)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    forecast_mode: Literal["budget", "payment"] | None = None
    payment_status: Literal["auto", "open", "closed"] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str | None) -> str | None:
        if name is None:
            return None
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("Category name must be between 1 and 100 characters")
        return name


class CategoryAssignment(BaseModel):
    category_id: int | None = None


class TransactionStatisticsSettings(BaseModel):
    excluded_from_statistics: StrictBool


class AccountBalanceSettings(BaseModel):
    balance: Decimal = Field(max_digits=18, decimal_places=2, allow_inf_nan=False)


class AccountCurrencySettings(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class SplitLine(BaseModel):
    category_id: int
    amount: float = Field(gt=0)


class ExpenseSplitAssignment(BaseModel):
    splits: list[SplitLine] = Field(min_length=2)


class NewPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    direction: Literal["DBIT", "CRDT"]
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    payment_status: Literal["auto", "open", "closed"] = "auto"
    transaction_ids: list[int] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value.strip()

    @field_validator("transaction_ids")
    @classmethod
    def validate_transaction_ids(cls, value: list[int]) -> list[int]:
        if any(transaction_id <= 0 for transaction_id in value) or len(value) != len(set(value)):
            raise ValueError("Transaction IDs must be positive and unique")
        return value


class PlanItemSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    direction: Literal["DBIT", "CRDT"] | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    payment_status: Literal["auto", "open", "closed"] | None = None
    transaction_ids: list[int] | None = None

    @field_validator("name", "direction", "amount", "currency", "payment_status", "transaction_ids")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Value must not be null")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return NewPlanItem.validate_name(value)

    @field_validator("transaction_ids")
    @classmethod
    def validate_transaction_ids(cls, value: list[int]) -> list[int]:
        return NewPlanItem.validate_transaction_ids(value)
