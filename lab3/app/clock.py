"""Часы приложения. Доменный и прикладной код получает время только через Clock."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Protocol

from .errors import ClockMovedBackwards, ValidationError


class Clock(Protocol):
    def now(self) -> datetime:
        """Возвращает текущее время с часов приложения."""


class SystemClock:
    def now(self) -> datetime:
        # datetime.now используется только в инфраструктурном адаптере часов.
        return datetime.now(timezone.utc)


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError("Время должно быть в формате ISO 8601") from exc
    else:
        raise ValidationError("Поле времени должно быть строкой ISO 8601")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class ShiftableClock:
    """Системные часы со сдвигом, пригодные для проверки таймеров."""

    def __init__(self, initial_time: datetime | str | None = None, system_clock: Clock | None = None):
        self._system_clock = system_clock or SystemClock()
        self._lock = RLock()
        real_now = self._system_clock.now()
        if initial_time is None:
            self._offset_seconds = 0.0
        else:
            target = parse_datetime(initial_time)
            self._offset_seconds = (target - real_now).total_seconds()

    def now(self) -> datetime:
        with self._lock:
            from datetime import timedelta
            return self._system_clock.now() + timedelta(seconds=self._offset_seconds)

    def set_time(self, target: datetime | str | None = None) -> datetime:
        """Переводит эффективное время вперёд; перевод назад запрещён."""
        with self._lock:
            current = self.now()
            target_dt = self.now() if target is None else parse_datetime(target)
            if target_dt < current:
                raise ClockMovedBackwards(
                    f"Нельзя перевести часы назад: текущее время {current.isoformat()}, "
                    f"запрошено {target_dt.isoformat()}"
                )
            real_now = self._system_clock.now()
            self._offset_seconds = (target_dt - real_now).total_seconds()
            return target_dt
