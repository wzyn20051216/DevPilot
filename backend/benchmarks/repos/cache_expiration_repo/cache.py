import time
from typing import Callable


class Cache:
    def __init__(self, ttl: int = 0, clock: Callable[[], float] = time.time) -> None:
        self.ttl = ttl
        self.clock = clock
        self.values: dict[str, tuple[object, float]] = {}

    def set(self, key: str, value: object) -> None:
        self.values[key] = (value, self.clock())

    def get(self, key: str) -> object | None:
        entry = self.values.get(key)
        return None if entry is None else entry[0]
