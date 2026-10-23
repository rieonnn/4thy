"""Простые in-memory репозитории, соответствующие требованиям ЛР3."""
from __future__ import annotations
from .domain import Ride, Scooter, User
from .errors import EntityNotFound


class RideRepository:
    def __init__(self): self._items: dict[str, Ride] = {}
    def add(self, item: Ride) -> None: self._items[item.id] = item
    def save(self, item: Ride) -> None: self._items[item.id] = item
    def get(self, item_id: str) -> Ride:
        try: return self._items[item_id]
        except KeyError as exc: raise EntityNotFound(f"Поездка '{item_id}' не найдена") from exc
    def all(self) -> list[Ride]: return list(self._items.values())


class ScooterRepository:
    def __init__(self): self._items: dict[str, Scooter] = {}
    def add(self, item: Scooter) -> None: self._items[item.id] = item
    def save(self, item: Scooter) -> None: self._items[item.id] = item
    def get(self, item_id: str) -> Scooter:
        try: return self._items[item_id]
        except KeyError as exc: raise EntityNotFound(f"Самокат '{item_id}' не найден") from exc
    def all(self) -> list[Scooter]: return list(self._items.values())


class UserRepository:
    def __init__(self): self._items: dict[str, User] = {}
    def add(self, item: User) -> None: self._items[item.id] = item
    def save(self, item: User) -> None: self._items[item.id] = item
    def get(self, item_id: str) -> User:
        try: return self._items[item_id]
        except KeyError as exc: raise EntityNotFound(f"Клиент '{item_id}' не найден") from exc
    def all(self) -> list[User]: return list(self._items.values())
