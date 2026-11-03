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

| # | Item | Status |
|---|------|--------|
| 1 | Negative prices are costed as zero | Open |
| 2 | Setup's system parameters never reach the model | Open |
| 3 | A low export price in pence can be read as pounds | Open |
| 4 | Economy 7 night hard-coded as 01:00–08:00 UTC | Open |
| 5 | Setup's consumption questions do nothing | Open |
| 6 | Battery capacity capped at 20 kWh, inverter power at 10 kW | Open |
| 7 | `InverterController` doesn't describe what Miser needs | Open |
| 8 | Entity discovery and controller lookup are Solis-only | Open |
| 9 | Solis quirks live in the shared optimiser | Open |
| 10 | Four overlapping pence thresholds for inverter churn | Open |
| 11 | Switches that should be automatic | Open |
| 12 | Whole Horizon or the heuristic passes: pick one | Open |
| 13 | Tuning numbers that should be hidden or merged | Open |
| 14 | "Tariff codes" price source duplicates "Octopus account" | Open |
| 15 | Intelligent Octopus Go slots and export limits not modelled | Open |

## Open

### Should fix

1. **Negative prices are costed as zero** (`pv_model.py:252-253`, `flows`)
   - **What happens:** `import_cost` is clipped at zero and `export_cost` at zero. On Agile, being paid to import
     is costed as free, so the plan doesn't fill the battery or run the house from the grid in those slots. A
     negative export price is costed as free, so the plan doesn't avoid exporting then.
   - **Also:** Whole Horizon costs slots without the clip (`pv_model.py:910`), so the two planners disagree, and the
     final cost Miser compares plans by comes from `flows`.
   - **Fix:** drop both clips; `grid × price` already has the right sign. Add a test with a negative import slot.

2. **Setup's system parameters never reach the model** (`config_flow.py:341`, `number.py:88`)
   - **What happens:** Setup asks for battery capacity, inverter power, charger power and both efficiencies and
     saves them in the entry's options, but nothing reads them. The number entities start from `DEFAULTS`, so a new
     user who enters 20 kWh is planned as 10 kWh until they change `number.miser_battery_capacity` by hand.
   - **Fix:** seed each number entity's first value from the options (restored state still wins), or drop the step.
     Where the inverter reports a figure (Solis model → charger power), use that.

3. **A low export price in pence can be read as pounds** (`entity_tariff.py:109`, `_scale`)
   - **What happens:** With the price unit on "auto", an entity with no unit whose prices are all below 2 is
     multiplied by 100. A flat 1.5p SEG export becomes 150p and Miser plans to export everything.
   - **Fix:** decide from the unit only; with no unit, assume the unit the user chose in setup and log it once.
     Keep a sanity warning for implausible values rather than rescaling.

4. **Economy 7 night hard-coded as 01:00–08:00 UTC** (`octopus.py:235`)
   - **What happens:** Real E7 hours vary by region and meter, and some are split. Affects the "Octopus account"
     and "tariff codes" price sources; the Octopus Energy integration's entities carry the real times.
   - **Fix:** take the night hours from the meter's registers if the account API gives them; otherwise make the
     window a setup input shown only for E7 tariffs, or point E7 users at the entities source.

5. **Setup's consumption questions do nothing** (`config_flow.py:386-442`)
   - **What happens:** "Consumption source", "daily amount" and "scaling option" are saved and never read. The
     `use_consumption_history` switch and `daily_consumption_kwh` number decide instead.
   - **Fix:** delete the two steps (see 11 and 13 for the settings that replace them).

6. **Battery capacity capped at 20 kWh, inverter power at 10 kW** (`const.py`, `NUMBER_ENTITIES`)
   - **What happens:** Larger and three-phase systems can't be entered. Charger power is capped at 5 kW.
   - **Fix:** raise the maxima (e.g. 100 kWh, 30 kW, 30 kW).

### Nice to have

7. **`InverterController` doesn't describe what Miser needs** (`inverters/controller.py`)
   - **What happens:** The optimiser looks up `control_idle`, `power_to_current`, `ensure_backup_soc`,
     `supports_backup_mode` and `confirm_control` by name and silently skips any that are missing, and it sets
     `backup_mode`/`backup_soc` attributes on the controller (`optimiser.py:534, 557, 845, 1176`). A new brand
     without `control_idle` would never go idle. Meanwhile `set_mode`/`get_mode` are abstract but shared code never
     calls them.
   - **Fix:** make `control_idle` abstract; give the others default implementations on the base class (no-op, no
     backup support, power passed in watts); replace the attribute pokes with a `set_backup(on, soc)` method; drop
     `set_mode`/`get_mode` from the base class.

8. **Entity discovery and controller lookup are Solis-only** (`inverters/solis.py`, `inverters/__init__.py:25`)
   - **What happens:** `brand`, `integration`, `entity_defs` and the model/control/config entity maps are defined on
     `SolisInverter`, but setup and the config flow rely on them. `get_inverter_controller_class` special-cases
     Solis. A second brand would have to copy Solis' structure without the base class saying so.
   - **Fix:** move those attributes to `InverterController`; look up every brand through the same registry.

9. **Solis quirks live in the shared optimiser** (`optimiser.py:740`, `optimiser.py:805`, `const.py`)
   - **What happens:** The midnight split and 00:01/23:59 edges (`_inverter_window`), programming only one slot at a
     time, and the `CONTROL_TIMED_*` / "update times" keys are Solis behaviour in shared code. Battery power is
     turned into a current through battery voltage, which other inverters don't need.
   - **Fix:** controller properties the optimiser reads, e.g. `max_window_days`, `allows_midnight_edges`,
     `max_slots`; move the Solis entity keys into `solis.py`.

10. **Four overlapping pence thresholds for inverter churn**
    - Write-minimisation threshold, Whole Horizon write cost, alternation cost threshold and cycling threshold are all
      "how many pence a change must be worth". Replace with one "minimum worthwhile change" number or fixed values.

11. **Switches that should be automatic**
    - **Use Solar:** no Solcast already means zero solar. Remove.
    - **Use consumption history / Shape consumption:** use history when there is some, else the built-in shape. Remove.
    - **Optimise Discharging:** with a low or missing export price the plan won't export anyway. Remove.
    - **Value Surplus SOC:** always on. Remove.
    - Keep Read Only (default on, so the first run writes nothing; say so in setup) and Backup Mode.

12. **Whole Horizon or the heuristic passes: pick one**
    - The Whole Horizon (beta) switch runs two planners side by side. Decide which is better on real nights; if Whole
      Horizon, delete the swap, low-cost charging, discharge and fill-first passes (about 600 lines) and the switch.

13. **Tuning numbers that should be hidden or merged**
    - Hide with fixed defaults: inverter and charger efficiency, history days, weekday weighting, load margin,
      optimiser frequency.
    - Merge daily consumption and minimum daily consumption into one fallback figure.
    - The live install has history days at 5, which silently disables weekday weighting (it needs 7).

14. **"Tariff codes" price source duplicates "Octopus account"**
    - The account source finds the codes itself. Keep codes only if there's a user without an API key.

15. **Intelligent Octopus Go slots and export limits not modelled**
    - IOG's extra cheap dispatch slots aren't seen unless the price entities include them. A G99 export cap is
      treated as full inverter power. Document both, or read the cap as a setting only when exporting is limited.

## Fixed

None yet under the new numbering.

## Not checked

- All 184 tests pass on Linux.
- Items 1, 3 and 4 come from reading the code; none was run against a negative-price, unitless-entity or E7 case.
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
