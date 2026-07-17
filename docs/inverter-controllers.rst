Adding Inverter Controllers
===========================

Miser separates optimisation from inverter-specific control. The optimiser
works with the abstract ``InverterController`` interface in
``custom_components/miser/inverters/controller.py``. A concrete controller
adapts Home Assistant entities from an upstream inverter integration into that
interface.

Use this approach when adding support for a new inverter brand or a new Home
Assistant inverter integration.

Controller Responsibilities
---------------------------

A controller must:

* Report whether the inverter is online and all required model entities are
  available.
* Read and optionally set the inverter clock.
* Configure a timed charge window.
* Configure a timed discharge window.
* Return current inverter status for diagnostics.
* Report whether the inverter already matches the requested Miser state.
* Read and set the inverter operating mode where the upstream integration
  exposes one.

The abstract methods are:

.. code-block:: python

   async def is_online(self) -> bool: ...
   async def get_time(self) -> datetime: ...
   async def set_time(self, time: datetime) -> None: ...
   async def control_charge(
       self, start: datetime, end: datetime, target_soc: float, power: float
   ) -> None: ...
   async def control_discharge(
       self, start: datetime, end: datetime, target_soc: float, power: float
   ) -> None: ...
   async def get_status(self) -> dict[str, Any]: ...
   async def control_matches(
       self, state: str, target_soc: float | None, power: float
   ) -> bool: ...
   async def set_mode(self, mode: str) -> None: ...
   async def get_mode(self) -> str: ...

Entity Definitions
------------------

Concrete controllers declare the upstream Home Assistant entities they need in
an ``entity_defs`` class attribute. Miser uses these definitions during config
flow validation and stores the resolved entity IDs in ``hass.data[DOMAIN]``.

The definitions are grouped by purpose:

``model_entities``
   Read-only or configuration entities used by the PV and battery model. These
   include battery SOC, daily import/export, daily consumption, and optional
   battery minimum SOC.

``control_entities``
   Entities used to write charge/discharge windows, current limits, target SOC,
   inverter mode, clock values, and update buttons.

``config_entities``
   Optional extra entities that are relevant to controller setup but not part
   of the model or control loop.

Entity templates may be a string or a list of strings. Use ``{device_name}`` as
the placeholder for the upstream integration's entity prefix:

.. code-block:: python

   entity_defs = {
       "model_entities": {
           MODEL_BATTERY_SOC: "sensor.{device_name}_battery_soc",
           MODEL_GRID_IMPORT_TODAY: "sensor.{device_name}_grid_import_today",
           MODEL_GRID_EXPORT_TODAY: "sensor.{device_name}_grid_export_today",
           MODEL_CONSUMPTION_TODAY: [
               "sensor.{device_name}_house_load_today",
               "sensor.{device_name}_today_energy_consumption",
           ],
       },
       "control_entities": {
           CONTROL_BATTERY_VOLTAGE: "sensor.{device_name}_battery_voltage",
           CONTROL_TIMED_CHARGE_CURRENT: "number.{device_name}_charge_current",
           CONTROL_TIMED_DISCHARGE_CURRENT: "number.{device_name}_discharge_current",
       },
   }

When multiple templates are supplied for a key, Miser accepts the first one
that exists for the selected upstream device. This is useful when upstream
integrations rename entities across versions.

Adding A New Integration For An Existing Brand
----------------------------------------------

For another Solis integration, add a class in
``custom_components/miser/inverters/solis.py``:

.. code-block:: python

   class SolisExampleInverter(SolisInverter):
       integration: ClassVar[str] = "example_solis"
       entity_defs: ClassVar[dict[str, dict[str, str]]] = {
           "model_entities": {...},
           "control_entities": {...},
       }

       async def control_charge(self, start, end, target_soc, power):
           ...

       async def control_discharge(self, start, end, target_soc, power):
           ...

Then add the class to ``SOLIS_INVERTER_CLASSES``. The helper
``get_solis_inverter_defs()`` will expose the entity definitions to the config
flow automatically.

Adding A New Brand
------------------

For a new inverter brand:

1. Create a new module under ``custom_components/miser/inverters``.
2. Add a brand-level abstract base class if shared helpers are useful.
3. Add one concrete class per supported upstream Home Assistant integration.
4. Register the new classes in ``custom_components/miser/inverters/__init__.py``
   by updating ``INVERTER_CONTROLLER_CLASSES`` and ``INVERTER_DEFS``.
5. Ensure the brand appears in the config flow by using the same brand key in
   those dictionaries.

Prefer small concrete classes first. Move behaviour into a brand base class
only once at least two controllers need the same helper.

Implementation Notes
--------------------

Use Home Assistant services to write controls:

* ``number.set_value`` for current, SOC, hour, minute, and offset entities.
* ``time.set_value`` for time entities.
* ``switch.turn_on`` and ``switch.turn_off`` for enable flags.
* ``select.select_option`` for inverter operating modes.
* ``button.press`` for integrations that require an explicit write/apply step.

Convert requested power in watts into battery current only where the upstream
integration expects current. Existing Solis controllers use battery voltage:

.. code-block:: python

   current = abs(power) / battery_voltage

If the upstream integration accepts power directly, keep the controller method
local to that integration and write watts to the appropriate entity.

``control_matches`` should be conservative. Return ``True`` only when the
inverter state, enable flags, target SOC, and current or power are known to
match the requested Miser state. If read-back values are missing or unreliable,
return ``False`` so Miser refreshes the control write when the slot is
imminent.

Testing Checklist
-----------------

Before enabling a new controller in a live install:

* Confirm config flow discovers the upstream integration.
* Confirm every required ``model_entities`` key resolves to an available entity.
* Confirm ``is_online()`` becomes true only when model data is usable.
* Test a one-minute charge slot at low power.
* Test a one-minute discharge slot at low power.
* Confirm idle control disables or clears the scheduled controls.
* Check Home Assistant logs for entity IDs, written values, and read-back
  status.
