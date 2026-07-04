from .controller import InverterController
from .solis import (
    SolisCloudInverter,
    SolisConnectInverter,
    SolisInverter,
    SolisSolaxModbusInverter,
    get_solis_inverter_class,
    get_solis_inverter_defs,
)

INVERTER_CONTROLLER_CLASSES = {
    SolisInverter.brand: {
        SolisSolaxModbusInverter.integration: SolisSolaxModbusInverter,
        SolisCloudInverter.integration: SolisCloudInverter,
        SolisConnectInverter.integration: SolisConnectInverter,
    }
}

INVERTER_DEFS = {
    SolisInverter.brand: get_solis_inverter_defs(),
}


def get_inverter_controller_class(brand: str, integration: str) -> type[InverterController]:
    if brand == SolisInverter.brand:
        return get_solis_inverter_class(integration)

    return INVERTER_CONTROLLER_CLASSES[brand][integration]


__all__ = [
    "INVERTER_CONTROLLER_CLASSES",
    "INVERTER_DEFS",
    "InverterController",
    "SolisCloudInverter",
    "SolisConnectInverter",
    "SolisInverter",
    "SolisSolaxModbusInverter",
    "get_inverter_controller_class",
]
