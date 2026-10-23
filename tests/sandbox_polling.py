"""Opt-in test-only workaround for missed selector wakeups in restricted sandboxes.

Enable explicitly: pytest -p tests.sandbox_polling
This changes selector polling only in the test process; network prohibitions remain active.
"""

import selectors
from typing import Any

import pytest

_patch = pytest.MonkeyPatch()


def pytest_configure(config: pytest.Config) -> None:
    original_select = selectors.DefaultSelector.select

    def bounded_select(
        selector: Any, timeout: float | None = None
    ) -> list[tuple[selectors.SelectorKey, int]]:
        return original_select(selector, 0.02 if timeout is None else min(timeout, 0.02))

    _patch.setattr(selectors.DefaultSelector, "select", bounded_select)


def pytest_unconfigure(config: pytest.Config) -> None:
    _patch.undo()
