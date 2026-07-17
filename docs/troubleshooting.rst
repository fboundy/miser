Troubleshooting
===============

If Miser remains in ``Awaiting Sensors``, check that the selected inverter
integration has created all required model entities and that those entities are
not ``unknown`` or ``unavailable``.

If the optimiser cost sensors do not update, check the Home Assistant logs for
missing Solcast, tariff, inverter, or consumption data.

If the inverter does not follow a scheduled control slot, check the Miser logs
for the timed charge/discharge values written to the inverter and the read-back
values reported by the upstream inverter integration.

Known Limitations
-----------------

* The integration is experimental and is not currently rated on the Home
  Assistant Integration Quality Scale.
* Only one Miser config entry is expected.
* Whole Horizon optimisation is beta and must be enabled explicitly.
* Solis Cloud control reliability depends on the upstream Solis integration and
  cloud API behaviour.
* SolaX Modbus availability depends on the inverter being reachable locally.
* BMS or inverter current limits exposed by upstream integrations are not yet
  consumed automatically.
