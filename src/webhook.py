"""App FastAPI para webhook de pagamentos PIX."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI

from src.payments.service import PaymentService


def get_payment_service() -> PaymentService:
    return PaymentService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # nada a fechar por enquanto


app = FastAPI(title="SalvaAI Payments Webhook", lifespan=lifespan)


@app.post("/payments/webhook")
async def payments_webhook(body: dict[str, Any] = Body(default={})) -> dict[str, str]:
    """
    Recebe notificação do gateway (ou teste manual).
    Body esperado (example): {"charge_id": "example-xxx"} ou {"charge_id": "example-paid"} para simular pago.
    """
    charge_id = body.get("charge_id") if isinstance(body, dict) else None
    if not charge_id or not isinstance(charge_id, str):
        return {"status": "error", "detail": "charge_id required"}
    service = get_payment_service()
    ok = service.confirm_recharge(charge_id.strip())
    if ok:
        return {"status": "ok", "detail": "recharge confirmed"}
    return {"status": "ignored", "detail": "recharge not found or already processed"}
