ApexCharts Dashboards
=====================

Miser cost sensors expose optimiser output as Home Assistant attributes that
can be plotted with the `ApexCharts Card
<https://github.com/RomRider/apexcharts-card>`_.

The most useful sensor for dashboarding is ``Miser Cost - Optimised``. Its
state is the optimised cost in pence, and its attributes include:

* ``flows``: one entry per model period, including ``start``, ``grid``,
  ``battery``, ``load``, ``soc``, and ``soc_end``.
* ``slots``: the forced inverter control slots selected by Miser, including
  ``start``, ``end``, and ``power`` for the optimised output.

The example below mirrors the Miser dashboard pattern used at
``/dashboard-home/miser``. It plots actual battery SOC, predicted SOC from the
optimised model, and forced charge/discharge power on a second axis. It also
shows the optimised cost in the card header.

Replace ``sensor.solis_inverter_battery_soc`` with the battery SOC sensor used
by your inverter integration. If Home Assistant generated different entity
IDs, use the actual entity IDs from **Developer Tools > States**.

.. code-block:: yaml

   type: custom:apexcharts-card
   header:
     show: true
     title: Battery SOC - Predicted vs Actual
     show_states: true
     colorize_states: true
   graph_span: 60h
   span:
     start: hour
     offset: "-18h"
   now:
     show: true
   yaxis:
     - id: soc
       min: 0
       max: 100
       decimals: 0
       apex_config:
         tickAmount: 5
         title:
           text: SOC - %
     - id: power
       opposite: true
       min: -5000
       max: 5000
       decimals: 0
       apex_config:
         title:
           text: Forced battery power - W
   apex_config:
     tooltip:
       x:
         format: ddd dd MMM HH:mm
   series:
     - entity: sensor.solis_inverter_battery_soc
       name: Actual SOC
       yaxis_id: soc
       color: "#FB8C00"
       stroke_width: 2
       unit: "%"
       extend_to: now

     # Header-only series: shows the optimiser cost sensor state in pence.
     - entity: sensor.miser_optimised_cost
       name: Optimised Cost
       color: "#FDD835"
       unit: p
       show:
         in_chart: false
         in_header: true

     # Chart-only series: plots predicted SOC from the optimiser flow attribute.
     - entity: sensor.miser_optimised_cost
       name: Predicted SOC
       yaxis_id: soc
       color: "#42A5F5"
       stroke_width: 2
       type: line
       unit: "%"
       show:
         in_header: false
       data_generator: |-
         return (entity.attributes.flows || []).map(f => [
           new Date(f.start).getTime(),
           Number(f.soc_end)
         ]);

     # Chart-only series: plots forced battery power from the optimiser slots.
     # Positive power is forced charging. Negative power is forced discharging.
     - entity: sensor.miser_optimised_cost
       name: Forced Battery Power
       yaxis_id: power
       color: "#1E88E5"
       type: line
       stroke_width: 2
       unit: W
       show:
         in_header: false
       data_generator: |-
         return (entity.attributes.slots || []).flatMap(s => {
           const start = new Date(s.start).getTime();
           const end = s.end
             ? new Date(s.end).getTime()
             : start + 30 * 60 * 1000;
           const power = Number(s.power || 0);
           return [
             [start, 0],
             [start, power],
             [end, power],
             [end, 0],
           ];
         });

Comparing Optimiser Models
--------------------------

To compare predicted SOC for other optimiser models, duplicate the predicted
SOC series and change the entity. Common cost sensor entity IDs are:

* ``sensor.miser_base_cost``
* ``sensor.miser_lcc_cost``
* ``sensor.miser_discharge_cost``
* ``sensor.miser_fill_first_cost``
* ``sensor.miser_whole_horizon_cost``
* ``sensor.miser_optimised_cost``

For example:

.. code-block:: yaml

   - entity: sensor.miser_fill_first_cost
     name: Fill First SOC
     yaxis_id: soc
     color: "#EF5350"
     stroke_width: 2
     type: line
     unit: "%"
     show:
       in_header: false
     data_generator: |-
       return (entity.attributes.flows || []).map(f => [
         new Date(f.start).getTime(),
         Number(f.soc_end)
       ]);

If the card header shows final SOC instead of cost, keep the SOC plotting
series hidden from the header with ``show: { in_header: false }`` and add a
separate header-only series for each cost sensor with ``show: { in_chart:
false, in_header: true }``.
