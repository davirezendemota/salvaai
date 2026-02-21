"""Interface base do gateway de pagamento PIX (desacoplada)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class CreateChargeResult:
    """Resultado da criação de uma cobrança PIX."""

    charge_id: str
    qr_code: str | None = None
    qr_code_base64: str | None = None
    link: str | None = None
    expires_at: datetime | None = None


@dataclass
class ChargeStatus:
    """Status de uma cobrança (consulta ou webhook)."""

    status: str  # pending, paid, cancelled, expired
    paid_at: datetime | None = None


class PaymentGatewayProtocol(Protocol):
    """Protocolo do gateway de pagamento PIX."""

    def create_pix_charge(
        self,
        amount_cents: int,
        reference: str,
        customer_identifier: str,
        description: str | None = None,
    ) -> CreateChargeResult:
        """Cria uma cobrança PIX e retorna dados para pagamento (QR/link)."""
        ...

    def get_charge_status(self, charge_id: str) -> ChargeStatus:
        """Consulta o status atual da cobrança."""
        ...
