Operation
=========

The optimiser runs on the configured interval. It waits for required inverter
and forecast entities before the first run.

Status values include:

* ``Awaiting Sensors``
* ``Optimising``
* ``Idle``
* ``Charging``
* ``Discharging``

Inverter writes are delayed until a control slot is imminent, based on the
optimiser frequency. This reduces unnecessary writes to the inverter.

Entities
--------

Miser creates sensors, numbers, and switches for optimiser configuration,
costs, current control state, and upcoming control slots.

Important sensors include:

* ``Miser State``
* ``Miser Cost - Base``
* ``Miser Cost - Low Cost Charging``
* ``Miser Cost - Discharge``
* ``Miser Cost - Fill First``
* ``Miser Cost - Whole Horizon``
* ``Miser Cost - Optimised``
* ``Miser Next Slot Start``
* ``Miser Next Slot End``
* ``Miser Next Slot Power``
* ``Miser Next Slot Target SOC``

Cost sensors include ``slots`` and ``flows`` attributes for dashboards and
analysis.

Important switches include:

* ``Miser Optimise Discharging``
* ``Miser Whole Horizon Beta``
* ``Miser Include Export``
* ``Miser Use Solar``
* ``Miser Use Consumption History``

Important numbers include:

* ``Miser Battery Capacity``
* ``Miser Inverter Power``
* ``Miser Charger Power``
* ``Miser Optimiser Frequency``
* ``Miser Daily Consumption kWh``

Service Actions, Triggers, And Conditions
-----------------------------------------

Miser does not currently register custom Home Assistant service actions.

Miser does not currently provide custom device triggers or custom conditions.
Use the generated sensors, switches, and number entities in standard Home
Assistant automations.
