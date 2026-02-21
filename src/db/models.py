"""Modelos SQLModel para SQLite: usuários, planos, recargas, uso, whitelist."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class User(SQLModel, table=True):
    """Usuário identificado pelo Telegram (saldo de posts para downloads)."""

    __tablename__ = "user"

    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_user_id: int = Field(unique=True, index=True)
    telegram_chat_id: int = Field(index=True)
    balance_posts: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    recharges: list["Recharge"] = Relationship(back_populates="user")
    usages: list["Usage"] = Relationship(back_populates="user")


class Plan(SQLModel, table=True):
    """Plano de recarga (Basic, Pro, Creator)."""

    __tablename__ = "plan"

    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True, max_length=32)
    name: str = Field(max_length=64)
    price_cents: int = Field()
    posts_included: int = Field()

    recharges: list["Recharge"] = Relationship(back_populates="plan")


class Recharge(SQLModel, table=True):
    """Recarga (compra avulsa) vinculada a um usuário e plano."""

    __tablename__ = "recharge"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    plan_id: int = Field(foreign_key="plan.id")
    amount_cents: int = Field()
    posts_granted: int = Field()
    gateway: str = Field(max_length=64)
    gateway_charge_id: str = Field(max_length=256, index=True)
    status: str = Field(max_length=32)  # pending, paid, cancelled, expired
    created_at: datetime = Field(default_factory=datetime.utcnow)
    paid_at: Optional[datetime] = Field(default=None)

    user: Optional[User] = Relationship(back_populates="recharges")
    plan: Optional[Plan] = Relationship(back_populates="recharges")


class Usage(SQLModel, table=True):
    """Registro de uso (1 download) com custo de tokens em USD e link do vídeo."""

    __tablename__ = "usage"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    used_at: datetime = Field(default_factory=datetime.utcnow)
    token_cost_usd: float = Field(default=0.0)
    video_link: Optional[str] = Field(default=None, max_length=2048)

    user: Optional[User] = Relationship(back_populates="usages")


class Whitelist(SQLModel, table=True):
    """Usuários que não precisam pagar (acesso ilimitado)."""

    __tablename__ = "whitelist"

    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_user_id: int = Field(unique=True, index=True)
    reason: Optional[str] = Field(default=None, max_length=256)
    created_at: datetime = Field(default_factory=datetime.utcnow)
