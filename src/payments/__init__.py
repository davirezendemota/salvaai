"""Módulo de pagamentos PIX (gateway desacoplado + serviço)."""

from src.payments.service import PaymentService

__all__ = ["PaymentService"]
