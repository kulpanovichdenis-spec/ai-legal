"""pytest configuration for async tests."""
import os
import pytest

# Выставляем до импорта main.py/config.py — подавляет production-валидацию
# конфигурации (MODEL_REGULATION, INTERNAL_API_URL), которая не нужна в тестах.
os.environ.setdefault("TESTING", "1")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )
