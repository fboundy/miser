import logging
from typing import Any, Dict, List, Union

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_custom_components, async_get_integration

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CURRENT_LIMIT,
    CONF_CHARGER_EFFICIENCY,
    CONF_CHARGER_POWER,
    CONF_INVERTER_EFFICIENCY,
    CONF_INVERTER_POWER,
    CONF_INVERTER_LOSS,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_BATTERY_CURRENT_LIMIT,
    DEFAULT_CHARGER_EFFICIENCY,
    DEFAULT_CHARGER_POWER,
    DEFAULT_INVERTER_EFFICIENCY,
    DEFAULT_INVERTER_POWER,
    DEFAULT_INVERTER_LOSS,
    DOMAIN,
    NAME,
    INVERTER_DEFS,
)
from .utils import get_integration_entities

_LOGGER = logging.getLogger(__name__)


async def _get_integration_name(hass: HomeAssistant, domain: str) -> str:
    """
    Retrieve the name of an integration from its manifest.json file.
    """
    try:
        integration = await async_get_integration(hass, domain)
        return integration.name  # This is the 'name' field in manifest.json
    except Exception as e:
        _LOGGER.error(f"Failed to get name for integration '{domain}': {e}")
        return "Unknown Integration"


async def _is_installed(hass: HomeAssistant, integration: str) -> bool:
    """
    Helper function to check if a given integration is installed as a custom component.
    """
    try:
        integrations = await async_get_custom_components(hass)
        result = integration in integrations
        _LOGGER.debug(f"{integration}: {result}")
        return result
    except Exception:
        # Return False if there's any issue checking integrations
        return False


