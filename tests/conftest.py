"""pytest конфиг — общие опции для всех тестов.

--run-llm включает тесты с реальной LLM (с пометкой @pytest.mark.llm).
"""
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-llm",
        action="store_true",
        default=False,
        help="Run tests with real LLM API (costs money, requires API keys)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: mark test as requiring real LLM API")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-llm"):
        return
    skip = pytest.mark.skip(reason="Need --run-llm to run")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)
