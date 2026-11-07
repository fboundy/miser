from .controller import InverterController
from .solis import SolisCloudInverter, SolisConnectInverter, SolisInverter, SolisSolaxModbusInverter

# Every controller Miser offers. A new brand subclasses InverterController and is added here.
CONTROLLERS: list[type[InverterController]] = [
    SolisSolaxModbusInverter,
    SolisCloudInverter,
    SolisConnectInverter,
]

INVERTER_CONTROLLER_CLASSES: dict[str, dict[str, type[InverterController]]] = {}
for _controller in CONTROLLERS:
    INVERTER_CONTROLLER_CLASSES.setdefault(_controller.brand, {})[_controller.integration] = _controller

# Brand -> integration -> entity templates, for the config flow.
INVERTER_DEFS = {
    brand: {integration: controller.entity_defs for integration, controller in integrations.items()}
    for brand, integrations in INVERTER_CONTROLLER_CLASSES.items()
}


def get_inverter_controller_class(brand: str, integration: str) -> type[InverterController]:
    return INVERTER_CONTROLLER_CLASSES[brand][integration]


__all__ = [
    "CONTROLLERS",
    "INVERTER_CONTROLLER_CLASSES",
    "INVERTER_DEFS",
    "InverterController",
    "SolisCloudInverter",
    "SolisConnectInverter",
    "SolisInverter",
    "SolisSolaxModbusInverter",
    "get_inverter_controller_class",
]
