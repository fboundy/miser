# Miser code audit

Last updated: 2026-10-31. When you fix an item, move it to **Fixed** with the commit.
Numbers stay the same across updates, so "fix 9" always means the same item.

**What this repo does:** Miser is a Home Assistant integration for one home. Every 10 minutes it plans when the
battery should charge from the grid and when it should export. The plan uses Octopus prices, a Solcast solar
forecast and the home's past usage. Miser then programs the plan into a Solis inverter and reads it back to
check that it took.

Assumed load: one house, one HA install, one Miser entry. Several triggers can still start runs inside that
one process at once. These are the timer, a settings change and Axle events.

## Status

| # | Item | Status |
|---|------|--------|
| 1 | Two planning runs at once corrupt each other | Fixed `40ff3a7` |
| 2 | High-cost pass crashes when no slot can take a charge | Fixed `40ff3a7` |
| 3 | Agile Predict fetched every run; a failure kills the run | Fixed `40ff3a7` |
| 4 | Discharge capped by charger power | Won't fix: the Solis rejects >~62.5 A (`af684f7`) |
| 5 | Five settings appear in HA but do nothing | Fixed (see below) |
| 6 | Setup fails for good instead of retrying | Fixed `f0ce538` |
| 7 | Big plan attributes saved to the HA database every run | Fixed `f0ce538` |
| 8 | Battery model loop is slow | Open |
| 9 | Device-name helper exists twice and the copies disagree | Fixed (see below) |
| 10 | Standing charge downloaded hourly, never used | Open |
| 11 | Three Solis controllers copy the same methods | Open |
| 12 | Controller code nothing calls | Open |
| 13 | Commented-out code and broken leftover methods | Open |
| 14 | `_write_status` defined twice | Open |
| 15 | Schedule "alignment" code does nothing | Open |
| 16 | Fallback load shape matched against UTC times | Open |
| 17 | Write check trusts SolisConnect's echo, not the inverter | Fixed (see below) |

## Open

### Should fix

8. **The battery model loop is slow** (`pv_model.py:228-252`)
   - **What this is:** `flows()` simulates the battery one half-hour at a time. It is called hundreds of times per run.
   - **Problem:** Each step reads `df["battery_temp"].loc[idx]` and `df["dt_hours"].loc[idx]`. These are slow pandas
     lookups. A run takes up to a minute, and that is how long the optimiser lock is held.
   - **Fix:** Before the loop, take `.to_numpy()` of both columns and loop over plain floats. The results stay the same.
   - **If we skip it:** Runs stay long, and the background thread and CPU stay busy.

10. **The standing charge is downloaded every hour and never used** (`octopus.py:326, 402`; `entity_tariff.py:94`)
    - **What this is:** The `fixed` column holds the daily standing charge.
    - **Problem:** No cost calculation reads it. It costs one extra Octopus API call per hour.
    - **Fix:** Delete the fetch and the `fixed` column.
    - **If we skip it:** Wasted API calls, and one more way for a refresh to fail.

11. **The three Solis controllers copy the same methods** (`inverters/solis.py`: `set_mode`, `get_mode`, `power_to_current` in each subclass)
    - **Problem:** The copies match word for word. A fix that lands in one copy is missed in the other two.
    - **Fix:** Move them into `SolisInverter`. SolisConnect keeps its own `get_mode`, which reads the register.
      That removes about 40 lines.
    - **If we skip it:** The copies drift apart.

12. **Controller code nothing calls** (`inverters/solis.py`, `inverters/controller.py`)
    - **What this is:** These are `get_time`, `set_time`, `get_status`, and the charge/discharge branches of
      `control_matches`. The only caller passes `"idle"` (`optimiser.py:531`).
    - **Fix:** Delete them, along with the RTC/clock entity definitions, and keep only the idle check.
      That is about 150 lines.
    - **If we skip it:** People read and maintain code that never runs.

### Nice to have

13. **Commented-out code and broken leftover methods** (`pv_model.py` tail, `octopus.py:294-317`, `octopus.py:361, 368`)
    - **Problem:**
      - There are about 80 lines of old optimiser code in comments.
      - `Tariff.__init__` ignores seven of its arguments.
      - `Tariff.start` and `Tariff.end` use `self.tz`, which doesn't exist, so they would crash.
      - Three `__str__` methods return `None`, so `str(model)` raises `TypeError`.
    - **Fix:** Delete all of it.

14. **`_write_status` is defined twice** (`__init__.py:491`, `optimiser.py:404`)
    - **Fix:** Import the optimiser version in `__init__.py`.

15. **The schedule's "alignment" code does nothing** (`__init__.py:428-465`)
    - **Problem:** It computes `next_run` but only logs it. `async_track_time_interval` counts from "now", so runs
      are not lined up to :00, :10, and so on.
    - **Fix:** Use `async_track_utc_time_change(minute=range(0, 60, n), second=5)`, or delete the alignment code.

16. **The fallback load shape is matched against UTC times** (`optimiser.py:1787, 1909-1918`)
    - **Problem:** `CONSUMPTION_SHAPE` hours are local clock time, but `time_of_day` comes from the UTC index. In
      summer time the modelled evening peak lands an hour late. This only matters when there is no usage history.
    - **Fix:** Take `time_of_day` from `index.tz_convert(dt_util.DEFAULT_TIME_ZONE).time`.

## Fixed

5. **Five settings appeared in HA but did nothing**
   - **Fix:** "Use Solar" now works: when it is off, `_get_solcast` uses a zero solar forecast. "Include Export",
     "Power Resolution", "Sleep SOC" and "Inverter Loss" are deleted. Tested in `tests/test_use_solar.py`.
   - **Leftover:** HA keeps the four deleted entities in its registry as "no longer provided" until you remove them in the UI.

9. **The device-name helper existed twice and the copies disagreed**
   - **Fix:** One copy, `infer_integration_device_name` in `utils.py`, now serves both the config flow and setup. It
     counts only the model entities, which are the ones setup requires, and returns `None` instead of guessing.
     On the live registry it still picks `solis_s5_eh1p`. Tested in `tests/test_setup_missing_inverter.py`.

17. **The write check trusted SolisConnect's echo, not the inverter** (`inverters/solis.py`, `SolisConnectInverter`)
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

## Not checked

- I didn't measure the speed of item 8.
- On Windows, `tests/test_config_flow.py` can't run, because the HA test harness needs `fcntl`.
  The other 160 tests pass.
- I didn't measure the database size for item 7.
- Not read closely: most of `config_flow.py`, `switch.py`, the docs and the workflows.
- Clock-change days (BST/GMT) aren't tested.
