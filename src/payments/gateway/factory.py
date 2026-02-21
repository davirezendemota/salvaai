"""Factory do gateway de pagamento (retorna implementação conforme config)."""

import os
from typing import Optional

from src.payments.gateway.base import PaymentGatewayProtocol
from src.payments.gateway.example import ExampleGateway


def get_gateway() -> Optional[PaymentGatewayProtocol]:
    """
    Retorna a implementação do gateway conforme PAYMENT_GATEWAY.
    Por enquanto só 'example' é suportado; default é example.
    """
    name = (os.getenv("PAYMENT_GATEWAY") or "example").strip().lower()
    if name == "example":
        return ExampleGateway()
    return ExampleGateway()
