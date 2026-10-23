"""Ошибки доменного и прикладного слоёв."""


class TransitionNotAllowed(Exception):
    def __init__(self, state: str, event: str):
        self.state = state
        self.event = event
        super().__init__(f"Событие '{event}' недопустимо в состоянии '{state}'")


class DomainInvariantViolation(Exception):
    """Нарушен инвариант доменной модели."""


class ValidationError(DomainInvariantViolation):
    """Некорректные данные запроса или объект-значение."""


class EntityNotFound(Exception):
    """Запрошенная сущность отсутствует в репозитории."""


class ClockMovedBackwards(Exception):
    """Часы приложения нельзя переводить назад."""


class GatewayUnresolved(Exception):
    """Ни одно условие XOR-шлюза не сработало, и поток по умолчанию отсутствует."""
