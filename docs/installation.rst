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

Install With HACS
-----------------

Until Miser is accepted into the default HACS store, add it as a custom
repository:

1. Open HACS in Home Assistant.
2. Go to **Integrations**.
3. Open the three-dot menu and select **Custom repositories**.
4. Add ``https://github.com/fboundy/miser``.
5. Select **Integration** as the category.
6. Click **Add**.
7. Install **Miser PV System Optimiser** from HACS.
8. Restart Home Assistant.

After the repository is accepted into the default HACS store, install it
directly from **HACS > Integrations** by searching for
**Miser PV System Optimiser**.

Manual Installation
-------------------

1. Copy ``custom_components/miser`` into your Home Assistant
   ``custom_components`` directory.
2. Restart Home Assistant.

Configure Miser
---------------

1. Go to **Settings > Devices & services > Add integration**.
2. Search for **Miser PV System Optimiser**.
3. Select the inverter brand and controller integration.
4. Select the tariff source.
5. Enter system parameters such as battery capacity, inverter power, charger
   power, and efficiencies.
6. Select the consumption source.

After setup, Miser creates configuration entities that can be adjusted from
Home Assistant without re-running the config flow.
