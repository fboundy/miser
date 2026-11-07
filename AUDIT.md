# Miser code audit

Last updated: 2026-11-03. Numbering was re-based on 2026-11-03: items 1 onward come from the review of tariff
robustness, inverter extensibility and settings. When you fix an item, move it to **Fixed** with the commit. Numbers
stay the same across updates, so "fix 3" always means item 3 below. Items from the earlier audit are under
**Earlier audit**, labelled "Old N".

**What this repo does:** Miser is a Home Assistant integration for one home. Every 10 minutes it plans when the
battery should charge from the grid and when it should export. The plan uses Octopus (or any HA entity) prices, a
Solcast solar forecast and the home's past usage. Miser then programs the plan into a Solis inverter and reads it
back to check that it took.

**Review criteria (2026-11-03):**
- Robust for any combination of import and export tariffs.
- Easy to extend beyond Solis through the common `InverterController` class.
- The fewest practical settings, working out of the box for most users; redundant inputs flagged for removal.

## Status

| # | Item | Status | Issue |
|---|------|--------|-------|
| 1 | Negative prices are mis-costed: import and export costs cross over | Fixed, PR from `fix/10-negative-prices` | [#10](https://github.com/fboundy/miser/issues/10) |
| 2 | Setup's system parameters never reach the model | Fixed, PR from `fix/11-setup-parameters` | [#11](https://github.com/fboundy/miser/issues/11) |
| 3 | A low export price in pence can be read as pounds | Fixed, PR from `fix/12-price-unit` | [#12](https://github.com/fboundy/miser/issues/12) |
| 4 | Economy 7 night hard-coded as 01:00–08:00 UTC | Fixed, PR from `fix/13-economy7-hours` | [#13](https://github.com/fboundy/miser/issues/13) |
| 5 | Setup's consumption questions do nothing | Fixed, PR from `fix/23-consumption-steps` | [#23](https://github.com/fboundy/miser/issues/23) |
| 6 | Battery capacity capped at 20 kWh, inverter power at 10 kW | Fixed, PR from `fix/14-size-caps` | [#14](https://github.com/fboundy/miser/issues/14) |
| 7 | `InverterController` doesn't describe what Miser needs | Open | [#15](https://github.com/fboundy/miser/issues/15) |
| 8 | Entity discovery and controller lookup are Solis-only | Open | [#16](https://github.com/fboundy/miser/issues/16) |
| 9 | Solis quirks live in the shared optimiser | Open | [#17](https://github.com/fboundy/miser/issues/17) |
| 10 | Four overlapping pence thresholds for inverter churn | Open | [#18](https://github.com/fboundy/miser/issues/18) |
| 11 | Switches that should be automatic | Open | [#19](https://github.com/fboundy/miser/issues/19) |
| 12 | Whole Horizon or the heuristic passes: pick one | Open | [#20](https://github.com/fboundy/miser/issues/20) |
| 13 | Tuning numbers that should be hidden or merged | Open | [#21](https://github.com/fboundy/miser/issues/21) |
| 14 | "Tariff codes" price source duplicates "Octopus account" | Not substantiated: the only Octopus source that needs no API key | — |
| 15 | Intelligent Octopus Go slots and export limits not modelled | Open | [#22](https://github.com/fboundy/miser/issues/22) |

## Open

### Should fix

None.

### Nice to have

7. **`InverterController` doesn't describe what Miser needs** (`inverters/controller.py`) — [#15](https://github.com/fboundy/miser/issues/15)
   - **What happens:** The optimiser looks up `control_idle`, `power_to_current`, `ensure_backup_soc`,
     `supports_backup_mode` and `confirm_control` by name and silently skips any that are missing, and it sets
     `backup_mode`/`backup_soc` attributes on the controller (`optimiser.py:534, 557, 845, 1176`). A new brand
     without `control_idle` would never go idle. Meanwhile `set_mode`/`get_mode` are abstract but shared code never
     calls them.
   - **Fix:** make `control_idle` abstract; give the others default implementations on the base class (no-op, no
     backup support, power passed in watts); replace the attribute pokes with a `set_backup(on, soc)` method; drop
     `set_mode`/`get_mode` from the base class.

8. **Entity discovery and controller lookup are Solis-only** (`inverters/solis.py`, `inverters/__init__.py:25`) — [#16](https://github.com/fboundy/miser/issues/16)
   - **What happens:** `brand`, `integration`, `entity_defs` and the model/control/config entity maps are defined on
     `SolisInverter`, but setup and the config flow rely on them. `get_inverter_controller_class` special-cases
     Solis. A second brand would have to copy Solis' structure without the base class saying so.
   - **Fix:** move those attributes to `InverterController`; look up every brand through the same registry.

9. **Solis quirks live in the shared optimiser** (`optimiser.py:740`, `optimiser.py:805`, `const.py`) — [#17](https://github.com/fboundy/miser/issues/17)
   - **What happens:** The midnight split and 00:01/23:59 edges (`_inverter_window`), programming only one slot at a
     time, and the `CONTROL_TIMED_*` / "update times" keys are Solis behaviour in shared code. Battery power is
     turned into a current through battery voltage, which other inverters don't need.
   - **Fix:** controller properties the optimiser reads, e.g. `max_window_days`, `allows_midnight_edges`,
     `max_slots`; move the Solis entity keys into `solis.py`.

10. **Four overlapping pence thresholds for inverter churn** — [#18](https://github.com/fboundy/miser/issues/18)
    - Write-minimisation threshold, Whole Horizon write cost, alternation cost threshold and cycling threshold are all
      "how many pence a change must be worth". Replace with one "minimum worthwhile change" number or fixed values.

11. **Switches that should be automatic** — [#19](https://github.com/fboundy/miser/issues/19)
    - **Use Solar:** no Solcast already means zero solar. Remove.
    - **Use consumption history / Shape consumption:** use history when there is some, else the built-in shape. Remove.
    - **Optimise Discharging:** with a low or missing export price the plan won't export anyway. Remove. It also
      doesn't stop exports: it only drops the discharge and fill-first plans (`optimiser.py:264-267`), and Whole
      Horizon still plans exports.
    - **Value Surplus SOC:** always on. Remove.
    - Keep Read Only (default on, so the first run writes nothing; say so in setup) and Backup Mode.

12. **Whole Horizon or the heuristic passes: pick one** — [#20](https://github.com/fboundy/miser/issues/20)
    - The Whole Horizon (beta) switch runs two planners side by side. Decide which is better on real nights; if Whole
      Horizon, delete the swap, low-cost charging, discharge and fill-first passes (about 600 lines) and the switch.

13. **Tuning numbers that should be hidden or merged** — [#21](https://github.com/fboundy/miser/issues/21)
    - Hide with fixed defaults: inverter and charger efficiency, history days, weekday weighting, load margin,
      optimiser frequency.
    - Merge daily consumption and minimum daily consumption into one fallback figure.
    - The live install has history days at 5, which silently disables weekday weighting (it needs 7).

14. **"Tariff codes" price source duplicates "Octopus account"** — not substantiated
    - Checked 2026-11-03: the codes source reads Octopus' public rate endpoints with no API key, so it is the only
      Octopus source for a user without one. Keep it; no issue raised.

15. **Intelligent Octopus Go slots and export limits not modelled** — [#22](https://github.com/fboundy/miser/issues/22)
    - IOG's extra cheap dispatch slots aren't seen unless the price entities include them. A G99 export cap is
      treated as full inverter power. Document both, or read the cap as a setting only when exporting is limited.

## Fixed

4. **Economy 7 night hard-coded as 01:00–08:00 UTC** (`octopus.py:235`) — [#13](https://github.com/fboundy/miser/issues/13)
   - **What happens:** Real E7 hours vary by region and meter, and some are split. Affects the "Octopus account"
     and "tariff codes" price sources; the Octopus Energy integration's entities carry the real times.
   - **Fix:** take the night hours from the meter's registers if the account API gives them; otherwise make the
     window a setup input shown only for E7 tariffs, or point E7 users at the entities source.
   - **Fixed** (`fix/13-economy7-hours`): The night window is Octopus' standard 00:30–07:30 UTC (01:30–08:30 BST), per Octopus' guidance; it was 01:00–08:00 UTC. The rates API gives no times, so meters on other hours still need the price-entities source; the code comment says so. Tested in `tests/test_octopus_tariff_rates.py`.

6. **Battery capacity capped at 20 kWh, inverter power at 10 kW** (`const.py`, `NUMBER_ENTITIES`) — [#14](https://github.com/fboundy/miser/issues/14)
   - **What happens:** Larger and three-phase systems can't be entered. Charger power is capped at 5 kW.
   - **Fix:** raise the maxima (e.g. 100 kWh, 30 kW, 30 kW).
   - **Fixed** (`fix/14-size-caps`): Maxima raised to 100 kWh battery, 30 kW inverter and 30 kW charger.

5. **Setup's consumption questions do nothing** (`config_flow.py:386-442`) — [#23](https://github.com/fboundy/miser/issues/23)
   - **What happens:** "Consumption source", "daily amount" and "scaling option" are saved and never read. The
     `use_consumption_history` switch and `daily_consumption_kwh` number decide instead.
   - **Fix:** delete the two steps (see 11 and 13 for the settings that replace them).
   - **Fixed** (`fix/23-consumption-steps`): Deleted the "consumption source" and "daily amount" steps; setup now ends after the system parameters. Consumption still comes from history with the built-in shape as fallback, tuned by the existing switches and numbers (see #19, #21).

2. **Setup's system parameters never reach the model** (`config_flow.py:341`, `number.py:88`) — [#11](https://github.com/fboundy/miser/issues/11)
   - **What happens:** Setup asks for battery capacity, inverter power, charger power and both efficiencies and
     saves them in the entry's options, but nothing reads them. The number entities start from `DEFAULTS`, so a new
     user who enters 20 kWh is planned as 10 kWh until they change `number.miser_battery_capacity` by hand.
   - **Fix:** seed each number entity's first value from the options (restored state still wins), or drop the step.
     Where the inverter reports a figure (Solis model → charger power), use that.
   - **Fixed** (`fix/11-setup-parameters`): Each number entity's first value comes from the setup option of the same key (battery capacity, inverter and charger power, both efficiencies, and the daily consumption amount); a restored state still wins. Reading charger power from the inverter model is left for #15/#16. Tested in `tests/test_number_initial_value.py`.

3. **A low export price in pence can be read as pounds** (`entity_tariff.py:109`, `_scale`) — [#12](https://github.com/fboundy/miser/issues/12)
   - **What happens:** With the price unit on "auto", an entity with no unit whose prices are all below 2 is
     multiplied by 100. A flat 1.5p SEG export becomes 150p and Miser plans to export everything.
   - **Reproduced 2026-11-03:** a unitless export entity at `1.5` comes out as 150p/kWh.
   - **Fix:** decide from the unit only; with no unit, assume the unit the user chose in setup and log it once.
     Keep a sanity warning for implausible values rather than rescaling.
   - **Fixed** (`fix/12-price-unit`): A price entity's own unit (or an Octopus Energy rate event, always pounds) decides the scale. Setup no longer accepts "Detect automatically" when an entity has no unit; it asks for pounds or pence. Older "auto" entries still guess for unitless entities but log a warning naming the entity. Tested in `tests/test_entity_tariff.py`.

1. **Negative prices are mis-costed: import and export costs cross over** (`pv_model.py:252-253`, `flows`) — [#10](https://github.com/fboundy/miser/issues/10)
   - **What happens:** `import_cost` and `export_cost` are split by the sign of `grid × price`, not of `grid`. With a
     negative price each picks up the other direction's energy.
   - **Reproduced 2026-11-03:** a 6 kWh grid charge at −5p import (−2p export) costs −12p in the model (the export
     price on an import); correct is −30p. A 5.8 kWh export at −2p export (−5p import) costs +29.1p (the import
     price on an export); correct is +11.6p.
   - **Also:** Whole Horizon costs slots correctly (`pv_model.py:910`), so the two planners disagree on negative slots.
   - **Fix:** `import_cost = grid.clip(lower=0) × dt × import`, `export_cost = grid.clip(upper=0) × dt × export`.
     Add a negative-price test.
   - **Fixed** (`fix/10-negative-prices`): `flows` splits import and export cost by the sign of `grid` (direction of flow), so negative prices are costed correctly. Tested in `tests/test_negative_prices.py`.

## Not checked

- All 184 tests pass on Linux.
- Items 1 and 3 were reproduced in the model. Item 4 is from the code: the hours are hard-coded, but which users'
  meters differ wasn't checked. Items 2 and 5–13 are from the code paths named in each item.
- No second inverter brand was tried.
- Not on the inverter yet: a window split at midnight (Old 20) and a whole night with idle slots averaged (Old 21).
- Five removed entities still show as unavailable in the live registry (`power_resolution`, `sleep_soc`,
  `inverter_loss`, `include_export`, `prevent_cycling`); remove them in the HA UI.
- Not read closely: `switch.py`, `sensor.py`, the docs and the workflows.
- Clock-change days (BST/GMT) aren't tested.

## Earlier audit

Numbering before 2026-11-03. All items are closed; kept for the record.

### Status (old numbering)

| Old # | Item | Status |
|---|------|--------|
| 1 | Two planning runs at once corrupt each other | Fixed `40ff3a7` |
| 2 | High-cost pass crashes when no slot can take a charge | Fixed `40ff3a7` |
| 3 | Agile Predict fetched every run; a failure kills the run | Fixed `40ff3a7` |
| 4 | Discharge capped by charger power | Won't fix: the Solis rejects >~62.5 A (`af684f7`); datasheet: 3 kW, 62.5 A battery limit on the 3–3.6K models; 70 A retest 2026-11-02 reverted |
| 5 | Five settings appear in HA but do nothing | Fixed (see below) |
| 6 | Setup fails for good instead of retrying | Fixed `f0ce538` |
| 7 | Big plan attributes saved to the HA database every run | Fixed `f0ce538` |
| 8 | Battery model loop is slow | Fixed (see below) |
| 9 | Device-name helper exists twice and the copies disagree | Fixed (see below) |
| 10 | Standing charge downloaded hourly, never used | Fixed (see below) |
| 11 | Three Solis controllers copy the same methods | Fixed (see below) |
| 12 | Controller code nothing calls | Fixed (see below) |
| 13 | Commented-out code and broken leftover methods | Fixed (see below) |
| 14 | `_write_status` defined twice | Fixed (see below) |
| 15 | Schedule "alignment" code does nothing | Fixed (see below) |
| 16 | Fallback load shape matched against UTC times | Fixed (see below) |
| 17 | Write check trusts SolisConnect's echo, not the inverter | Fixed (see below) |
| 18 | Inverter keeps an old charge window | Fixed `1e3e5f1` |
| 19 | A planned charge blocks export in a high-export slot (missed Axle event) | Fixed (see below) |
| 20 | Windows that cross midnight or touch 00:00 are written as-is | Fixed (see below) |
| 21 | Whole Horizon leaves idle gaps in a flat-price charge period | Fixed (see below) |

### Fixed (old numbering)

- **Old 21:** **Whole Horizon left idle gaps in a flat-price charge period** (`pv_model.py`, `_average_same_price_runs`)
  - **What happened:** On the night of 2026-11-03 (7.43p from 00:00 to 07:00) Miser charged 00:06–01:06, sat idle
      01:06–02:00, charged 02:00–06:06, idled 10 min and charged 06:16–06:26. At 23:30 the plan was 01:30–07:00.
  - **Cause:** Whole Horizon only charges at full power or not at all. When the battery needs less than the whole
      period at full power, every choice of slots costs the same, so it picks some and leaves others idle, and each
      10-minute re-plan can pick differently. `_average_same_price_runs` only joins charge slots that are next to each
      other, so it never fills an idle slot inside the period. Reproduced offline: from 15% at 00:00 it idles to 01:30.
  - **Cost:** small. The house runs from the battery in the gaps and is refilled at the same price, losing the
      charge and discharge efficiency on that energy. A later start also leaves less time if something goes wrong.
  - **Fix:** `_average_same_price_runs` spreads each charge over its whole same-price period, idle slots included.
      It takes off the battery energy those idle slots would have supplied to the house, so the plan ends at the
      same SOC, and keeps the check that the averaged plan costs no more. `_drop_small_cycles` re-averages after
      dropping an export, because a dropped export inside the charge period caused the 01:06–02:00 gap. Tested in
      `tests/test_pv_model_write_cost_and_precharge.py` and `tests/test_cycling_threshold.py`.
  - **Still unchecked:** a whole night on the live system.

- **Old 20:** **Windows that crossed midnight or touched 00:00 were written as-is** (`optimiser.py`, `_inverter_window`)
  - **What happened:** The Solis doesn't take a charge window that crosses midnight, and 00:00 as a start or end is
      unreliable.
  - **Fix:** Miser writes only the part of a window on the local day it is running in (or next starts in), as
      00:01–23:59 at the edges. A compliance check scheduled one minute after midnight writes the rest. A 00:00 start
      due within the run interval is now written ahead as 00:01. Tested in `tests/test_inverter_window_midnight.py`.
  - **Still unchecked on the inverter:** the first split window and the first 00:01 start (tonight's 00:00–07:00).

- **Old 18:** **The inverter kept an old charge window** (2026-11-01 06:00 and all night 2026-11-02)
  - **What happened:** Miser planned a charge from 00:00 to 07:00 on 2026-11-02, but the inverter held 06:13–06:30
      and charged only then. "Control failed" fired 9 times.
  - **Cause (confirmed on the inverter 2026-11-02):** the Solis rejects slot times that overlap another slot's
      times, even when that slot is disabled in 43707. About 30 s after the write it restores the last valid times
      for both slots. Miser left the discharge slot's old times in place when it switched to charging.
  - **Wrong first theory:** that SOC/current writes reverted the times and that only a 43707 write saved them
      (`4464f65`, and SolisConnect issue #42 as first written). Those writes merely came just before the revert. The
      issue now has a correction; `4464f65`'s write order and direct 43707 write are removed.
  - **Fix:** before writing a charge window, `SolisConnectInverter` turns the discharge slot off and sets its times to
      00:00–00:00 (and the other way round for a discharge). It skips that when the registers already read 00:00.
      Tested in `tests/test_solis_slot_exclusion.py`.
  - **Seen working 2026-11-03:** Miser wrote 00:06–07:00 with the discharge slot at 00:00–00:00. The times held, a
      rewrite at 00:16 held too, and the battery charged at about 2.4 kW from the grid.
  - **Still unchecked:** whether a window that spans midnight (e.g. 23:30–01:00) counts as overlapping 00:00–00:00.

- **Old 16:** **The fallback load shape was matched against UTC times**
  - **Fix:** `_fallback_consumption` looks up the shape by local clock time. In summer time the evening peak now
      starts at 17:00 local, not 18:00. Tested in `tests/test_fallback_consumption_local_time.py`.

- **Old 14:** **`_write_status` was defined twice**
  - **Fix:** `__init__.py` imports the optimiser's copy.

- **Old 15:** **The schedule's "alignment" code did nothing**
  - **Fix:** Deleted it. Runs still start 2 minutes after setup and repeat every interval from then, exactly as before.

- **Old 13:** **Commented-out code and broken leftover methods**
  - **Fix:** Deleted the old optimiser code in comments at the end of `pv_model.py`, the three `__str__` methods
      that returned `None`, `Tariff.start`/`end`, `manual`, `is_intelligent` and the commented Intelligent block.
      `Tariff.__init__` now takes only the two arguments callers pass. About 170 lines removed.

- **Old 11:** **The three Solis controllers copied the same methods**
  - **Fix:** `set_mode`, `get_mode` and `power_to_current` now live once, in `SolisInverter`. SolisConnect keeps its
      own `get_mode`, which reads the work-mode register.

- **Old 12:** **Controller code nothing called**
  - **Fix:** Deleted `get_time`, `set_time`, `get_status`, the date parser, the RTC and clock entity definitions
      and the charge/discharge branches of `control_matches`. `control_matches` now answers only "idle", the only
      state the optimiser asks about. Items 11 and 12 together removed about 250 lines.

- **Old 10:** **The standing charge was downloaded every hour and never used**
  - **Fix:** Deleted the standing-charge download, the `fixed` price column and its URL. One fewer Octopus call per
      refresh, and one fewer way for a refresh to fail.

- **Old 19:** **A planned charge blocked export in a high-export slot** (`pv_model.py`, `discharging`)
  - **What happened:** On 2026-11-01 Axle paid 100p/kWh for export from 06:00 to 07:00. At 06:00, low-cost charging
      put a top-up charge in that slot, because its import price was below the highest export price. The discharge pass
      skipped any slot with a planned charge, so the house imported 2.2 kW at the start of the event and exported
      under 1 kWh over the hour.
  - **Fix:** The discharge pass now also tries slots with a planned charge. It drops that charge, tries an export,
      and keeps the swap only if the plan gets cheaper. The fix isn't specific to Axle; any slot with a high export
      price is handled the same way. Tested in `tests/test_pv_model_discharge_over_charge.py`.
  - **Possible link:** Item 18's failed charge window was written during this event, so Axle may have been
      controlling the inverter at the time. Not proven.

- **Old 8:** **The battery model loop was slow**
  - **Fix:** `flows()` now loops over the two columns as plain Python floats instead of a `.loc` lookup per row. On a
     96-slot test day the three main passes took 42 s instead of 68 s, with the same cost and slots. Battery charge
     figures can differ by rounding (at most 0.1 Wh per slot). On the live system, the first run took 31 s, against a
     median of 64 s over the day before.

- **Old 5:** **Five settings appeared in HA but did nothing**
  - **Fix:** "Use Solar" now works: when it is off, `_get_solcast` uses a zero solar forecast. "Include Export",
     "Power Resolution", "Sleep SOC" and "Inverter Loss" are deleted. Tested in `tests/test_use_solar.py`.
  - **Leftover:** HA keeps the four deleted entities in its registry as "no longer provided" until you remove them in the UI.

- **Old 9:** **The device-name helper existed twice and the copies disagreed**
  - **Fix:** One copy, `infer_integration_device_name` in `utils.py`, now serves both the config flow and setup. It
     counts only the model entities, which are the ones setup requires, and returns `None` instead of guessing.
     On the live registry it still picks `solis_s5_eh1p`. Tested in `tests/test_setup_missing_inverter.py`.

- **Old 17:** **The write check trusted SolisConnect's echo, not the inverter** (`inverters/solis.py`, `SolisConnectInverter`)
  - **What happened:** SolisConnect updates its switch, time and number entities as soon as it sends a write. From
      00:00 to 00:38 on 2026-10-31 the inverter did not take the charge slot. Miser's check still passed and it
      showed "Charging".
  - **Fix:** Now the write check reads the inverter's own registers. It reads the slot-enable bits of register
      43707 (`sensor.*_time_of_use_v2_switch`: bit 0 is charge slot 1, bit 6 is discharge slot 1). It also reads
      the slot-1 start/end hour and minute sensors. `control_matches` reads the same bits. The guard that keeps
      charge and discharge from overlapping still reads the switch entity, because the register can lag a write
      by up to 30 s. Tested in `tests/test_solis_slot_exclusion.py`.
  - **Still unchecked:** SolisConnect has no read-only sensor for the slot's cut-off SOC and current, so those
      two are still checked against SolisConnect's own entities.
