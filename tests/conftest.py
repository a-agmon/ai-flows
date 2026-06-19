"""Pytest configuration shared by the test suite."""

import inspect

import pytest


def pytest_collection_modifyitems(items):
    """Run async tests with AnyIO when pytest-asyncio is unavailable."""
    for item in items:
        if inspect.iscoroutinefunction(item.obj):
            item.add_marker(pytest.mark.anyio)


@pytest.fixture
def anyio_backend():
    return "asyncio"
