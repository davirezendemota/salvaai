"""Gateway de pagamento PIX (interface base + implementações)."""

from src.payments.gateway.base import (
    ChargeStatus,
    CreateChargeResult,
    PaymentGatewayProtocol,
)
from src.payments.gateway.example import ExampleGateway
from src.payments.gateway.factory import get_gateway

__all__ = [
    "ChargeStatus",
    "CreateChargeResult",
    "ExampleGateway",
    "PaymentGatewayProtocol",
    "get_gateway",
]
