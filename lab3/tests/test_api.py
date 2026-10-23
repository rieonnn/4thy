"""HTTP tests for runtime environments where Flask is installed."""
import pytest
flask = pytest.importorskip("flask")
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.ports import InMemoryPaymentGateway
from app.process import ScooterRentalProcess
from app.repositories import RideRepository, ScooterRepository, UserRepository
from app.domain import Tariff
from app.web import create_app


class FrozenClock:
    def __init__(self): self.current = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
    def now(self): return self.current
    def set_time(self, target=None):
        if target is None: return self.current
        from app.clock import parse_datetime
        target = parse_datetime(target)
        if target < self.current:
            from app.errors import ClockMovedBackwards
            raise ClockMovedBackwards('clock backwards')
        self.current = target
        return self.current


def make_client():
    clock = FrozenClock()
    gateway = InMemoryPaymentGateway()
    process = ScooterRentalProcess(clock, RideRepository(), ScooterRepository(), UserRepository(), gateway,
                                   Tariff(Decimal('5'), Decimal('50'), Decimal('1')))
    app = create_app({"TESTING": True, "ENABLE_TEST_CONTROLS": True}, process=process, clock=clock)
    return app.test_client(), process, gateway, clock


def create_ride(client, battery=80):
    user = client.post('/users', json={'full_name': 'Иван'}).get_json()
    scooter = client.post('/scooters', json={'battery_percent': battery}).get_json()
    ride = client.post('/rides', json={'user_id': user['id'], 'scooter_id': scooter['id']}).get_json()
    return user, scooter, ride


def test_api_basic_happy_path_and_get_state():
    client, _, _, _ = make_client()
    user, scooter, reservation = create_ride(client)
    ride_id = reservation['id']
    assert client.post(f'/rides/{ride_id}/start', json={}).status_code == 200
    assert client.post(f'/rides/{ride_id}/pause', json={}).status_code == 200
    assert client.post(f'/rides/{ride_id}/resume', json={}).status_code == 200
    assert client.post(f'/rides/{ride_id}/finish', json={'zone_allowed': True, 'photo_received': True}).status_code == 200
    assert client.post(f'/rides/{ride_id}/pay', json={}).status_code == 200
    current = client.get(f'/rides/{ride_id}').get_json()
    assert current['state'] == 'paid'
    assert current['event_journal']


def test_http_status_codes_for_invalid_and_missing_data():
    client, _, _, _ = make_client()
    assert client.post('/scooters', json={'battery_percent': 101}).status_code == 422
    assert client.get('/rides/no-such-ride').status_code == 404
    user, scooter, ride = create_ride(client)
    before = client.get(f"/rides/{ride['id']}").get_json()
    bad_photo = client.post(f"/rides/{ride['id']}/finish", json={'zone_allowed': True, 'photo_received': False})
    assert bad_photo.status_code == 409 or bad_photo.status_code == 422
    after = client.get(f"/rides/{ride['id']}").get_json()
    assert before['state'] == after['state'] == 'reserved'


def test_three_disallowed_http_events_return_409_without_state_change():
    client, _, _, clock = make_client()
    user, scooter, reserved = create_ride(client)
    rid = reserved['id']

    # 1) Пауза в состоянии reserved.
    before = client.get(f'/rides/{rid}').get_json()
    response = client.post(f'/rides/{rid}/pause', json={})
    after = client.get(f'/rides/{rid}').get_json()
    assert response.status_code == 409 and before['state'] == after['state'] == 'reserved'

    # 2) Возобновление в состоянии active, когда пауза не была включена.
    assert client.post(f'/rides/{rid}/start', json={}).status_code == 200
    before = client.get(f'/rides/{rid}').get_json()
    response = client.post(f'/rides/{rid}/resume', json={})
    after = client.get(f'/rides/{rid}').get_json()
    assert response.status_code == 409 and before['state'] == after['state'] == 'active'

    # 3) Пауза после перехода в состояние completed_pending_payment.
    finish = client.post(f'/rides/{rid}/finish', json={'zone_allowed': True, 'photo_received': True})
    assert finish.status_code == 200
    before = client.get(f'/rides/{rid}').get_json()
    response = client.post(f'/rides/{rid}/pause', json={})
    after = client.get(f'/rides/{rid}').get_json()
    assert response.status_code == 409
    assert before['state'] == after['state'] == 'completed_pending_payment'


def test_http_timer_expiry_and_non_interrupting_operator_notification():
    client, _, _, clock = make_client()
    user, scooter, reserved = create_ride(client)
    expiry = reserved['reservation_expires_at']
    response = client.post('/internal/tick', json={'now': expiry})
    assert response.status_code == 200
    assert client.get(f"/rides/{reserved['id']}").get_json()['state'] == 'cancelled'

    user, scooter, ride = create_ride(client)
    assert client.post(f"/rides/{ride['id']}/start", json={}).status_code == 200
    started = client.get(f"/rides/{ride['id']}").get_json()['start_time']
    from app.clock import parse_datetime
    target = parse_datetime(started) + timedelta(hours=12, seconds=1)
    response = client.post('/internal/tick', json={'now': target.isoformat()})
    assert response.status_code == 200
    after = client.get(f"/rides/{ride['id']}").get_json()
    assert after['state'] == 'active' and after['operator_notified'] is True


def test_clock_backwards_returns_422():
    client, _, _, _ = make_client()
    response = client.post('/internal/tick', json={'now': '2026-10-22T08:00:00Z'})
    assert response.status_code == 422
