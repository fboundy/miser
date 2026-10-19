import logging
from typing import Any, Dict, List, Union

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.loader import async_get_custom_components, async_get_integration

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_CHARGER_EFFICIENCY,
    CONF_CHARGER_POWER,
    CONF_DAILY_CONSUMPTION_KWH,
    CONF_INVERTER_EFFICIENCY,
    CONF_INVERTER_POWER,
    CONF_INVERTER_LOSS,
    DEFAULTS,
    DOMAIN,
    NAME,
    TARIFF_SOURCE_ACCOUNT,
    TARIFF_SOURCE_CODES,
    TARIFF_SOURCE_ENTITIES,
    TARIFF_SOURCE_OCTOPUS_ENERGY,
)
from .entity_tariff import PRICE_UNIT_AUTO, PRICE_UNIT_GBP, PRICE_UNIT_PENCE, EntityTariff
from .inverters import INVERTER_DEFS, get_inverter_controller_class
from .utils import get_integration_entities, redact_sensitive

_LOGGER = logging.getLogger(__name__)


def _entities_give_prices(hass: HomeAssistant, entity_ids: list[str], price_unit: str) -> bool:
    """True if the entities currently provide at least one usable price."""
    if not entity_ids:
        return False
    rates, flat = EntityTariff(hass, entity_ids, price_unit=price_unit).read_rates()
    return bool(rates) or flat is not None


def _default_price_entities(hass: HomeAssistant) -> tuple[list[str], list[str]]:
    """Suggest the Octopus Energy integration's day-rate entities, if present."""
    import_entities, export_entities = [], []
    for state in hass.states.async_all("event"):
        entity_id = state.entity_id
        if not entity_id.startswith("event.octopus_energy_electricity_"):
            continue
        if not entity_id.endswith(("_current_day_rates", "_next_day_rates")):
            continue
        (export_entities if "_export_" in entity_id else import_entities).append(entity_id)
    return sorted(import_entities), sorted(export_entities)


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
        # Don't let a failure to check masquerade as "not installed" silently:
        # a lost import once made this abort every setup with that message.
        _LOGGER.exception("Unable to check whether %s is installed", integration)
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
                    _LOGGER.debug(
                        f"Config entry for integration '{integration}': {redact_sensitive(entry.as_dict())}"
                    )

            else:
                _LOGGER.debug(f"No config entries found for integration '{integration}'")

        return installed_integrations
    except Exception as e:
        # Log any errors during the discovery process
        _LOGGER.error(f"Error discovering integrations for brand '{inverter_brand}': {e}")
        return []


def _entity_template_candidates(templates: str | list[str], device_name: str) -> list[str]:
    if isinstance(templates, str):
        templates = [templates]
    return [template.replace("{device_name}", device_name) for template in templates]


def _infer_integration_device_name(entity_ids: list[str], entity_defs: dict) -> str | None:
    candidates: dict[str, int] = {}

    for entity_templates in entity_defs.get("model_entities", {}).values():
        templates = [entity_templates] if isinstance(entity_templates, str) else entity_templates
        for template in templates:
            if "{device_name}" not in template:
                continue
            domain, object_template = template.split(".", 1)
            suffix = object_template.split("{device_name}", 1)[1]
            for entity_id in entity_ids:
                entity_domain, entity_object_id = entity_id.split(".", 1)
                if entity_domain == domain and entity_object_id.endswith(suffix):
                    candidate = entity_object_id[: -len(suffix)]
                    candidates[candidate] = candidates.get(candidate, 0) + 1

    if not candidates:
        return None

    return max(candidates, key=candidates.get)


