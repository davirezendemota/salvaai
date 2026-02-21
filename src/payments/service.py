"""Serviço de domínio: usuários, planos, recargas, whitelist, saldo e uso."""

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlmodel import select

from src.db.models import Plan, Recharge, Usage, User, Whitelist
from src.db.session import get_session
from src.payments.gateway.base import CreateChargeResult, PaymentGatewayProtocol


def _test_balance_posts() -> int:
    """Saldo inicial em ambiente de teste (variável TEST_BALANCE_POSTS, ex: 1000)."""
    raw = (os.getenv("TEST_BALANCE_POSTS") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


class PaymentService:
    """Serviço síncrono (usar via asyncio.to_thread a partir do bot)."""

    def __init__(self, gateway: Optional[PaymentGatewayProtocol] = None):
        from src.payments.gateway.factory import get_gateway
        self._gateway = gateway or get_gateway()

    def get_or_create_user(self, telegram_user_id: int, telegram_chat_id: int) -> Optional[User]:
        test_balance = _test_balance_posts()
        with get_session() as session:
            user = session.exec(
                select(User).where(User.telegram_user_id == telegram_user_id)
            ).first()
            if user:
                return user
            user = User(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                balance_posts=test_balance if test_balance else 0,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    def is_whitelisted(self, telegram_user_id: int) -> bool:
        with get_session() as session:
            w = session.exec(
                select(Whitelist).where(Whitelist.telegram_user_id == telegram_user_id)
            ).first()
            return w is not None

    def get_plans(self) -> list[Plan]:
        with get_session() as session:
            return list(session.exec(select(Plan).order_by(Plan.price_cents)))

    def get_plan_by_slug(self, slug: str) -> Optional[Plan]:
        with get_session() as session:
            return session.exec(select(Plan).where(Plan.slug == slug)).first()

    def create_recharge(
        self,
        telegram_user_id: int,
        telegram_chat_id: int,
        plan_slug: str,
    ) -> tuple[Optional[Recharge], Optional[CreateChargeResult]]:
        """Cria recarga pendente e cobrança no gateway. Retorna (Recharge, CreateChargeResult) ou (None, None)."""
        if not self._gateway:
            return None, None
        plan = self.get_plan_by_slug(plan_slug)
        if not plan:
            return None, None
        user = self.get_or_create_user(telegram_user_id, telegram_chat_id)
        if not user:
            return None, None
        reference = f"recharge-{user.id}-{plan.slug}"
        result = self._gateway.create_pix_charge(
            amount_cents=plan.price_cents,
            reference=reference,
            customer_identifier=str(telegram_user_id),
            description=f"SalvaAI - {plan.name} ({plan.posts_included} posts)",
        )
        with get_session() as session:
            recharge = Recharge(
                user_id=user.id,
                plan_id=plan.id,
                amount_cents=plan.price_cents,
                posts_granted=plan.posts_included,
                gateway="example",
                gateway_charge_id=result.charge_id,
                status="pending",
            )
            session.add(recharge)
            session.commit()
            session.refresh(recharge)
        return recharge, result

    def confirm_recharge(self, gateway_charge_id: str) -> bool:
        """Marca recarga como paga e adiciona posts ao saldo do usuário. Retorna True se encontrou e confirmou."""
        with get_session() as session:
            recharge = session.exec(
                select(Recharge).where(
                    Recharge.gateway_charge_id == gateway_charge_id,
                    Recharge.status == "pending",
                )
            ).first()
            if not recharge:
                return False
            recharge.status = "paid"
            recharge.paid_at = datetime.utcnow()
            user = session.get(User, recharge.user_id)
            if user:
                user.balance_posts = (user.balance_posts or 0) + recharge.posts_granted
                user.updated_at = datetime.utcnow()
            session.add(recharge)
            session.commit()
            return True

    def can_download(self, telegram_user_id: int) -> bool:
        if self.is_whitelisted(telegram_user_id):
            return True
        user = self.get_or_create_user(telegram_user_id, telegram_user_id)
        if not user:
            return False
        return (user.balance_posts or 0) > 0

    def record_usage(
        self,
        telegram_user_id: int,
        video_link: Optional[str] = None,
        token_cost_usd: float = 0.0,
    ) -> Optional[int]:
        """Cria registro de uso (para obter ID da caption). Não debita saldo. Retorna usage id ou None se sem saldo."""
        user = self.get_or_create_user(telegram_user_id, telegram_user_id)
        if not user:
            return None
        if not self.is_whitelisted(telegram_user_id) and (user.balance_posts or 0) < 1:
            return None
        with get_session() as session:
            user = session.get(User, user.id)
            if not user:
                return None
            usage = Usage(
                user_id=user.id,
                token_cost_usd=token_cost_usd,
                video_link=video_link or None,
            )
            session.add(usage)
            session.commit()
            session.refresh(usage)
            return usage.id

    def deduct_balance(self, telegram_user_id: int) -> bool:
        """Debita 1 post do saldo (chamar após envio do vídeo). Retorna True se debitou ou é whitelist."""
        if self.is_whitelisted(telegram_user_id):
            return True
        with get_session() as session:
            user = session.exec(
                select(User).where(User.telegram_user_id == telegram_user_id)
            ).first()
            if not user or (user.balance_posts or 0) < 1:
                return False
            user.balance_posts = user.balance_posts - 1
            user.updated_at = datetime.utcnow()
            session.add(user)
            session.commit()
            return True

    def consume_post(
        self,
        telegram_user_id: int,
        token_cost_usd: float = 0.0,
        video_link: Optional[str] = None,
    ) -> Optional[int]:
        """Registra uso e debita 1 post (compatibilidade). Preferir record_usage + deduct_balance após envio."""
        usage_id = self.record_usage(telegram_user_id, video_link, token_cost_usd)
        if usage_id is None:
            return None
        if not self.deduct_balance(telegram_user_id):
            return None
        return usage_id

    def get_balance(self, telegram_user_id: int) -> int:
        user = self.get_or_create_user(telegram_user_id, telegram_user_id)
        if not user:
            return 0
        return user.balance_posts or 0

    def get_usage_count(self, telegram_user_id: int) -> int:
        """Quantidade de downloads já usados pelo usuário."""
        user = self.get_or_create_user(telegram_user_id, telegram_user_id)
        if not user:
            return 0
        with get_session() as session:
            r = session.exec(
                select(func.count(Usage.id)).where(Usage.user_id == user.id)
            ).one()
            return r or 0

    def get_usage_history(
        self, telegram_user_id: int, limit: int = 50
    ) -> list[tuple[int, datetime, float, Optional[str]]]:
        """Histórico de consumo: (id, used_at, token_cost_usd, video_link) ordenado por used_at desc."""
        user = self.get_or_create_user(telegram_user_id, telegram_user_id)
        if not user:
            return []
        with get_session() as session:
            usages = list(
                session.exec(
                    select(Usage)
                    .where(Usage.user_id == user.id)
                    .order_by(Usage.used_at.desc())
                    .limit(limit)
                )
            )
            return [
                (u.id, u.used_at, u.token_cost_usd, u.video_link)
                for u in usages
            ]

    def get_total_recharged_brl(self, telegram_user_id: int) -> float:
        """Soma em R$ do que o usuário já recarregou (recargas pagas)."""
        user = self.get_or_create_user(telegram_user_id, telegram_user_id)
        if not user:
            return 0.0
        with get_session() as session:
            r = session.exec(
                select(func.coalesce(func.sum(Recharge.amount_cents), 0)).where(
                    Recharge.user_id == user.id,
                    Recharge.status == "paid",
                )
            ).one()
            return (r or 0) / 100.0

    def whitelist_add(self, telegram_user_id: int, reason: Optional[str] = None) -> bool:
        with get_session() as session:
            existing = session.exec(
                select(Whitelist).where(Whitelist.telegram_user_id == telegram_user_id)
            ).first()
            if existing:
                return True
            session.add(Whitelist(telegram_user_id=telegram_user_id, reason=reason))
            session.commit()
            return True

    def whitelist_remove(self, telegram_user_id: int) -> bool:
        with get_session() as session:
            w = session.exec(
                select(Whitelist).where(Whitelist.telegram_user_id == telegram_user_id)
            ).first()
            if not w:
                return False
            session.delete(w)
            session.commit()
            return True
