from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar


class InverterController(ABC):
    """Everything the optimiser needs from an inverter brand.

    The abstract methods are required. The rest have working defaults: override them only if the
    inverter can do more (read a write back, take a current instead of a power, hold a backup reserve).
    """

    # Identity, used by setup and the config flow. Add a new controller to CONTROLLERS in inverters/__init__.py.
    brand: ClassVar[str]  # e.g. "solis"
    integration: ClassVar[str]  # the Home Assistant integration domain it drives, e.g. "solisconnect"
    # Entity name templates per entity type ("model_entities", "control_entities", "config_entities") keyed by
    # Miser's key. Setup maps them to the integration's entities; model_entities must include battery_soc.
    entity_defs: ClassVar[dict[str, dict[str, str]]] = {}

    def __init__(self, hass, integration_data: dict | None = None) -> None:
        self.hass = hass
        self.integration_data = integration_data or {}

    # True if a window must stay within one local day and avoid 00:00 edges; the optimiser then writes a
    # window that crosses midnight as 00:01-23:59 halves.
    day_bounded_windows: bool = False

    # True if the controller can put the inverter into a backup/reserve mode itself.
    supports_backup_mode: bool = False
    backup_mode: bool = False
    backup_soc: float | None = None

    @abstractmethod
    async def is_online(self) -> bool:
        """Return whether the inverter is reachable and ready for control."""

    @abstractmethod
    async def control_charge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        """Charge from the grid between start and end, up to target_soc, at power (W, battery side)."""

    @abstractmethod
    async def control_discharge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        """Discharge (export) between start and end, down to target_soc, at power (W, battery side)."""

    @abstractmethod
    async def control_idle(self) -> None:
        """Stop any forced charge or discharge and leave the inverter in its normal self-use mode."""

    @abstractmethod
    async def control_matches(self, state: str, target_soc: float | None, power: float) -> bool:
        """Return whether the inverter is already in `state` ("idle" is the only state asked about)."""

    async def confirm_control(
        self,
        state: str,
        start: datetime,
        end: datetime,
        target_soc: float,
        power: float,
    ) -> list[str]:
        """Read back a control just written and describe anything the inverter did not take.

        Returns an empty list when the inverter reports the requested window, target and power.
        The default can't read back, so every write counts as confirmed.
        """
        return []

    async def power_to_current(self, power: float) -> float | None:
        """Battery current (A) for a power (W), for inverters programmed in amps; None if not used."""
        return None

    def set_backup(self, on: bool, soc: float | None) -> None:
        """Miser's Backup Mode and reserve SOC, set before every control run."""
        self.backup_mode = on
        self.backup_soc = soc if on else None

    async def ensure_backup_soc(self) -> None:
        """Re-apply the backup reserve after a mode change, if the inverter resets it."""
