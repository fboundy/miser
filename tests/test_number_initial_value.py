"""Setup's system parameters seed Miser's number entities (#11)."""

from custom_components.miser.const import CONF_BATTERY_CAPACITY, DEFAULTS
from custom_components.miser.number import initial_value


def test_setup_value_seeds_the_entity() -> None:
    assert initial_value({CONF_BATTERY_CAPACITY: 20000}, CONF_BATTERY_CAPACITY, DEFAULTS[CONF_BATTERY_CAPACITY]) == 20000


def test_default_when_setup_did_not_ask_or_left_it_blank() -> None:
    assert initial_value({}, CONF_BATTERY_CAPACITY, 10000) == 10000
    assert initial_value({CONF_BATTERY_CAPACITY: None}, CONF_BATTERY_CAPACITY, 10000) == 10000
