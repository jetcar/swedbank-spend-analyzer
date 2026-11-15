from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


TENANT_SCHEMA = "tenant"
CONTROL_SCHEMA = "control"


class Base(DeclarativeBase):
    pass


class ControlBase(DeclarativeBase):
    pass


class BankSession(Base):
    __tablename__ = "bank_sessions"
    __table_args__ = {"schema": TENANT_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(320), index=True)
    enable_session_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    bank_name: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(2))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    accounts: Mapped[list["Account"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class AuthSession(ControlBase):
    __tablename__ = "auth_sessions"
    __table_args__ = {"schema": CONTROL_SCHEMA}

    sid: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(320), index=True)
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PendingBankAuthorization(ControlBase):
    __tablename__ = "pending_bank_authorizations"
    __table_args__ = {"schema": CONTROL_SCHEMA}

    state: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(320), index=True)
    browser_binding: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BankSyncState(ControlBase):
    __tablename__ = "bank_sync_states"
    __table_args__ = {"schema": CONTROL_SCHEMA}

    owner_id: Mapped[str] = mapped_column(String(320), primary_key=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = {"schema": TENANT_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey(f"{TENANT_SCHEMA}.bank_sessions.id", ondelete="CASCADE"), index=True)
    bank_account_id: Mapped[str] = mapped_column(String(200), index=True)
    identification_hash: Mapped[str | None] = mapped_column(String(500))
    iban: Mapped[str | None] = mapped_column(String(50))
    name: Mapped[str | None] = mapped_column(String(300))
    currency: Mapped[str | None] = mapped_column(String(3))
    monitored: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    balance_adjustment: Mapped[Decimal] = mapped_column(Numeric(24, 2), default=0, server_default="0", nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON)
    session: Mapped[BankSession] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", "month", name="uq_category_owner_name_month"),
        {"schema": TENANT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str] = mapped_column(String(20), default="🏷️")
    month: Mapped[str] = mapped_column(String(7), default=lambda: datetime.utcnow().strftime("%Y-%m"))
    budget: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR", nullable=False)
    forecast_mode: Mapped[str] = mapped_column(String(10), default="budget", server_default="budget", nullable=False)
    payment_status: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    name_customized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CategoryRule(Base):
    __tablename__ = "category_rules"
    __table_args__ = (
        UniqueConstraint("owner_id", "merchant_key", name="uq_category_rule_owner_merchant"),
        {"schema": TENANT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(320), index=True)
    merchant_key: Mapped[str] = mapped_column(String(500))
    category_name: Mapped[str] = mapped_column(String(100))
    category_icon: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_transaction_account_external"),
        {"schema": TENANT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey(f"{TENANT_SCHEMA}.accounts.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(500))
    booking_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    value_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount: Mapped[float | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    direction: Mapped[str | None] = mapped_column(String(4))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    excluded_from_statistics: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey(f"{TENANT_SCHEMA}.categories.id", ondelete="SET NULL"), index=True)
    raw_data: Mapped[dict] = mapped_column(JSON)
    account: Mapped[Account] = relationship(back_populates="transactions")


class ExpenseSplit(Base):
    __tablename__ = "expense_splits"
    __table_args__ = (
        UniqueConstraint("transaction_id", "category_id", name="uq_expense_split_category"),
        {"schema": TENANT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey(f"{TENANT_SCHEMA}.transactions.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey(f"{TENANT_SCHEMA}.categories.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(18, 2))


class PlanItem(Base):
    __tablename__ = "plan_items"
    __table_args__ = {"schema": TENANT_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(320), index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    name: Mapped[str] = mapped_column(String(100))
    direction: Mapped[str] = mapped_column(String(4))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    payment_status: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto", nullable=False)


class PlanItemTransaction(Base):
    __tablename__ = "plan_item_transactions"
    __table_args__ = {"schema": TENANT_SCHEMA}

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey(f"{TENANT_SCHEMA}.transactions.id", ondelete="CASCADE"), primary_key=True,
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey(f"{TENANT_SCHEMA}.plan_items.id", ondelete="CASCADE"), index=True,
    )