def _has_required_model_entities(brand: str, integration: str, associated_entities: list) -> bool:
    entity_ids = [entity.entity_id for entity in associated_entities]
    controller = get_inverter_controller_class(brand, integration)
    entity_defs = controller.entity_defs
    device_name = _infer_integration_device_name(entity_ids, entity_defs)
    if device_name is None:
        return False

    for templates in entity_defs.get("model_entities", {}).values():
        expected_entity_ids = _entity_template_candidates(templates, device_name)
        if not any(entity_id in entity_ids for entity_id in expected_entity_ids):
            return False

    return True


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
                await self.async_set_unique_id(f"{DOMAIN}_{self._data['inverter_brand']}_{self._options['integration']}")
                self._abort_if_unique_id_configured()
                user_input["controller_config_entry"], associated_entities = await get_integration_entities(
                    hass=self.hass, integration=self._options["integration"]
                )
                if not associated_entities or not _has_required_model_entities(
                    self._data["inverter_brand"],
                    self._options["integration"],
                    associated_entities,
                ):
                    errors["base"] = "missing_controller_entities"
                    return self.async_show_form(
                        step_id="select_controller",
                        data_schema=vol.Schema({vol.Required("inverter_integration"): vol.In(self._names.values())}),
                        errors=errors,
                    )

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
            if self._options["tariff_source"] == TARIFF_SOURCE_ACCOUNT:
                return await self.async_step_octopus_account()
            elif self._options["tariff_source"] == TARIFF_SOURCE_CODES:
                return await self.async_step_tariff_codes()
            elif self._options["tariff_source"] == TARIFF_SOURCE_ENTITIES:
                return await self.async_step_tariff_entities()
            elif self._options["tariff_source"]:
                return await self.async_step_system_parameters()
            else:
                errors["base"] = "invalid_selection"

        # Options for selecting the tariff source
        options = [
            TARIFF_SOURCE_OCTOPUS_ENERGY,
            TARIFF_SOURCE_ACCOUNT,
            TARIFF_SOURCE_CODES,
            TARIFF_SOURCE_ENTITIES,
        ]

        if not self._use_octopus_energy:
            # Remove the option if Octopus Energy is not installed
            options.remove(TARIFF_SOURCE_OCTOPUS_ENERGY)

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

    async def async_step_tariff_entities(self, user_input=None):
        """
        Step 4c: Choose Home Assistant entities that provide import and export prices.
        """
        errors = {}

        if user_input is not None:
            import_entities = list(user_input.get("import_entities") or [])
            export_entities = list(user_input.get("export_entities") or [])
            price_unit = user_input.get("price_unit", PRICE_UNIT_AUTO)

            if not _entities_give_prices(self.hass, import_entities, price_unit):
                errors["import_entities"] = "no_prices"
            if export_entities and not _entities_give_prices(self.hass, export_entities, price_unit):
                errors["export_entities"] = "no_prices"

            if not errors:
                self._options["import_entities"] = import_entities
                self._options["export_entities"] = export_entities
                self._options["price_unit"] = price_unit
                return await self.async_step_system_parameters()

        default_import, default_export = _default_price_entities(self.hass)
        price_entity = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "event", "input_number"], multiple=True)
        )
        schema = vol.Schema(
            {
                vol.Required("import_entities", default=default_import): price_entity,
                vol.Optional("export_entities", default=default_export): price_entity,
                vol.Required("price_unit", default=PRICE_UNIT_AUTO): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=PRICE_UNIT_AUTO, label="Detect automatically"),
                            selector.SelectOptionDict(value=PRICE_UNIT_GBP, label="Pounds per kWh (e.g. 0.2667)"),
                            selector.SelectOptionDict(value=PRICE_UNIT_PENCE, label="Pence per kWh (e.g. 26.67)"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="tariff_entities", data_schema=schema, errors=errors)

    async def async_step_system_parameters(self, user_input=None):
        """
        Step 5: Enter system parameters such as battery capacity, power ratings, and efficiencies.
        """
        errors = {}

        if user_input is not None:
            # Collect and validate system parameters
            self._options["battery_capacity"] = user_input.get(CONF_BATTERY_CAPACITY)
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
                vol.Optional(CONF_BATTERY_CAPACITY, default=DEFAULTS[CONF_BATTERY_CAPACITY]): int,
                vol.Optional(CONF_INVERTER_POWER, default=DEFAULTS[CONF_INVERTER_POWER]): int,
                vol.Optional(CONF_CHARGER_POWER, default=DEFAULTS[CONF_CHARGER_POWER]): int,
                vol.Optional(CONF_INVERTER_EFFICIENCY, default=DEFAULTS[CONF_INVERTER_EFFICIENCY]): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=100)
                ),
                vol.Optional(CONF_CHARGER_EFFICIENCY, default=DEFAULTS[CONF_CHARGER_EFFICIENCY]): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=100)
                ),
                vol.Optional(CONF_INVERTER_LOSS, default=DEFAULTS[CONF_INVERTER_LOSS]): int,
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
            self._options[CONF_DAILY_CONSUMPTION_KWH] = user_input.get("daily_amount")
            self._options["scaling_option"] = user_input.get("scaling_option")
            if self._options[CONF_DAILY_CONSUMPTION_KWH] is not None and self._options["scaling_option"] is not None:
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
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """
    Handle options flow for the integration.
    """

    # No __init__: OptionsFlow provides config_entry as a read-only property.

    async def async_step_init(self, user_input=None):
        """
        Manage the options for the integration.
        """
        if user_input is not None:
            # Merge rather than replace: setup reads integration, tariff_source
            # and the system parameters from options.
            return self.async_create_entry(title="", data={**self.config_entry.options, **user_input})

        current_name = self.config_entry.options.get(CONF_NAME, self.config_entry.data.get(CONF_NAME, ""))
        schema = vol.Schema({vol.Optional(CONF_NAME, default=current_name): str})
        return self.async_show_form(step_id="init", data_schema=schema)
