"""Flask HTTP-адаптер. Доменный и прикладной слои не импортируют Flask."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import Flask, jsonify, request

from .clock import ShiftableClock
from .domain import DomainEvent, ScooterStatus, Tariff
from .errors import (
    ClockMovedBackwards, DomainInvariantViolation, EntityNotFound,
    GatewayUnresolved, TransitionNotAllowed, ValidationError,
)
from .ports import InMemoryPaymentGateway
from .process import ScooterRentalProcess
from .repositories import RideRepository, ScooterRepository, UserRepository


def _jsonable_event(event: DomainEvent) -> dict:
    return {
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "message": event.message,
        "data": dict(event.data),
    }


def ride_json(ride) -> dict:
    return {
        "id": ride.id,
        "scooter_id": ride.scooter_id,
        "user_id": ride.user_id,
        "state": ride.state,
        "allowed_events": list(ride.allowed_events),
        "reserved_at": ride.reserved_at.isoformat(),
        "reservation_expires_at": ride.reservation_expires_at.isoformat(),
        "start_time": ride.start_time.isoformat() if ride.start_time else None,
        "end_time": ride.end_time.isoformat() if ride.end_time else None,
        "cost": str(ride.cost) if ride.cost is not None else None,
        "operator_notified": ride.operator_notified,
        "operator_contacted": ride.operator_contacted,
        "rejection_reason": ride.rejection_reason,
        "event_journal": [_jsonable_event(e) for e in ride.event_journal],
    }


def user_json(user) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "payment_data_configured": bool(user.payment_data),
        "has_debt": user.has_debt,
        "blocked": user.blocked,
        "debt_amount": str(user.debt_amount),
        "event_journal": [_jsonable_event(e) for e in user.event_journal],
    }


def create_app(config: dict | None = None, *, process: ScooterRentalProcess | None = None, clock=None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        PORT=int(os.getenv("PORT", "8000")),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "info").upper(),
        ENABLE_TEST_CONTROLS=os.getenv("ENABLE_TEST_CONTROLS", "false").lower() == "true",
    )
    if config:
        app.config.update(config)

    logging.basicConfig(level=getattr(logging, str(app.config["LOG_LEVEL"]).upper(), logging.INFO))

    if process is None:
        clock = clock or ShiftableClock(os.getenv("APP_INITIAL_TIME") or None)
        payment = InMemoryPaymentGateway()
        tariff = Tariff(
            price_per_minute=Decimal(os.getenv("PRICE_PER_MINUTE", "5.00")),
            minimum_cost=Decimal(os.getenv("MINIMUM_COST", "50.00")),
            pause_price_per_minute=Decimal(os.getenv("PAUSE_PRICE_PER_MINUTE", "1.00")),
        )
        process = ScooterRentalProcess(
            clock=clock,
            rides=RideRepository(),
            scooters=ScooterRepository(),
            users=UserRepository(),
            payment_gateway=payment,
            tariff=tariff,
        )
    else:
        clock = clock or process.clock
        payment = process.payment_gateway

    # Объекты создаются один раз при старте приложения.
    app.extensions["rental_process"] = process
    app.extensions["app_clock"] = clock
    app.extensions["payment_gateway"] = payment

    def body() -> dict:
        data = request.get_json(silent=True)
        if data is None or not isinstance(data, dict):
            raise ValidationError("Тело запроса должно быть JSON-объектом")
        return data

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @app.post("/users")
    def create_user():
        data = body()
        user = process.create_user(data.get("full_name"), data.get("payment_data", "demo-payment-method"))
        return jsonify(user_json(user)), 201

    @app.get("/users/<user_id>")
    def get_user(user_id):
        return jsonify(user_json(process.users.get(user_id))), 200

    @app.post("/users/<user_id>/settle-debt")
    def settle_debt(user_id):
        user = process.settle_user_debt(user_id)
        return jsonify(user_json(user)), 200

    @app.post("/scooters")
    def create_scooter():
        data = body()
        if "battery_percent" not in data:
            raise ValidationError("Поле battery_percent обязательно")
        scooter = process.create_scooter(data["battery_percent"])
        return jsonify({
            "id": scooter.id,
            "battery_percent": scooter.battery_percent,
            "state": scooter.state.value,
            "current_ride_id": scooter.current_ride_id,
        }), 201

    @app.get("/scooters/<scooter_id>")
    def get_scooter(scooter_id):
        scooter = process.scooters.get(scooter_id)
        return jsonify({
            "id": scooter.id,
            "battery_percent": scooter.battery_percent,
            "state": scooter.state.value,
            "current_ride_id": scooter.current_ride_id,
            "reservation_ride_id": scooter.reservation_ride_id,
        }), 200

    @app.post("/rides")
    def book_ride():
        data = body()
        if not isinstance(data.get("scooter_id"), str) or not isinstance(data.get("user_id"), str):
            raise ValidationError("Поля scooter_id и user_id обязательны и должны быть строками")
        ride = process.book_ride(data["scooter_id"], data["user_id"])
        return jsonify(ride_json(ride)), 201

    @app.get("/rides/<ride_id>")
    def get_ride(ride_id):
        return jsonify(ride_json(process.rides.get(ride_id))), 200

    @app.post("/rides/<ride_id>/start")
    def start_ride(ride_id):
        ride = process.start_ride(ride_id)
        status = 200 if ride.state == "active" else 200
        return jsonify(ride_json(ride)), status

    @app.post("/rides/<ride_id>/pause")
    def pause_ride(ride_id):
        return jsonify(ride_json(process.pause_ride(ride_id))), 200

    @app.post("/rides/<ride_id>/resume")
    def resume_ride(ride_id):
        return jsonify(ride_json(process.resume_ride(ride_id))), 200

    @app.post("/rides/<ride_id>/finish")
    def finish_ride(ride_id):
        data = body()
        if "zone_allowed" not in data or "photo_received" not in data:
            raise ValidationError("Поля zone_allowed и photo_received обязательны")
        ride, accepted = process.finish_ride(ride_id, data["zone_allowed"], data["photo_received"])
        return jsonify({"result": "completed_pending_payment" if accepted else "zone_not_allowed", "ride": ride_json(ride)}), 200

    @app.post("/rides/<ride_id>/pay")
    def pay_ride(ride_id):
        return jsonify(ride_json(process.pay_for_ride(ride_id))), 200

    @app.post("/rides/<ride_id>/operator-contact")
    def operator_contact(ride_id):
        return jsonify(ride_json(process.operator_contacted(ride_id))), 200

    @app.post("/internal/tick")
    def tick():
        raw = request.get_json(silent=True)
        if raw is None:
            data = {}
        elif not isinstance(raw, dict):
            raise ValidationError("Тело запроса должно быть JSON-объектом")
        else:
            data = raw
        now = clock.set_time(data.get("now") if "now" in data else None)
        results = process.on_timers(now)
        return jsonify({"now": now.isoformat(), **results}), 200

    @app.post("/internal/payment-outcome")
    def payment_outcome():
        # Только демонстрационный переключатель заглушки; выключен по умолчанию.
        if not app.config.get("ENABLE_TEST_CONTROLS"):
            return jsonify({"error": "not_found", "message": "Маршрут недоступен"}), 404
        data = body()
        if "authorization_succeeds" in data:
            if not isinstance(data["authorization_succeeds"], bool):
                raise ValidationError("authorization_succeeds должен быть boolean")
            payment.next_authorization_result = data["authorization_succeeds"]
        if "charge_succeeds" in data:
            if not isinstance(data["charge_succeeds"], bool):
                raise ValidationError("charge_succeeds должен быть boolean")
            payment.next_charge_result = data["charge_succeeds"]
        return jsonify({"status": "configured_for_next_payment_only"}), 200

    @app.errorhandler(TransitionNotAllowed)
    def handle_transition(exc):
        return jsonify({"error": "transition_not_allowed", "message": str(exc)}), 409

    @app.errorhandler(ValidationError)
    @app.errorhandler(DomainInvariantViolation)
    def handle_validation(exc):
        return jsonify({"error": "unprocessable_content", "message": str(exc)}), 422

    @app.errorhandler(EntityNotFound)
    def handle_missing_entity(exc):
        return jsonify({"error": "not_found", "message": str(exc)}), 404

    @app.errorhandler(ClockMovedBackwards)
    def handle_clock_backwards(exc):
        return jsonify({"error": "unprocessable_content", "message": str(exc)}), 422

    @app.errorhandler(GatewayUnresolved)
    def handle_gateway(exc):
        app.logger.exception("GatewayUnresolved")
        return jsonify({"error": "gateway_unresolved", "message": str(exc)}), 500

    @app.errorhandler(404)
    def handle_not_found(_exc):
        return jsonify({"error": "not_found", "message": "Маршрут или сущность не найдены"}), 404

    @app.errorhandler(500)
    def handle_internal(_exc):
        return jsonify({"error": "internal_error", "message": "Внутренняя ошибка сервера"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
