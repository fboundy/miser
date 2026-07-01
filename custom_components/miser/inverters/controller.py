from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class InverterController(ABC):
    """Abstract interface for inverter control implementations."""

    @abstractmethod
    async def is_online(self) -> bool:
        """Return whether the inverter is reachable and ready for control."""

    @abstractmethod
    async def get_time(self) -> datetime:
        """Return the inverter's current clock time."""

    @abstractmethod
    async def set_time(self, time: datetime) -> None:
        """Set the inverter's current clock time."""

    @abstractmethod
    async def control_charge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        """Configure a timed charge period."""

    @abstractmethod
    async def control_discharge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        """Configure a timed discharge period."""

    @abstractmethod
    async def get_status(self) -> dict[str, Any]:
        """Return the inverter's current status."""

    @abstractmethod
    async def control_matches(self, state: str, target_soc: float | None, power: float) -> bool:
        """Return whether the inverter control state matches the requested force state."""

    @abstractmethod
    async def set_mode(self, mode: str) -> None:
        """Set the inverter operating mode."""

    @abstractmethod
    async def get_mode(self) -> str:
        """Return the inverter operating mode."""
