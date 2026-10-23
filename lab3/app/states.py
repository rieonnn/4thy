"""GoF State: отдельный класс для каждого состояния жизненного цикла поездки."""
from __future__ import annotations

from .errors import TransitionNotAllowed


class RideState:
    name = "unknown"
    allowed_events: tuple[str, ...] = ()

    def _deny(self, event: str):
        raise TransitionNotAllowed(self.name, event)

    def start(self): return self._deny("start")
    def cancel_timeout(self): return self._deny("cancel_timeout")
    def reject_start(self): return self._deny("reject_start")
    def pause(self): return self._deny("pause")
    def resume(self): return self._deny("resume")
    def finish(self): return self._deny("finish")
    def payment_succeeded(self): return self._deny("payment_succeeded")
    def payment_failed(self): return self._deny("payment_failed")
    def operator_notified(self): return self._deny("operator_notified")
    def operator_contacted(self): return self._deny("operator_contacted")


class ReservedState(RideState):
    name = "reserved"
    allowed_events = ("start", "cancel_timeout", "reject_start")

    def start(self): return ActiveState()
    def cancel_timeout(self): return CancelledState()
    def reject_start(self): return RejectedState()


class ActiveState(RideState):
    name = "active"
    allowed_events = ("pause", "finish", "operator_notified", "operator_contacted")

    def pause(self): return PausedState()
    def finish(self): return CompletedPendingPaymentState()
    def operator_notified(self): return self
    def operator_contacted(self): return self


class PausedState(RideState):
    name = "paused"
    allowed_events = ("resume", "finish", "operator_notified", "operator_contacted")

    def resume(self): return ActiveState()
    def finish(self): return CompletedPendingPaymentState()
    def operator_notified(self): return self
    def operator_contacted(self): return self


class CompletedPendingPaymentState(RideState):
    name = "completed_pending_payment"
    allowed_events = ("payment_succeeded", "payment_failed", "operator_contacted")

    def payment_succeeded(self): return PaidState()
    def payment_failed(self): return DebtBlockedState()
    def operator_contacted(self): return self


class PaidState(RideState):
    name = "paid"
    allowed_events = ("operator_contacted",)

    def operator_contacted(self): return self


class DebtBlockedState(RideState):
    name = "debt_blocked"
    allowed_events = ("operator_contacted",)

    def operator_contacted(self): return self


class CancelledState(RideState):
    name = "cancelled"
    allowed_events = ()


class RejectedState(RideState):
    name = "rejected"
    allowed_events = ()


STATE_TYPES = {
    state.name: state for state in (
        ReservedState(), ActiveState(), PausedState(), CompletedPendingPaymentState(),
        PaidState(), DebtBlockedState(), CancelledState(), RejectedState()
    )
}
