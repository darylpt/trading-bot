"""Safe container entrypoint; full broker orchestration is added later."""

from __future__ import annotations

import logging
import os
import time
from decimal import Decimal
from typing import Literal

from pydantic import AnyHttpUrl, TypeAdapter

from config.settings import Settings


LOGGER = logging.getLogger(__name__)


def _boolean_env(name: str) -> bool:
    value = os.environ[name].lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _broker_environment(value: str) -> Literal["paper", "demo"]:
    if value == "paper":
        return "paper"
    if value == "demo":
        return "demo"
    raise ValueError("BROKER_ENV must be paper or demo")


def main() -> None:
    settings = Settings(
        paper_trading=_boolean_env("PAPER_TRADING"),
        live_trading=_boolean_env("LIVE_TRADING"),
        broker_environment=_broker_environment(os.environ["BROKER_ENV"]),
        broker_endpoint=TypeAdapter(AnyHttpUrl).validate_python(
            os.environ["BROKER_ENDPOINT"]
        ),
        daily_drawdown_limit=Decimal(os.environ["DAILY_DRAWDOWN_LIMIT"]),
    )
    logging.basicConfig(level=logging.INFO)
    LOGGER.info(
        "paper/demo runtime ready provider=%s data_dir=%s",
        settings.provider,
        settings.data_dir,
    )
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
