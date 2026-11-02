# Miser code audit

Last updated: 2026-11-01. When you fix an item, move it to **Fixed** with the commit.
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
| 18 | Inverter keeps an old charge window | Fixed (see below); first overnight charge not yet seen |
| 19 | A planned charge blocks export in a high-export slot (missed Axle event) | Fixed (see below) |

## Open

### Should fix

None.

## Fixed

18. **The inverter kept an old charge window** (2026-11-01 06:00 and all night 2026-11-02)
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
    - **Still unchecked:** whether a window that spans midnight (e.g. 23:30–01:00) counts as overlapping 00:00–00:00.

16. **The fallback load shape was matched against UTC times**
    - **Fix:** `_fallback_consumption` looks up the shape by local clock time. In summer time the evening peak now
      starts at 17:00 local, not 18:00. Tested in `tests/test_fallback_consumption_local_time.py`.

14. **`_write_status` was defined twice**
    - **Fix:** `__init__.py` imports the optimiser's copy.

15. **The schedule's "alignment" code did nothing**
    - **Fix:** Deleted it. Runs still start 2 minutes after setup and repeat every interval from then, exactly as before.

13. **Commented-out code and broken leftover methods**
    - **Fix:** Deleted the old optimiser code in comments at the end of `pv_model.py`, the three `__str__` methods
      that returned `None`, `Tariff.start`/`end`, `manual`, `is_intelligent` and the commented Intelligent block.
      `Tariff.__init__` now takes only the two arguments callers pass. About 170 lines removed.

11. **The three Solis controllers copied the same methods**
    - **Fix:** `set_mode`, `get_mode` and `power_to_current` now live once, in `SolisInverter`. SolisConnect keeps its
      own `get_mode`, which reads the work-mode register.

12. **Controller code nothing called**
    - **Fix:** Deleted `get_time`, `set_time`, `get_status`, the date parser, the RTC and clock entity definitions
      and the charge/discharge branches of `control_matches`. `control_matches` now answers only "idle", the only
      state the optimiser asks about. Items 11 and 12 together removed about 250 lines.

10. **The standing charge was downloaded every hour and never used**
    - **Fix:** Deleted the standing-charge download, the `fixed` price column and its URL. One fewer Octopus call per
      refresh, and one fewer way for a refresh to fail.

19. **A planned charge blocked export in a high-export slot** (`pv_model.py`, `discharging`)
    - **What happened:** On 2026-11-01 Axle paid 100p/kWh for export from 06:00 to 07:00. At 06:00, low-cost charging
      put a top-up charge in that slot, because its import price was below the highest export price. The discharge pass
      skipped any slot with a planned charge, so the house imported 2.2 kW at the start of the event and exported
      under 1 kWh over the hour.
    - **Fix:** The discharge pass now also tries slots with a planned charge. It drops that charge, tries an export,
      and keeps the swap only if the plan gets cheaper. The fix isn't specific to Axle; any slot with a high export
      price is handled the same way. Tested in `tests/test_pv_model_discharge_over_charge.py`.
    - **Possible link:** Item 18's failed charge window was written during this event, so Axle may have been
      controlling the inverter at the time. Not proven.

8. **The battery model loop was slow**
   - **Fix:** `flows()` now loops over the two columns as plain Python floats instead of a `.loc` lookup per row. On a
     96-slot test day the three main passes took 42 s instead of 68 s, with the same cost and slots. Battery charge
     figures can differ by rounding (at most 0.1 Wh per slot). On the live system, the first run took 31 s, against a
     median of 64 s over the day before.

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

- All 172 tests pass on Linux, including `tests/test_config_flow.py`.
- I didn't measure the database size for item 7.
- Not read closely: most of `config_flow.py`, `switch.py`, the docs and the workflows.
- Clock-change days (BST/GMT) aren't tested.
