"""The InverterController base class is the whole contract a new brand implements (#15)."""

import pytest

from custom_components.miser.inverters.controller import InverterController
from custom_components.miser.inverters.solis import SolisCloudInverter, SolisConnectInverter, SolisSolaxModbusInverter


class _NoIdle(InverterController):
    async def is_online(self):
        return True

    async def control_charge(self, start, end, target_soc, power):
        pass

    async def control_discharge(self, start, end, target_soc, power):
        pass

    async def control_matches(self, state, target_soc, power):
        return True


def test_a_brand_without_control_idle_cannot_be_created() -> None:
    with pytest.raises(TypeError, match="control_idle"):
        _NoIdle()


@pytest.mark.asyncio
async def test_optional_methods_have_working_defaults() -> None:
    class _Minimal(_NoIdle):
        async def control_idle(self):
            pass

    controller = _Minimal()
    assert await controller.confirm_control("charging", None, None, 100, 3000) == []
    assert await controller.power_to_current(3000) is None
    assert controller.supports_backup_mode is False
    controller.set_backup(True, 40)
    assert (controller.backup_mode, controller.backup_soc) == (True, 40)
    controller.set_backup(False, 40)
    assert controller.backup_soc is None


@pytest.mark.parametrize("cls", [SolisCloudInverter, SolisConnectInverter, SolisSolaxModbusInverter])
def test_solis_controllers_meet_the_contract(cls) -> None:
    assert not cls.__abstractmethods__
