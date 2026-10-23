from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.clock import ShiftableClock
from app.domain import Scooter, ScooterStatus, Tariff
from app.errors import ClockMovedBackwards, DomainInvariantViolation, GatewayUnresolved, TransitionNotAllowed, ValidationError
from app.ports import InMemoryPaymentGateway
from app.process import ScooterRentalProcess
from app.repositories import RideRepository, ScooterRepository, UserRepository


class FrozenClock:
    def __init__(self, start): self.current = start
    def now(self): return self.current
    def advance(self, **kwargs): self.current += timedelta(**kwargs); return self.current


def make_process(start=None):
    clock = FrozenClock(start or datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc))
    payments = InMemoryPaymentGateway()
    process = ScooterRentalProcess(clock, RideRepository(), ScooterRepository(), UserRepository(), payments,
                                   Tariff(Decimal('5'), Decimal('50'), Decimal('1')))
    user = process.create_user('Тестовый клиент')
    scooter = process.create_scooter(80)
    ride = process.book_ride(scooter.id, user.id)
    return clock, payments, process, user, scooter, ride


def test_happy_path_pause_resume_and_payment_success():
    clock, payments, process, user, scooter, ride = make_process()
    started = process.start_ride(ride.id)
    assert started.state == 'active'
    assert scooter.state == ScooterStatus.RENTED
    clock.advance(minutes=5)
    process.pause_ride(ride.id)
    clock.advance(minutes=3)
    process.resume_ride(ride.id)
    clock.advance(minutes=10)
    finished, accepted = process.finish_ride(ride.id, zone_allowed=True, photo_received=True)
    assert accepted is True
    assert finished.state == 'completed_pending_payment'
    assert finished.end_time >= finished.start_time
    assert scooter.state == ScooterStatus.FREE
    cost = finished.cost
    assert cost >= Decimal('50.00')
    process.pay_for_ride(ride.id)
    assert ride.state == 'paid'
    assert user.blocked is False
    assert any(e.event_type == 'PaymentSucceeded' for e in ride.event_journal)


def test_reservation_timeout_cancels_and_releases_scooter():
    clock, _, process, _, scooter, ride = make_process()
    now = clock.advance(minutes=10)
    result = process.on_timers(now)
    assert ride.id in result['cancelled_reservations']
    assert ride.state == 'cancelled'
    assert scooter.state == ScooterStatus.FREE
    assert 'start' not in ride.allowed_events


def test_existing_debt_rejects_start_and_releases_reservation():
    _, _, process, user, scooter, ride = make_process()
    user.has_debt = True
    result = process.start_ride(ride.id)
    assert result.state == 'rejected'
    assert scooter.state == ScooterStatus.FREE
    assert result.start_time is None


def test_failed_preauthorization_rejects_start():
    _, payments, process, _, scooter, ride = make_process()
    payments.next_authorization_result = False
    result = process.start_ride(ride.id)
    assert result.state == 'rejected'
    assert scooter.state == ScooterStatus.FREE
    assert any(e.event_type == 'PreauthorizationFailed' for e in result.event_journal)


def test_payment_failure_closes_trip_but_blocks_account():
    clock, payments, process, user, scooter, ride = make_process()
    process.start_ride(ride.id)
    clock.advance(minutes=30)
    process.finish_ride(ride.id, zone_allowed=True, photo_received=True)
    payments.next_charge_result = False
    process.pay_for_ride(ride.id)
    assert ride.state == 'debt_blocked'
    assert ride.end_time is not None
    assert scooter.state == ScooterStatus.FREE
    assert user.has_debt and user.blocked
    assert user.debt_amount == ride.cost


def test_zone_rejection_keeps_trip_active():
    _, _, process, _, scooter, ride = make_process()
    process.start_ride(ride.id)
    result, accepted = process.finish_ride(ride.id, zone_allowed=False, photo_received=False)
    assert accepted is False
    assert result.state == 'active'
    assert result.end_time is None
    assert scooter.state == ScooterStatus.RENTED
    assert any(e.event_type == 'FinishDeniedWrongZone' for e in result.event_journal)


