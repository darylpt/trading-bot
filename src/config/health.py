"""Health information for the container runtime."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ContainerHealth(BaseModel):
    """Validated runtime health information exposed by the container."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["OK"]
    python_version: str
    timestamp: datetime


def check_container_health() -> ContainerHealth:
    """Return the current runtime status, interpreter version, and UTC time."""
    version = ".".join(str(part) for part in sys.version_info[:3])
    return ContainerHealth(
        status="OK",
        python_version=version,
        timestamp=datetime.now(timezone.utc),
    )


def main() -> None:
    """Emit a machine-readable health result for container probes."""
    sys.stdout.write(check_container_health().model_dump_json() + "\n")


if __name__ == "__main__":
    main()
