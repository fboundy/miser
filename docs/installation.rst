Installation
============

Prerequisites
-------------

Miser requires:

* Home Assistant with custom integrations enabled.
* The Solcast Solar integration for PV forecast data.
* One supported inverter controller integration installed and configured.
* Import tariff data from Octopus Energy, direct tariff codes, or custom tariff
  configuration.

Supported inverter controller integrations currently include:

* Solis Cloud integration (``solis``).
* SolaX Modbus integration for Solis inverters (``solax_modbus``).
* SolisConnect integration (``solisconnect``).

Install Miser
-------------

1. Copy ``custom_components/miser`` into your Home Assistant
   ``custom_components`` directory.
2. Restart Home Assistant.
3. Go to **Settings > Devices & services > Add integration**.
4. Search for **Miser PV System Optimiser**.
5. Select the inverter brand and controller integration.
6. Select the tariff source.
7. Enter system parameters such as battery capacity, inverter power, charger
   power, and efficiencies.
8. Select the consumption source.

After setup, Miser creates configuration entities that can be adjusted from
Home Assistant without re-running the config flow.