def test_invalid_event_does_not_change_state():
    _, _, process, _, scooter, ride = make_process()
    before = ride.state
    with pytest.raises(TransitionNotAllowed):
        process.pause_ride(ride.id)
    assert ride.state == before == 'reserved'
    assert scooter.state == ScooterStatus.RESERVED


def test_duplicate_finish_is_rejected():
    clock, _, process, _, _, ride = make_process()
    process.start_ride(ride.id)
    clock.advance(minutes=2)
    process.finish_ride(ride.id, zone_allowed=True, photo_received=True)
    snapshot = (ride.state, ride.end_time, ride.cost, len(ride.event_journal))
    with pytest.raises(TransitionNotAllowed):
        process.finish_ride(ride.id, zone_allowed=True, photo_received=True)
    assert (ride.state, ride.end_time, ride.cost, len(ride.event_journal)) == snapshot


def test_operator_timer_fires_once_without_ending_ride():
    clock, _, process, _, _, ride = make_process()
    process.start_ride(ride.id)
    now = clock.advance(hours=12, seconds=1)
    result = process.on_timers(now)
    assert result['operator_notifications'] == [ride.id]
    assert ride.state == 'active'
    second = process.on_timers(now + timedelta(minutes=1))
    assert second['operator_notifications'] == []
    assert ride.operator_notified


def test_low_battery_rejected_by_domain():
    from app.domain import Scooter
    from app.errors import ValidationError
    with pytest.raises(ValidationError):
        Scooter(id='s1', battery_percent=101)


def test_clock_does_not_move_backwards():
    class FixedSystemClock:
        def now(self): return datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
    clock = ShiftableClock('2026-10-23T08:00:00Z', system_clock=FixedSystemClock())
    clock.set_time('2026-10-23T09:00:00Z')
    with pytest.raises(ClockMovedBackwards):
        clock.set_time('2026-10-23T08:30:00Z')


def test_pause_rate_is_lower_than_regular_rate():
    tariff = Tariff(Decimal('5'), Decimal('0'), Decimal('1'))
    amount = tariff.calculate_with_pause(active_seconds=10 * 60, paused_seconds=10 * 60)
    assert amount == Decimal('60.00')


def test_start_after_timer_expires_cancels_reservation():
    clock, _, process, _, scooter, ride = make_process()
    clock.advance(minutes=10)
    result = process.start_ride(ride.id)
    assert result.state == 'cancelled'
    assert scooter.state == ScooterStatus.FREE


def test_user_with_debt_can_book_but_cannot_start():
    clock = FrozenClock(datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc))
    payments = InMemoryPaymentGateway()
    process = ScooterRentalProcess(clock, RideRepository(), ScooterRepository(), UserRepository(), payments,
                                   Tariff(Decimal('5'), Decimal('50'), Decimal('1')))
    user = process.create_user('Клиент с задолженностью')
    user.has_debt = True
    user.blocked = True
    scooter = process.create_scooter(80)
    ride = process.book_ride(scooter.id, user.id)
    assert ride.state == 'reserved'
    result = process.start_ride(ride.id)
    assert result.state == 'rejected'
    assert scooter.state == ScooterStatus.FREE
    assert not payments.authorizations


def test_repeated_payment_is_rejected_before_gateway_side_effect():
    _, payments, process, _, _, ride = make_process()
    process.start_ride(ride.id)
    process.finish_ride(ride.id, zone_allowed=True, photo_received=True)
    process.pay_for_ride(ride.id)
    calls_before = len(payments.charges)
    snapshot = (ride.state, len(ride.event_journal))
    with pytest.raises(TransitionNotAllowed):
        process.pay_for_ride(ride.id)
    assert len(payments.charges) == calls_before
    assert (ride.state, len(ride.event_journal)) == snapshot


def test_unresolved_gateway_outcome_raises_gateway_unresolved_without_starting():
    _, payments, process, _, scooter, ride = make_process()
    payments.authorize = lambda user_id, amount: None
    with pytest.raises(GatewayUnresolved):
        process.start_ride(ride.id)
    assert ride.state == 'reserved'
    assert scooter.state == ScooterStatus.RESERVED
    assert not any(e.event_type in ('LockUnlocked', 'TariffingStarted', 'RideStarted') for e in ride.event_journal)
