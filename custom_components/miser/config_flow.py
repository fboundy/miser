from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_NAME
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.loader import async_get_custom_components

import voluptuous as vol
import logging

from .const import (
    DOMAIN,
    NAME,
    VERSION,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_INVERTER_POWER,
    DEFAULT_CHARGER_POWER,
    DEFAULT_INVERTER_EFFICIENCY,
    DEFAULT_CHARGER_EFFICIENCY,
    CONF_BATTERY_CAPACITY,
    CONF_INVERTER_POWER,
    CONF_CHARGER_POWER,
    CONF_INVERTER_EFFICIENCY,
    CONF_CHARGER_EFFICIENCY,
)
import logging

_LOGGER = logging.getLogger(__name__)

AVAILABLE_INTEGRATIONS = ["solis", "solax_modbus", "solisconnect", "solarman"]


async def _is_installed(hass, integration: str) -> bool:
    try:
        integrations = await async_get_custom_components(hass)
        _LOGGER.debug(f"Integrations: {integrations}")
        result = integration in integrations
        _LOGGER.debug(f"{integration}: {result}")
        return result

    except Exception:
        return False


async def _discover_installed_integrations(hass):
    """
    Discover which of the known inverter integrations are installed.
    """

    try:
        custom_components = await async_get_custom_components(hass)
        installed_integrations = [
            integration for integration in AVAILABLE_INTEGRATIONS if integration in custom_components
        ]
        return installed_integrations
    except Exception:
        return []


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Handle a config flow for the integration.
    """

    VERSION = 1

    def __init__(self):
        self._discovered_integrations = []
        self._use_octopus_energy = False
        self._tariff_source = None

    async def async_step_user(self, user_input=None):
        """
        Step 1: Select inverter controller integration.
        """
        errors = {}

        # Check if Solcast Solar is installed
        if not await _is_installed(self.hass, "solcast_solar"):
            return self.async_abort(reason="solcast_solar_not_installed")

        if user_input is not None:
            # Validate user input
            selected_integration = user_input.get("inverter_integration")
            if selected_integration in self._discovered_integrations:
                self._use_octopus_energy = await _is_installed(self.hass, "octopus_energy")
                return await self.async_step_tariff_source()
            else:
                errors["base"] = "invalid_selection"

        # Discover installed integrations
        self._discovered_integrations = await _discover_installed_integrations(self.hass)

        if not self._discovered_integrations:
            return self.async_abort(reason="no_supported_integrations_found")

        # Build the input schema
        schema = vol.Schema({vol.Required("inverter_integration"): vol.In(self._discovered_integrations)})

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_tariff_source(self, user_input=None):
        """
        Step 2: Determine how import and export tariffs will be specified.
        """
        errors = {}

        if user_input is not None:
            self._tariff_source = user_input.get("tariff_source")
            if self._tariff_source == "Specify Octopus Account ID and API Key":
                return await self.async_step_octopus_account()
            elif self._tariff_source == "Specify Octopus import and export tariff codes directly":
                return await self.async_step_tariff_codes()
            elif self._tariff_source:
                return await self.async_step_system_parameters()
            else:
                errors["base"] = "invalid_selection"

        options = [
            "Get tariff codes from Octopus Energy integration",
            "Specify Octopus Account ID and API Key",
            "Specify Octopus import and export tariff codes directly",
            "Enter custom import and export tariffs via configuration.yaml",
        ]

        if not self._use_octopus_energy:
            options.remove("Get tariff codes from Octopus Energy integration")

        # Build the input schema
        schema = vol.Schema({vol.Required("tariff_source"): vol.In(options)})

        return self.async_show_form(step_id="tariff_source", data_schema=schema, errors=errors)

    async def async_step_octopus_account(self, user_input=None):
        """
        Step to enter Octopus Account ID and API Key.
        """
        errors = {}

        if user_input is not None:
            account_id = user_input.get("account_id")
            api_key = user_input.get("api_key")
            if account_id and api_key:
                return await self.async_step_system_parameters(
                    data={"tariff_source": self._tariff_source, "account_id": account_id, "api_key": api_key}
                )
            else:
                errors["base"] = "missing_credentials"

        schema = vol.Schema({vol.Required("account_id"): str, vol.Required("api_key"): str})

        return self.async_show_form(step_id="octopus_account", data_schema=schema, errors=errors)

    async def async_step_tariff_codes(self, user_input=None):
        """
        Step to enter Octopus import and export tariff codes.
        """
        errors = {}

        if user_input is not None:
            import_code = user_input.get("import_code")
            export_code = user_input.get("export_code")
            if import_code and export_code:
                return await self.async_step_system_parameters(
                    data={"tariff_source": self._tariff_source, "import_code": import_code, "export_code": export_code}
                )
            else:
                errors["base"] = "missing_codes"

        schema = vol.Schema({vol.Required("import_code"): str, vol.Required("export_code"): str})

        return self.async_show_form(step_id="tariff_codes", data_schema=schema, errors=errors)

    async def async_step_system_parameters(self, user_input=None, data=None):
        """
        Step 3: Enter system parameters.
        """
        errors = {}

        if user_input is not None:
            battery_capacity = user_input.get(CONF_BATTERY_CAPACITY)
            inverter_power = user_input.get(CONF_INVERTER_POWER)
            charger_power = user_input.get(CONF_CHARGER_POWER)
            inverter_efficiency = user_input.get(CONF_INVERTER_EFFICIENCY)
            charger_efficiency = user_input.get(CONF_CHARGER_EFFICIENCY)
            if all([battery_capacity, inverter_power, charger_power, inverter_efficiency, charger_efficiency]):
                final_data = data if data else {}
                final_data.update(
                    {
                        CONF_BATTERY_CAPACITY: battery_capacity,
                        CONF_INVERTER_POWER: inverter_power,
                        CONF_CHARGER_POWER: charger_power,
                        CONF_INVERTER_EFFICIENCY: inverter_efficiency,
                        CONF_CHARGER_EFFICIENCY: charger_efficiency,
                    }
                )
                return await self.async_step_consumption_source(final_data)
            else:
                errors["base"] = "missing_parameters"

        schema = vol.Schema(
            {
                vol.Optional(CONF_BATTERY_CAPACITY, default=DEFAULT_BATTERY_CAPACITY): int,
                vol.Optional(CONF_INVERTER_POWER, default=DEFAULT_INVERTER_POWER): int,
                vol.Optional(CONF_CHARGER_POWER, default=DEFAULT_CHARGER_POWER): int,
                vol.Optional(CONF_INVERTER_EFFICIENCY, default=DEFAULT_INVERTER_EFFICIENCY): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=100)
                ),
                vol.Optional(CONF_CHARGER_EFFICIENCY, default=DEFAULT_CHARGER_EFFICIENCY): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=100)
                ),
            }
        )

        return self.async_show_form(step_id="system_parameters", data_schema=schema, errors=errors)

    async def async_step_consumption_source(self, user_input=None):
        """
        Step 4: Confirm source of consumption data.
        """
        errors = {}

        if user_input is not None:
            consumption_source = user_input.get("consumption_source")
            if consumption_source == "Enter a daily amount":
                return await self.async_step_daily_amount()
            elif consumption_source == "Custom scaling profile via configuration.yaml":
                return self.async_create_entry(
                    title="Consumption Source", data={"consumption_source": consumption_source}
                )
            elif consumption_source == "Use historical data":
                return self.async_create_entry(
                    title="Consumption Source", data={"consumption_source": consumption_source}
                )
            else:
                errors["base"] = "invalid_selection"

        # Options for the user to select
        options = [
            "Use historical data",
            "Enter a daily amount",
            "Custom scaling profile via configuration.yaml",
        ]

        # Build schema for form
        schema = vol.Schema({vol.Required("consumption_source", default="Use historical data"): vol.In(options)})

        return self.async_show_form(step_id="consumption_source", data_schema=schema, errors=errors)

    async def async_step_daily_amount(self, user_input=None):
        """
        Step to enter daily consumption amount and scaling option.
        """
        errors = {}

        if user_input is not None:
            daily_amount = user_input.get("daily_amount")
            scaling_option = user_input.get("scaling_option")
            if daily_amount is not None and scaling_option is not None:
                return self.async_create_entry(
                    title="Daily Consumption",
                    data={"daily_amount": daily_amount, "scaling_option": scaling_option},
                )
            else:
                errors["base"] = "missing_fields"

        # Build the schema for the form
        schema = vol.Schema(
            {
                vol.Required("daily_amount", description="Enter daily amount (kWh)"): int,
                vol.Required(
                    "scaling_option",
                    description="Select scaling option",
                    default="Constant",
                ): vol.In(["Constant", "Scaled to typical usage profile"]),
            }
        )

        return self.async_show_form(step_id="daily_amount", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """
        Return the options flow handler.
        """
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """
    Handle options flow for the integration.
    """

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """
        Manage the options for the integration.
        """
        if user_input is not None:
            # Update the configuration entry with new options
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema({vol.Optional(CONF_NAME, default=self.config_entry.data.get(CONF_NAME, "")): str})

        return self.async_show_form(step_id="init", data_schema=schema)
