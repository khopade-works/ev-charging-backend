import pytest

from tests import stores


@pytest.fixture(autouse=True)
def _close_stores():
    yield
    while stores.opened:
        stores.opened.pop().close()
