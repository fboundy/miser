# Two nights without an overnight charge (18-19 and 19-20 Sep 2026)

- Author: Claude, 2026-09-20 (morning)
- Evidence: InfluxDB `ToppinHouse` (HA sensor history, 30 s Solis register reads). `miser.log`
  and the HA journal had both rotated past the incidents.

## Night 1, 18-19 Sep: Miser was not running

Last optimiser cycle 14:06 BST on the 18th. Something reloaded the Miser config entry at
~14:15 (no HA restart), and from 14:21 the Miser sensors were rewritten with identical stale
values at 21 s, 42 s, 81 s, 161 s, 321 s, then every 10 min: Home Assistant's
`ConfigEntryNotReady` retry back-off. `miser_state` never returned to `Optimising`. The loop
ran 27 h until the HA restart at 16:57 on the 19th. Nothing was written to the inverter;
the battery self-used 61 % -> 27 %.

## Night 2, 19-20 Sep: Miser wrote, the inverter did not take it

Plan: 3 kW, 00:30-05:30 BST, target ~83 %. Every 10 min Miser wrote start=now, end=05:30,
target, current, enable. Register-level sequence each cycle (UTC):

    00:10:08.42  start written -> regs flash 01:09
    00:10:08.63  target SOC 74 written (sticks)
    00:10:11.61  next poll reads the slot: 18:30 -> 19:00

18:30-19:00 was Miser's own write from 18:30 the previous evening: a 38 W "forced charge"
segment (0.7 A, target 43 %). The inverter snapped back to it after almost every write from
01:10 to 04:40 BST. During the spells when it did report Miser's window (00:39-01:10,
01:31-01:40) battery power stayed ~35 W. At 04:49 BST a write finally took; SOC 15 -> 20 %
before the window closed. The identical rolling-start pattern worked on 17-18 Sep
(34 -> 99 %). Modbus was healthy, slot 1 was the only enabled slot, inverter clock correct.
Why the inverter refused for those hours is not visible from HA-side data.

Miser-side faults:

1. The first write of the night was 00:30 -> **17:00 next day** (target 88 %): the control
   window merged contiguous daytime forced-charge flows (~300 W) with the cheap-rate charge.
   Rejected outright by the inverter.
2. No read-back. Writes were `blocking=False` and `control_matches` never checked times, so
   Miser reported `Charging` all night against a battery drawing 35 W.
3. A 38 W forced-charge segment is not a control worth writing.

## Fixes (this commit)

- Writes are blocking and every charge/discharge control is read back after
  `CONTROL_CONFIRM_DELAY_SECONDS` (45 s, past SolisConnect's 30 s poll), retried up to
  `CONTROL_CONFIRM_ATTEMPTS` (3), then reported as `Control failed` with an ERROR log and a
  persistent notification.
- A control window never spans an import/export price change (`PRICE_PERIOD_TOLERANCE`).
- Forced flows below `CONTROL_MIN_FORCE_POWER` (100 W) are not controls.
- `async_setup_entry` resets `setup_in_progress` in `finally` (cancellation-safe) and logs
  the `ConfigEntryNotReady` reason at WARNING on every retry.
- `miser.log` retention 5 MB x 12.
- `_apply_inverter_control` is serialised with a lock (optimiser run vs compliance callbacks).

Still to do: turn SolisConnect debug logging off (it floods the HA journal to ~4 h of
retention) - it is enabled at runtime, not in `configuration.yaml`.
