# Miser PV System Optimiser

Miser is a Home Assistant custom integration that models a PV, battery, inverter, tariff, and load forecast, then writes scheduled charge or discharge controls to a supported inverter integration.

The integration is currently focused on Solis inverter systems and Octopus-style import/export tariffs.

## Prerequisites

- Home Assistant with custom integrations enabled.
- Solcast Solar integration, used for PV forecast data.
- One supported inverter controller integration installed and configured.
- Import tariff data, either from the Octopus Energy integration, Octopus account details, direct tariff codes, or custom tariff configuration.

Supported inverter controller integrations currently include:

- Solis Cloud integration (`solis`)
- SolaX Modbus integration for Solis inverters (`solax_modbus`)
- SolisConnect integration (`solisconnect`) for model data only

## Installation

1. Copy `custom_components/miser` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Go to **Settings > Devices & services > Add integration**.
4. Search for **Miser PV System Optimiser**.
5. Select the inverter brand and controller integration.
6. Select the tariff source.
7. Enter system parameters such as battery capacity, inverter power, charger power, and efficiencies.
8. Select the consumption source.

After setup, Miser creates configuration entities that can be adjusted from Home Assistant without re-running the config flow.

## Entities

Miser creates sensors, numbers, and switches for optimiser configuration, costs, current control state, and upcoming control slots.

Important sensors include:

- `Miser State`
- `Miser Cost - Base`
- `Miser Cost - Low Cost Charging`
- `Miser Cost - Discharge`
- `Miser Cost - Fill First`
- `Miser Cost - Whole Horizon`
- `Miser Cost - Optimised`
- `Miser Next Slot Start`
- `Miser Next Slot End`
- `Miser Next Slot Power`
- `Miser Next Slot Target SOC`

Cost sensors include `slots` and `flows` attributes for dashboards and analysis.

Important switches include:

- `Miser Optimise Discharging`
- `Miser Whole Horizon Beta`
- `Miser Include Export`
- `Miser Use Solar`
- `Miser Use Consumption History`

Important numbers include:

- `Miser Battery Capacity`
- `Miser Inverter Power`
- `Miser Charger Power`
- `Miser Optimiser Frequency`
- `Miser Daily Consumption kWh`

## Service Actions, Triggers, And Conditions

Miser does not currently register custom Home Assistant service actions.

Miser does not currently provide custom device triggers or custom conditions. Use the generated sensors, switches, and number entities in standard Home Assistant automations.

## Operation

The optimiser runs on the configured interval. It waits for required inverter and forecast entities before the first run.

Status values include:

- `Awaiting Sensors`
- `Optimising`
- `Idle`
- `Charging`
- `Discharging`

Inverter writes are delayed until a control slot is imminent, based on the optimiser frequency, to reduce unnecessary writes to the inverter.

## Removal

1. Delete the Miser integration entry from **Settings > Devices & services**.
2. Restart Home Assistant if any entities remain unavailable or stale.
3. Remove `custom_components/miser` if you no longer want the custom integration installed.

## Known Limitations

- The integration is experimental and is not currently rated on the Home Assistant Integration Quality Scale.
- Only one Miser config entry is expected.
- Whole Horizon optimisation is beta and must be enabled explicitly.
- Solis Cloud control reliability depends on the upstream Solis integration and cloud API behaviour.
- SolaX Modbus availability depends on the inverter being reachable locally.
- BMS or inverter current limits exposed by upstream integrations are not yet consumed automatically.

## Troubleshooting

If Miser remains in `Awaiting Sensors`, check that the selected inverter integration has created all required model entities and that those entities are not `unknown` or `unavailable`.

If the optimiser cost sensors do not update, check the Home Assistant logs for missing Solcast, tariff, inverter, or consumption data.

If the inverter does not follow a scheduled control slot, check the Miser logs for the timed charge/discharge values written to the inverter and the read-back values reported by the upstream inverter integration.
