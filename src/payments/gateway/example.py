"""Implementação de exemplo (stub) do gateway PIX — sem API externa."""

import uuid
from datetime import datetime, timedelta, timezone

from src.payments.gateway.base import ChargeStatus, CreateChargeResult, PaymentGatewayProtocol


class ExampleGateway:
    """Gateway stub: retorna dados fictícios para desenvolver/testar o fluxo."""

    def create_pix_charge(
        self,
        amount_cents: int,
        reference: str,
        customer_identifier: str,
        description: str | None = None,
    ) -> CreateChargeResult:
        charge_id = f"example-{uuid.uuid4().hex[:16]}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        return CreateChargeResult(
            charge_id=charge_id,
            qr_code=f"00020126580014br.gov.bcb.pix0136{charge_id}",
            qr_code_base64=None,
            link=f"https://example.com/pay/{charge_id}",
            expires_at=expires_at,
        )

    def get_charge_status(self, charge_id: str) -> ChargeStatus:
        # Para testes: charge_id que termina com "-paid" é considerado pago
        if charge_id.endswith("-paid") or charge_id == "example-paid":
            return ChargeStatus(status="paid", paid_at=datetime.now(timezone.utc))
        return ChargeStatus(status="pending", paid_at=None)
