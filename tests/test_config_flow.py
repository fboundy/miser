"""Test the Miser config flow."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miser.const import DOMAIN


async def test_user_step_aborts_without_solcast(hass):
    """Test the flow aborts when Solcast Solar is not installed."""
    with patch("custom_components.miser.config_flow._is_installed", return_value=False):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "solcast_solar_not_installed"


async def test_duplicate_controller_aborts(hass):
    """Test a configured inverter controller cannot be added twice."""
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Miser PV System Optimiser",
        data={"inverter_brand": "solis"},
        unique_id=f"{DOMAIN}_solis_solis",
        options={"integration": "solis"},
    )
    entry.add_to_hass(hass)

    integration_entity = SimpleNamespace(
        entity_id="sensor.solis_inverter_solis_remaining_battery_capacity",
    )

    with (
        patch("custom_components.miser.config_flow._is_installed", new=AsyncMock(return_value=True)),
        patch(
            "custom_components.miser.config_flow._discover_installed_integrations",
            new=AsyncMock(return_value=["solis"]),
        ),
        patch(
            "custom_components.miser.config_flow._get_integration_name",
            new=AsyncMock(return_value="Solis"),
        ),
        patch(
            "custom_components.miser.config_flow.get_integration_entities",
            new=AsyncMock(return_value=({}, [integration_entity])),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={"inverter_brand": "Solis"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"inverter_integration": "Solis"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_controller_without_required_entities_shows_error(hass):
    """Test selecting a controller without required model entities shows an error."""
    with (
        patch("custom_components.miser.config_flow._is_installed", new=AsyncMock(return_value=True)),
        patch(
            "custom_components.miser.config_flow._discover_installed_integrations",
            new=AsyncMock(return_value=["solis"]),
        ),
        patch(
            "custom_components.miser.config_flow._get_integration_name",
            new=AsyncMock(return_value="Solis"),
        ),
        patch(
            "custom_components.miser.config_flow.get_integration_entities",
            new=AsyncMock(return_value=({}, [])),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={"inverter_brand": "Solis"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"inverter_integration": "Solis"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_controller"
    assert result["errors"] == {"base": "missing_controller_entities"}
