from cache import Cache
from config import DEFAULT_TTL


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_default_ttl_is_loaded_from_config() -> None:
    assert Cache().ttl == DEFAULT_TTL


def test_value_expires_at_boundary_and_is_removed() -> None:
    clock = FakeClock()
    cache = Cache(ttl=5, clock=clock)
    cache.set("answer", 42)
    assert cache.get("answer") == 42

    clock.now = 105.0
    assert cache.get("answer") is None
    assert "answer" not in cache.values
