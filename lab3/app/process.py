"""Прикладной процесс аренды. Все временные события используют Clock."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from .clock import Clock
from .domain import (
    DomainEvent, MIN_BATTERY_THRESHOLD, OPERATOR_NOTICE_HOURS,
    RESERVATION_MINUTES, Ride, Scooter, ScooterStatus, Tariff, User,
)
from .errors import DomainInvariantViolation, GatewayUnresolved, ValidationError
from .ports import PaymentGateway
from .repositories import RideRepository, ScooterRepository, UserRepository


class ScooterRentalProcess:
    def __init__(
        self,
        clock: Clock,
        rides: RideRepository,
        scooters: ScooterRepository,
        users: UserRepository,
        payment_gateway: PaymentGateway,
        tariff: Tariff | None = None,
        authorization_amount: Decimal = Decimal("300.00"),
    ):
        self.clock = clock
        self.rides = rides
        self.scooters = scooters
        self.users = users
        self.payment_gateway = payment_gateway
        self.tariff = tariff or Tariff()
        self.authorization_amount = authorization_amount

    def create_user(self, full_name: str, payment_data: str = "demo-payment-method") -> User:
        if not isinstance(full_name, str) or not full_name.strip():
            raise ValidationError("full_name обязателен и должен быть непустой строкой")
        if not isinstance(payment_data, str) or not payment_data.strip():
            raise ValidationError("payment_data должен быть непустой строкой")
        now = self.clock.now()
        user = User(id=str(uuid4()), full_name=full_name.strip(), payment_data=payment_data.strip())
        # Платёжные данные не записываются в журнал событий.
        user.add_event(DomainEvent.build("UserCreated", now, "Клиент создан", full_name=user.full_name))
        self.users.add(user)
        return user

    def create_scooter(self, battery_percent: int) -> Scooter:
        if isinstance(battery_percent, bool) or not isinstance(battery_percent, int):
            raise ValidationError("battery_percent должен быть целым числом")
        scooter = Scooter(id=str(uuid4()), battery_percent=battery_percent)
        self.scooters.add(scooter)
        return scooter

    def book_ride(self, scooter_id: str, user_id: str) -> Ride:
        now = self.clock.now()
        user = self.users.get(user_id)
        scooter = self.scooters.get(scooter_id)
        # По регламенту задолженность проверяется перед началом поездки,
        # а не запрещает саму бесплатную бронь. Старт будет отклонён позже.
        scooter.ensure_available_for_reservation()
        ride_id = str(uuid4())
        ride = Ride(
            id=ride_id,
            scooter_id=scooter.id,
            user_id=user.id,
            reserved_at=now,
            reservation_expires_at=now + timedelta(minutes=RESERVATION_MINUTES),
        )
        scooter.reserve(ride_id)
        ride.add_event("BookingCreated", now, "Бесплатная бронь создана на 10 минут", expires_at=ride.reservation_expires_at)
        self.rides.add(ride)
        self.scooters.save(scooter)
        return ride

    @staticmethod
    def _resolve_bool_gateway(*, yes: bool, yes_result, no_result):
        """Отдельная функция-предикат/резолвер для XOR; все случаи исчерпываются."""
        if yes is True:
            return yes_result
        if yes is False:
            return no_result
        raise GatewayUnresolved("Не удалось определить выход XOR-шлюза")

    def start_ride(self, ride_id: str) -> Ride:
        now = self.clock.now()
        ride = self.rides.get(ride_id)
        # Повторный старт запрещён непосредственно объектом состояния.
        if ride.state != "reserved":
            ride.start(now)  # выбросит TransitionNotAllowed с состоянием и событием
        scooter = self.scooters.get(ride.scooter_id)
        user = self.users.get(ride.user_id)

        if now >= ride.reservation_expires_at:
            ride.cancel_on_timeout(now)
            scooter.release_reservation(ride.id)
            self.scooters.save(scooter)
            self.rides.save(ride)
            return ride

        # XOR «Есть задолженность?»; предикат и разрешение шлюза разделены.
        debt_route = self._resolve_bool_gateway(
            yes=(user.has_debt or user.blocked), yes_result="debt", no_result="clear"
        )
        if debt_route == "debt":
            ride.reject_start(now, "Поездка не начата: у клиента имеется задолженность")
            scooter.release_reservation(ride.id)
            self.scooters.save(scooter)
            self.rides.save(ride)
            return ride

        # Защита инвариантов ЛР2 повторно непосредственно перед стартом.
        try:
            if scooter.state != ScooterStatus.RESERVED or scooter.reservation_ride_id != ride.id:
                raise DomainInvariantViolation("Бронь самоката не принадлежит этой поездке")
            if scooter.battery_percent < MIN_BATTERY_THRESHOLD:
                raise DomainInvariantViolation("Недостаточный заряд самоката: требуется минимум 15%")
            if scooter.state == ScooterStatus.MAINTENANCE:
                raise DomainInvariantViolation("Самокат находится на обслуживании")
        except DomainInvariantViolation as exc:
            ride.reject_start(now, str(exc))
            if scooter.state == ScooterStatus.RESERVED and scooter.reservation_ride_id == ride.id:
                scooter.release_reservation(ride.id)
                self.scooters.save(scooter)
            self.rides.save(ride)
            return ride

        authorized = self.payment_gateway.authorize(user.id, self.authorization_amount)
        auth_route = self._resolve_bool_gateway(
            yes=authorized, yes_result="authorized", no_result="rejected"
        )
        if auth_route == "rejected":
            ride.reject_start(now, "Поездка не начата: предварительная авторизация 300 рублей отклонена")
            ride.add_event("PreauthorizationFailed", now, "Предварительная авторизация отклонена", amount=self.authorization_amount)
            scooter.release_reservation(ride.id)
            self.scooters.save(scooter)
            self.rides.save(ride)
            return ride
        ride.add_event("PreauthorizationSucceeded", now, "Предварительная авторизация на 300 рублей прошла", amount=self.authorization_amount)

        # В BPMN действия соединены AND-разветвлением. В прикладном коде их
        # выполняем последовательно, но переход к старту выполняется после обоих.
        self._unlock_lock(ride, now)
        self._start_tariffing(ride, now)
        scooter.start_rental(ride.id)
        ride.start(self.clock.now())  # фактическое начало только после обоих действий
        self.scooters.save(scooter)
        self.rides.save(ride)
        return ride

    def _unlock_lock(self, ride: Ride, now: datetime) -> None:
        ride.add_event("LockUnlocked", now, "Замок разблокирован")

    def _start_tariffing(self, ride: Ride, now: datetime) -> None:
        ride.add_event("TariffingStarted", now, "Тарификация запущена")

    def pause_ride(self, ride_id: str) -> Ride:
        ride = self.rides.get(ride_id)
        ride.pause(self.clock.now())
        self.rides.save(ride)
        return ride

    def resume_ride(self, ride_id: str) -> Ride:
        ride = self.rides.get(ride_id)
        ride.resume(self.clock.now())
        self.rides.save(ride)
        return ride

    def finish_ride(self, ride_id: str, zone_allowed: bool, photo_received: bool) -> tuple[Ride, bool]:
        if not isinstance(zone_allowed, bool) or not isinstance(photo_received, bool):
            raise ValidationError("zone_allowed и photo_received должны быть логическими значениями")
        ride = self.rides.get(ride_id)
        now = self.clock.now()
        if ride.state not in ("active", "paused"):
            ride.complete(now, self.tariff)  # вызывает TransitionNotAllowed при неверном состоянии
        zone_route = self._resolve_bool_gateway(
            yes=zone_allowed, yes_result="allowed", no_result="denied"
        )
        if zone_route == "denied":
            ride.record_zone_denied(now)
            self.rides.save(ride)
            return ride, False
        if not photo_received:
            raise ValidationError("Для завершения поездки требуется фотография самоката")
        ride.add_event("PhotoReceived", now, "Фотография самоката получена")
        ride.complete(now, self.tariff)
        scooter = self.scooters.get(ride.scooter_id)
        scooter.finish_rental(ride.id)
        self.scooters.save(scooter)
        self.rides.save(ride)
        return ride, True

    def pay_for_ride(self, ride_id: str) -> Ride:
        ride = self.rides.get(ride_id)
        user = self.users.get(ride.user_id)
        now = self.clock.now()
        if ride.state != "completed_pending_payment":
            # Не делаем внешний платёжный вызов для неоплачиваемого состояния.
            # Метод состояния выбросит TransitionNotAllowed до побочного эффекта.
            ride.payment_succeeded(now)
        if ride.cost is None:
            raise DomainInvariantViolation("Стоимость завершённой поездки не рассчитана")
        succeeded = self.payment_gateway.charge(user.id, ride.cost)
        payment_route = self._resolve_bool_gateway(
            yes=succeeded, yes_result="success", no_result="failure"
        )
        if payment_route == "success":
            ride.payment_succeeded(now)
            self.rides.save(ride)
            return ride
        ride.payment_failed(now)
        user.block_for_debt(ride.cost, now)
        self.users.save(user)
        self.rides.save(ride)
        return ride

    def settle_user_debt(self, user_id: str) -> User:
        user = self.users.get(user_id)
        if not user.has_debt or user.debt_amount <= 0:
            raise DomainInvariantViolation("У клиента нет задолженности для погашения")
        now = self.clock.now()
        if self.payment_gateway.charge(user.id, user.debt_amount):
            user.settle_debt(now)
        else:
            user.add_event(DomainEvent.build("DebtPaymentFailed", now, "Оплата задолженности не прошла"))
        self.users.save(user)
        return user

    def operator_contacted(self, ride_id: str) -> Ride:
        ride = self.rides.get(ride_id)
        if not ride.operator_notified:
            raise DomainInvariantViolation("Нельзя зафиксировать связь: уведомление оператору ещё не отправлялось")
        ride.mark_operator_contacted(self.clock.now())
        self.rides.save(ride)
        return ride

    def on_timers(self, now: datetime | None = None) -> dict[str, list[str]]:
        """Обрабатывает истечение 10 минут и событие длительности более 12 часов."""
        current = now or self.clock.now()
        cancelled: list[str] = []
        operator_alerts: list[str] = []
        for ride in self.rides.all():
            if ride.state == "reserved" and current >= ride.reservation_expires_at:
                ride.cancel_on_timeout(current)
                scooter = self.scooters.get(ride.scooter_id)
                scooter.release_reservation(ride.id)
                self.scooters.save(scooter)
                self.rides.save(ride)
                cancelled.append(ride.id)
            elif ride.state in ("active", "paused") and ride.start_time is not None:
                if current - ride.start_time > timedelta(hours=OPERATOR_NOTICE_HOURS) and not ride.operator_notified:
                    ride.notify_operator(current)
                    ride.add_event("OperatorMessageSent", current, "Система отправила оператору уведомление о длительной поездке")
                    self.rides.save(ride)
                    operator_alerts.append(ride.id)
        return {"cancelled_reservations": cancelled, "operator_notifications": operator_alerts}
