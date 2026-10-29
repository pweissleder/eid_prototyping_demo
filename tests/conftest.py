import pytest

from wakequest.clock import FakeClock

from .helpers import new_machine


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def m(clock):
    return new_machine(clock)