async def _discover_installed_integrations(hass: HomeAssistant, inverter_brand: str) -> List[str]:
    """
    Discover which of the known inverter integrations for a given brand are installed.
    Log the config_entry and associated entities for each discovered integration to the debug logger.
    """
    try:
        # Get all custom components installed in Home Assistant
        custom_components = await async_get_custom_components(hass)

        # Find installed integrations for the specified inverter brand
        installed_integrations = [
            integration for integration in INVERTER_DEFS.get(inverter_brand, []) if integration in custom_components
        ]

        # Log configuration entries and associated entities for each installed integration
        for integration in installed_integrations:
            config_entries = hass.config_entries.async_entries(integration)
            if config_entries:
                for entry in config_entries:
                    _LOGGER.debug(f"Config entry for integration '{integration}': {entry.as_dict()}")

            else:
                _LOGGER.debug(f"No config entries found for integration '{integration}'")

        return installed_integrations
    except Exception as e:
        # Log any errors during the discovery process
        _LOGGER.error(f"Error discovering integrations for brand '{inverter_brand}': {e}")
        return []


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Handle the configuration flow for the integration.
    """

    VERSION = 1

    def __init__(self):
        # Initialize instance variables to track user input across steps
        self._discovered_integrations = []  # List of discovered integrations
        self._use_octopus_energy = False  # Whether the Octopus Energy integration is used
        self._data = {}
        self._options = {}

    async def async_step_user(self, user_input=None):
        """
        Step 1: Select inverter brand.
        """
        errors = {}

        # Check if the required Solcast Solar integration is installed
        if not await _is_installed(self.hass, "solcast_solar"):
            return self.async_abort(reason="solcast_solar_not_installed")

        if user_input is not None:
            # Save the selected inverter brand and proceed to the next step
            self._data["inverter_brand"] = user_input.get("inverter_brand").lower()
            return await self.async_step_select_controller()

        # Display a form for selecting the inverter brand
        schema = vol.Schema(
            {vol.Required("inverter_brand"): vol.In([brand.title() for brand in INVERTER_DEFS.keys()])}
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_select_controller(self, user_input=None):
        """
        Step 2: Select an inverter controller integration.
        """
        errors = {}
        names = {}
        if user_input is not None:
            self._options["integration_name"] = user_input.get("inverter_integration")
            lookup = {self._names[i]: i for i in self._names}
            self._options["integration"] = lookup.get(self._options["integration_name"], "")

            if self._options["integration"] in self._discovered_integrations:
                user_input["controller_config_entry"], associated_entities = await get_integration_entities(
                    hass=self.hass, integration=self._options["integration"]
                )
                if associated_entities:
                    _LOGGER.debug(
                        f"First entity for integration '{self._options['integration']}': {associated_entities[0]}"
                    )

                self._use_octopus_energy = await _is_installed(self.hass, "octopus_energy")
                return await self.async_step_tariff_source()
            else:
                errors["base"] = "invalid_selection"

        self._discovered_integrations = await _discover_installed_integrations(
            self.hass, inverter_brand=self._data["inverter_brand"]
        )

        if not self._discovered_integrations:
            return self.async_abort(reason="no_supported_integrations_found")
        else:
            for integration in self._discovered_integrations:
                name = await _get_integration_name(self.hass, integration)
                names[integration] = name
            self._names = names

        schema = vol.Schema({vol.Required("inverter_integration"): vol.In(names.values())})
        return self.async_show_form(step_id="select_controller", data_schema=schema, errors=errors)

    async def async_step_tariff_source(self, user_input=None):
        """
        Step 3: Determine how import and export tariffs will be specified.
        """
        errors = {}

        if user_input is not None:
            self._options["tariff_source"] = user_input.get("tariff_source")
            if self._options["tariff_source"] == "Specify Octopus Account ID and API Key":
                return await self.async_step_octopus_account()
            elif self._options["tariff_source"] == "Specify Octopus import and export tariff codes directly":
                return await self.async_step_tariff_codes()
            elif self._options["tariff_source"]:
                return await self.async_step_system_parameters()
            else:
                errors["base"] = "invalid_selection"

        # Options for selecting the tariff source
        options = [
            "Get tariff codes from Octopus Energy integration",
            "Specify Octopus Account ID and API Key",
            "Specify Octopus import and export tariff codes directly",
            "Enter custom import and export tariffs via configuration.yaml",
        ]

        if not self._use_octopus_energy:
            # Remove the option if Octopus Energy is not installed
            options.remove("Get tariff codes from Octopus Energy integration")

        # Display a form for selecting the tariff source
        schema = vol.Schema({vol.Required("tariff_source"): vol.In(options)})
        return self.async_show_form(step_id="tariff_source", data_schema=schema, errors=errors)

    async def async_step_octopus_account(self, user_input=None):
        """
        Step 4a: Enter Octopus Account ID and API Key.
        """
        errors = {}

        if user_input is not None:
            # Collect account credentials
            self._data["account_id"] = user_input.get("account_id")
            self._data["api_key"] = user_input.get("api_key")
            if self._data["account_id"] and self._data["api_key"]:
                return await self.async_step_system_parameters()
            else:
                errors["base"] = "missing_credentials"

        # Display a form for entering Octopus account credentials
        schema = vol.Schema({vol.Required("account_id"): str, vol.Required("api_key"): str})
        return self.async_show_form(step_id="octopus_account", data_schema=schema, errors=errors)

    async def async_step_tariff_codes(self, user_input=None):
        """
        Step 4b: Enter tariff codes directly.
        """
        errors = {}

        if user_input is not None:
            # Collect import and export tariff codes
            self._options["import_code"] = user_input.get("import_code")
            self._options["export_code"] = user_input.get("export_code")
            if self._options["import_code"]:
                return await self.async_step_system_parameters()
            else:
                errors["base"] = "missing_codes"

        # Display a form for entering tariff codes
        schema = vol.Schema({vol.Required("import_code"): str, vol.Optional("export_code"): str})
        return self.async_show_form(step_id="tariff_codes", data_schema=schema, errors=errors)

    async def async_step_system_parameters(self, user_input=None):
        """
        Step 5: Enter system parameters such as battery capacity, power ratings, and efficiencies.
        """
        errors = {}

        if user_input is not None:
            # Collect and validate system parameters
            self._options["battery_capacity"] = user_input.get(CONF_BATTERY_CAPACITY)
            self._options["battery_current_limit"] = user_input.get(CONF_BATTERY_CURRENT_LIMIT)
            self._options["inverter_power"] = user_input.get(CONF_INVERTER_POWER)
            self._options["charger_power"] = user_input.get(CONF_CHARGER_POWER)
            self._options["inverter_efficiency"] = user_input.get(CONF_INVERTER_EFFICIENCY)
            self._options["charger_efficiency"] = user_input.get(CONF_CHARGER_EFFICIENCY)
            self._options["inverter_loss"] = user_input.get(CONF_INVERTER_LOSS)
            if all(
                [
                    self._options[x]
                    for x in [
                        "battery_capacity",
                        "battery_current_limit",
                        "inverter_power",
                        "charger_power",
                        "inverter_efficiency",
                        "charger_efficiency",
                        "inverter_loss",
                    ]
                ]
            ):
                return await self.async_step_consumption_source()
            else:
                errors["base"] = "missing_parameters"

        # Display a form for entering system parameters
        schema = vol.Schema(
            {
                vol.Optional(CONF_BATTERY_CAPACITY, default=DEFAULT_BATTERY_CAPACITY): int,
                vol.Optional(CONF_BATTERY_CURRENT_LIMIT, default=DEFAULT_BATTERY_CURRENT_LIMIT): int,
                vol.Optional(CONF_INVERTER_POWER, default=DEFAULT_INVERTER_POWER): int,
                vol.Optional(CONF_CHARGER_POWER, default=DEFAULT_CHARGER_POWER): int,
                vol.Optional(CONF_INVERTER_EFFICIENCY, default=DEFAULT_INVERTER_EFFICIENCY): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=100)
                ),
                vol.Optional(CONF_CHARGER_EFFICIENCY, default=DEFAULT_CHARGER_EFFICIENCY): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=100)
                ),
                vol.Optional(CONF_INVERTER_LOSS, default=DEFAULT_INVERTER_LOSS): int,
            }
        )
        return self.async_show_form(step_id="system_parameters", data_schema=schema, errors=errors)

    async def async_step_consumption_source(self, user_input=None):
        """
        Step 6: Confirm the source of consumption data.
        """
        errors = {}

        if user_input is not None:
            # Validate consumption source
            self._options["consumption_source"] = user_input.get("consumption_source")
            if self._options["consumption_source"] == "Enter a daily amount":
                return await self.async_step_daily_amount()
            elif self._options["consumption_source"] in [
                "Custom scaling profile via configuration.yaml",
                "Use historical data",
            ]:
                return self.async_create_entry(title=NAME, data=self._data, options=self._options)
            else:
                errors["base"] = "invalid_selection"

        # Provide consumption source options
        options = [
            "Use historical data",
            "Enter a daily amount",
            "Custom scaling profile via configuration.yaml",
        ]

        schema = vol.Schema({vol.Required("consumption_source", default="Use historical data"): vol.In(options)})
        return self.async_show_form(step_id="consumption_source", data_schema=schema, errors=errors)

    async def async_step_daily_amount(self, user_input=None):
        """
        Step 7: Enter a daily consumption amount and scaling option.
        """
        errors = {}

        if user_input is not None:
            self._options["daily_consumption"] = user_input.get("daily_consumption")
            self._options["scaling_option"] = user_input.get("scaling_option")
            if self._options["daily_consumption"] is not None and self._options["scaling_option"] is not None:
                return self.async_create_entry(
                    title=NAME,
                    data=self._data,
                    options=self._options,
                )
            else:
                errors["base"] = "missing_fields"

        # Display a form for entering daily consumption data
        schema = vol.Schema(
            {
                vol.Required("daily_amount"): int,
                vol.Required("scaling_option", default="Constant"): vol.In(
                    ["Constant", "Scaled to typical usage profile"]
                ),
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
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema({vol.Optional(CONF_NAME, default=self.config_entry.data.get(CONF_NAME, "")): str})
        return self.async_show_form(step_id="init", data_schema=schema)
