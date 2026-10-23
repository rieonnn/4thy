"""Доменная модель аренды электросамоката с журналом неизменяемых событий."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from enum import Enum
from typing import Any
from uuid import uuid4

from .errors import DomainInvariantViolation, ValidationError
from .states import (
    ActiveState, CompletedPendingPaymentState, ReservedState,
    RideState, STATE_TYPES,
)

MIN_BATTERY_THRESHOLD = 15
RESERVATION_MINUTES = 10
OPERATOR_NOTICE_HOURS = 12


@dataclass(frozen=True)
class DomainEvent:
    event_type: str
    occurred_at: datetime
    message: str
    data: tuple[tuple[str, str], ...] = ()

    @classmethod
    def build(cls, event_type: str, occurred_at: datetime, message: str, **data: Any) -> "DomainEvent":
        normalized = tuple(sorted((str(k), str(v)) for k, v in data.items()))
        return cls(event_type, occurred_at, message, normalized)


class ScooterStatus(str, Enum):
    FREE = "free"
    RESERVED = "reserved"  # Дополнение к ЛР2, необходимое для брони на 10 минут.
    RENTED = "rented"
    MAINTENANCE = "maintenance"


@dataclass
class Scooter:
    id: str
    battery_percent: int
    state: ScooterStatus = ScooterStatus.FREE
    current_ride_id: str | None = None
    reservation_ride_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.battery_percent, int) or not 0 <= self.battery_percent <= 100:
            raise ValidationError("battery_percent должен быть целым числом от 0 до 100")

    def ensure_available_for_reservation(self) -> None:
        if self.state == ScooterStatus.MAINTENANCE:
            raise DomainInvariantViolation("Самокат находится на техническом обслуживании")
        if self.state != ScooterStatus.FREE:
            raise DomainInvariantViolation("Самокат не свободен для бронирования")
        if self.battery_percent < MIN_BATTERY_THRESHOLD:
            raise DomainInvariantViolation("Недостаточный заряд для начала аренды (минимум 15%)")

    def reserve(self, ride_id: str) -> None:
        self.ensure_available_for_reservation()
        self.state = ScooterStatus.RESERVED
        self.reservation_ride_id = ride_id

    def release_reservation(self, ride_id: str) -> None:
        if self.state != ScooterStatus.RESERVED or self.reservation_ride_id != ride_id:
            raise DomainInvariantViolation("Нельзя снять чужую или неактивную бронь самоката")
        self.state = ScooterStatus.FREE
        self.reservation_ride_id = None

    def start_rental(self, ride_id: str) -> None:
        if self.state == ScooterStatus.MAINTENANCE:
            raise DomainInvariantViolation("Самокат находится на техническом обслуживании")
        if self.state == ScooterStatus.RENTED:
            raise DomainInvariantViolation("Самокат уже арендован")
        if self.battery_percent < MIN_BATTERY_THRESHOLD:
            raise DomainInvariantViolation("Недостаточный заряд для начала аренды")
        if self.state != ScooterStatus.RESERVED or self.reservation_ride_id != ride_id:
            raise DomainInvariantViolation("Активная бронь этого самоката не найдена")
        self.state = ScooterStatus.RENTED
        self.reservation_ride_id = None
        self.current_ride_id = ride_id

    def finish_rental(self, ride_id: str) -> None:
        if self.state != ScooterStatus.RENTED or self.current_ride_id != ride_id:
            raise DomainInvariantViolation("Самокат не находится в этой аренде")
        self.state = ScooterStatus.FREE
        self.current_ride_id = None


@dataclass
class User:
    id: str
    full_name: str
    # В ЛР2 User содержит платёжные данные; значение по умолчанию только для демо.
    payment_data: str = "demo-payment-method"
    has_debt: bool = False
    blocked: bool = False
    debt_amount: Decimal = Decimal("0.00")
    event_journal: list[DomainEvent] = field(default_factory=list)

    def add_event(self, event: DomainEvent) -> None:
        self.event_journal.append(event)

    def block_for_debt(self, amount: Decimal, now: datetime) -> None:
        self.has_debt = True
        self.blocked = True
        self.debt_amount = max(Decimal("0.00"), amount)
        self.add_event(DomainEvent.build(
            "AccountBlocked", now, "Аккаунт заблокирован из-за неоплаченной поездки",
            debt_amount=self.debt_amount,
        ))

    def settle_debt(self, now: datetime) -> None:
        self.has_debt = False
        self.blocked = False
        self.debt_amount = Decimal("0.00")
        self.add_event(DomainEvent.build("DebtSettled", now, "Задолженность погашена; блокировка снята"))


@dataclass(frozen=True)
class Tariff:
    price_per_minute: Decimal = Decimal("5.00")
    minimum_cost: Decimal = Decimal("50.00")
    pause_price_per_minute: Decimal = Decimal("1.00")

    def __post_init__(self):
        if self.price_per_minute < 0 or self.minimum_cost < 0 or self.pause_price_per_minute < 0:
            raise ValidationError("Тарифы и минимальная стоимость не могут быть отрицательными")
        if self.pause_price_per_minute >= self.price_per_minute:
            raise ValidationError("Ставка паузы должна быть ниже обычной ставки")

    @staticmethod
    def _billable_minutes(seconds: float) -> int:
        if seconds <= 0:
            return 0
        return int((Decimal(str(seconds)) / Decimal(60)).to_integral_value(rounding=ROUND_CEILING))

    def calculate_with_pause(self, active_seconds: float, paused_seconds: float) -> Decimal:
        if active_seconds < 0 or paused_seconds < 0:
            raise DomainInvariantViolation("Продолжительность поездки и паузы не может быть отрицательной")
        active_cost = Decimal(self._billable_minutes(active_seconds)) * self.price_per_minute
        paused_cost = Decimal(self._billable_minutes(paused_seconds)) * self.pause_price_per_minute
        amount = max(self.minimum_cost, active_cost + paused_cost)
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class Ride:
    id: str
    scooter_id: str
    user_id: str
    reserved_at: datetime
    reservation_expires_at: datetime
    _state: RideState = field(default_factory=ReservedState, repr=False)
    start_time: datetime | None = None
    end_time: datetime | None = None
    cost: Decimal | None = None
    pause_started_at: datetime | None = None
    paused_seconds: float = 0.0
    operator_notified: bool = False
    operator_contacted: bool = False
    rejection_reason: str | None = None
    event_journal: list[DomainEvent] = field(default_factory=list)

    @property
    def state(self) -> str:
        return self._state.name

    @property
    def allowed_events(self) -> tuple[str, ...]:
        return self._state.allowed_events

    def add_event(self, event_type: str, now: datetime, message: str, **data: Any) -> None:
        self.event_journal.append(DomainEvent.build(event_type, now, message, **data))

    def cancel_on_timeout(self, now: datetime) -> None:
        self._state = self._state.cancel_timeout()
        self.add_event("ReservationExpired", now, "Бронь автоматически снята по истечении 10 минут")

    def reject_start(self, now: datetime, reason: str) -> None:
        self._state = self._state.reject_start()
        self.rejection_reason = reason
        self.add_event("RideStartRejected", now, reason)

    def start(self, now: datetime) -> None:
        if now >= self.reservation_expires_at:
            raise DomainInvariantViolation("Нельзя начать поездку после истечения брони")
        self._state = self._state.start()
        self.start_time = now
        self.add_event("RideStarted", now, "Поездка начата после разблокировки замка и запуска тарификации")

    def pause(self, now: datetime) -> None:
        self._state = self._state.pause()
        self.pause_started_at = now
        self.add_event("RidePaused", now, "Поездка поставлена на паузу; действует сниженная ставка")

    def resume(self, now: datetime) -> None:
        if self.pause_started_at is None:
            raise DomainInvariantViolation("Нет открытого интервала паузы")
        self.paused_seconds += (now - self.pause_started_at).total_seconds()
        self.pause_started_at = None
        self._state = self._state.resume()
        self.add_event("RideResumed", now, "Поездка возобновлена; действует обычная ставка")

    def record_zone_denied(self, now: datetime) -> None:
        self.add_event("FinishDeniedWrongZone", now, "Завершение отклонено: самокат вне разрешённой зоны")

    def complete(self, now: datetime, tariff: Tariff) -> Decimal:
        if self.start_time is None:
            raise DomainInvariantViolation("Поездка не была начата")
        if now < self.start_time:
            raise DomainInvariantViolation("Поездка не может завершиться раньше начала")
        final_paused_seconds = self.paused_seconds
        if self.pause_started_at is not None:
            final_paused_seconds += (now - self.pause_started_at).total_seconds()
        elapsed = (now - self.start_time).total_seconds()
        active_seconds = max(0.0, elapsed - final_paused_seconds)
        amount = tariff.calculate_with_pause(active_seconds, final_paused_seconds)
        self._state = self._state.finish()
        self.end_time = now
        self.cost = amount
        self.paused_seconds = final_paused_seconds
        self.pause_started_at = None
        self.add_event("RideCompleted", now, "Фотография получена; поездка закрыта, стоимость рассчитана", cost=amount)
        return amount

    def payment_succeeded(self, now: datetime) -> None:
        self._state = self._state.payment_succeeded()
        self.add_event("PaymentSucceeded", now, "Стоимость поездки списана успешно", cost=self.cost)

    def payment_failed(self, now: datetime) -> None:
        self._state = self._state.payment_failed()
        self.add_event("PaymentFailed", now, "Списание не прошло; сформирована задолженность", cost=self.cost)

    def notify_operator(self, now: datetime) -> None:
        self._state = self._state.operator_notified()
        self.operator_notified = True
        self.add_event("OperatorNotified", now, "Оператор уведомлён о поездке длительностью более 12 часов")

    def mark_operator_contacted(self, now: datetime) -> None:
        self._state = self._state.operator_contacted()
        self.operator_contacted = True
        self.add_event("OperatorContacted", now, "Оператор связался с клиентом")
