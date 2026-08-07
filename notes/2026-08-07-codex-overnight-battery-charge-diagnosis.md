# Overnight battery charging diagnosis

- Timestamp: 2026-08-07 15:17:52 BST
- Author: Codex
- Incident window: Night of 2026-08-06 to 2026-08-07
- Scope: Read-only diagnosis; no Home Assistant or integration settings were changed.

## Finding

Miser planned and reported an overnight charge, but the available evidence indicates that the Solis inverter did not execute it. The most likely cause is that Miser could not resolve a SolisConnect entity capable of enabling timed charge slot 1.

The controller deliberately treats the timed-charge enable entity as optional. If it is absent, it still writes the slot times, current, and target SOC, but silently skips the enable operation. Miser can consequently report `Charging` even though the inverter has not enabled the charging period.

## Evidence

- Home Assistant's retained time-series history showed `sensor.miser_state` as `Charging` from approximately 00:35 to 05:20 BST.
- Before the window, Miser advertised a next slot of approximately 00:30 to 05:30 BST.
- The entity registry contained the SolisConnect slot 1 start, end, current, and target-SOC controls.
- Neither expected enable candidate was present in the registry:
  - `switch.solis_s5_eh1p_timed_charge_enable_1`
  - `switch.solis_s5_eh1p_grid_time_of_use_charging_period_1`
- The current controller only calls `turn_on` when an enable entity resolves; it does not warn or fail if neither candidate exists.
- `ha core logs` contained no retained Miser exception identifying a failed charge command. This is consistent with the missing-enable path being silent.

## Other relevant findings

- The active Miser configuration uses the `solisconnect` integration and a configured charger power of 3000 W.
- The live `miser.log` files had rotated several times during 7 August and no longer retained the overnight entries.
- Home Assistant Recorder uses MariaDB with a ten-day retention period.
- Sensor history is also written to InfluxDB, which preserved Miser's overnight state and planned-slot timeline.
- The entity registry contains duplicate/suffixed Solis entities from current and previous inverter integrations. Entity resolution should continue to prefer an available entity belonging to the selected integration/device.
- Miser's state currently represents the requested optimiser action, not verified physical battery power or SOC movement. This can make a failed inverter command look like a successful charge.
- `ha core logs` was heavily populated by SolisConnect debug output; the dedicated Miser log is more useful for optimiser detail but its rotation capacity is too small for next-day incident analysis.

## Recommended follow-up

1. Make the timed-charge enable capability required for SolisConnect, or support the actual enable mechanism exposed by the installed SolisConnect version.
2. Emit a clear warning/error when no timed-charge enable entity can be resolved.
3. Verify a charge command using inverter state, battery power, or SOC movement; expose a distinct requested-versus-confirmed control state.
4. Add the resolved inverter control entity IDs and command results to diagnostic attributes/logging.
5. Increase Miser log retention if overnight decisions need to remain locally inspectable the following day.
6. Investigate the existing repository TODO: prefer reliable Solis BMS/inverter charge and discharge current limits over the configured Miser current limit when available.

## Confidence and limitation

Confidence is high that Miser requested charging and the physical inverter did not follow that request. The absent timed-charge enable entity is the strongest code-and-configuration explanation, but the exact inverter register state during the incident was not retained in the available Core or Miser text logs.
