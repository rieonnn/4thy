"""Порты внешних зависимостей и инфраструктурные заглушки."""
from __future__ import annotations
from decimal import Decimal
from typing import Protocol


class PaymentGateway(Protocol):
    def authorize(self, user_id: str, amount: Decimal) -> bool: ...
    def charge(self, user_id: str, amount: Decimal) -> bool: ...


class InMemoryPaymentGateway:
    """Заглушка платёжного шлюза для локального запуска и тестов."""
    def __init__(self):
        self.next_authorization_result = True
        self.next_charge_result = True
        self.authorizations: list[tuple[str, Decimal]] = []
        self.charges: list[tuple[str, Decimal]] = []

    def authorize(self, user_id: str, amount: Decimal) -> bool:
        self.authorizations.append((user_id, amount))
        result = self.next_authorization_result
        self.next_authorization_result = True
        return result

    def charge(self, user_id: str, amount: Decimal) -> bool:
        self.charges.append((user_id, amount))
        result = self.next_charge_result
        self.next_charge_result = True
        return result
