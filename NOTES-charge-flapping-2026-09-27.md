# Miser: force_power sign flips at slot boundaries

Observed 2026-09-24 → 2026-09-27 from the household morning-report work (separate session, Immich/Proxmox repo).
Nothing in this repo was changed or investigated — this is raw observation to pick up here.

## What was seen

During overnight grid charging, `sensor.miser_force_power` briefly **inverts sign** a few minutes before each
half-hour slot boundary, then returns to the charge value at the boundary. The inverter follows each flip, so the
battery is told to discharge at ~3 kW for a couple of minutes in the middle of a charge, several times a night.

The charge still completes (26 Sep: 33 % → 100 % by 05:18, 13.2 kWh in), so it is not currently harmful —
but it is unnecessary cycling and it makes the charge take longer than planned.

### The pattern (identical on all three nights)

| time | force_power |
|---|---|
| HH:28 / HH:58 | flips to **−3000 W** (discharge) |
| HH:01 / HH:31 | back to **+3000 W** (charge) |

So the flip lands ~2–3 minutes *before* the slot boundary and clears ~1 minute *after* it. That timing is the
main clue: it smells like the last optimiser pass inside a slot writing the *next* slot's value, or an
off-by-one on the plan index at the boundary.

### Counts (22:00–07:00, sign changes while |P| > 500 W)

| night | flips | median gap | miser_state counts |
|---|---|---|---|
| Thu 24 Sep | 18 | 16.9 min | Optimising 54, Charging 27, Discharging 21, Idle 18, **Control failed 1** |
| Fri 25 Sep | 17 | 27.0 min | Optimising 54, Charging 29, Idle 25, Discharging 11 |
| Sat 26 Sep | 11 | 23.8 min | Optimising 54, Charging 30, Idle 19, Discharging 15, **Control failed 3** |

Sample (26 Sep):

```
23:31:00  -3000 W -> +3000 W
00:54:55  +3000 W -> -3000 W
01:01:00  -3000 W -> +2994 W
01:54:53  +3000 W -> -1940 W
02:01:00  -1940 W -> +3000 W
02:24:49  +2987 W -> -3000 W
02:31:00  -3000 W -> +2989 W
```

`sensor.miser_state` also shows **Control failed** on some nights (3× on 26 Sep, 1× on 24 Sep) — may or may not
be related; it appeared during the charge window.

## Sign conventions (confirmed empirically, worth not re-deriving)

- `sensor.miser_force_power` — **positive = charge**, negative = discharge.
- `sensor.solis_s5_eh1p_battery_power_net` — **the opposite: positive = discharging**, negative = charging.
  Verified live: net `+558 W` while `battery_discharge_power` = 558 W and `battery_charge_power` = 0 W.
- `sensor.miser_next_slot_target_soc` is the target Miser is aiming at; max value seen during a charge window
  is what the morning report judges the result against.

## How to reproduce the data

From any host on the LAN with an HA long-lived token (the dashboard CT 101 `dropbox` / `dashboard` has one at
`/etc/morning-report/ha-token`, and a ready-made script at `/tmp/collect_flaps.py`):

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://192.168.4.23:8123/api/history/period/2026-09-26T22:00:00Z?filter_entity_id=sensor.miser_force_power&end_time=2026-09-27T07:00:00Z&minimal_response&no_attributes"
```

Useful entities: `sensor.miser_force_power`, `sensor.miser_force_current`, `sensor.miser_state`,
`sensor.miser_next_slot_start/_end/_power/_target_soc`, `sensor.miser_optimised_cost`,
`switch.miser_read_only`, `number.miser_optimiser_frequency` (10 min), `number.miser_alternation_cost_threshold`.

Miser's own log is on the HA box at `/config/miser.log` (rotated, ~5 MB each, 12 kept) — `ssh root@192.168.4.23`.
The window around `HH:28` and `HH:58` is where to look.

## Things that might be worth checking here

1. Slot-boundary handling in the optimiser write path — which slot's power is written on the pass that runs
   ~2 min before the boundary.
2. `alternation_cost_threshold` / `whole_horizon_beta` — whether alternation between charge and discharge is
   being priced as free and so chosen at the margin.
3. Whether `Control failed` correlates with the flips (SolisCloud write rejections).

## Context you may want

- Daily household report (07:00) includes a 24 h battery chart — SoC, Miser requested rate, actual battery rate,
  all charge-positive, −4…+4 kW. Archive of dated PDFs: <http://192.168.4.100:8088/reports/>
  (or `http://dashboard:8088/reports/` on the tailnet). Each night's chart shows this pattern as a picket fence.
- The report judges the charge purely against what Miser asked for, so if the flapping is fixed the verdict
  should stay "As Miser called for" but with a cleaner trace.
- Note this PC is logged into the `penrithmrt.org.uk` tailnet, not the home one — use the LAN IPs, or
  `tailscale switch`, if MagicDNS names do not resolve.

---

## Diagnosis & fix (2026-09-27, this repo)

**Root cause — two parts.**

1. *Economic driver:* export is a flat **15.00 p/kWh**, overnight import **8.63 p** (log: "30 slots
   have an export price greater than the min import price"). Export beats import by more than the
   round-trip loss, so discharge-then-recharge shows a net profit in the model — the optimiser
   genuinely wants to cycle overnight. `number.miser_whole_horizon_write_cost` was **0**, so the
   whole-horizon DP priced every charge↔discharge switch as free and inserted discharges for even
   a few minutes of 15p export.
2. *Mechanism that made it flap at every boundary:* `optimise()` models the current interval as a
   partial slot from *now* to the next half-hour boundary. The recurring optimiser schedule is
   **not clock-aligned** (it anchors to when the initial run finished — e.g. runs at :X4:30), so one
   run always lands a few minutes before each boundary. The DP front-loads a discharge into that
   short leading fragment; `_slots_to_apply` then writes both the discharge fragment and the charge
   window, and the boundary+60s compliance check flips it back. Hence the −3000 W ~2–3 min before
   each boundary, back to +3000 W ~1 min after. (Confirmed in the 26 Sep 00:57 run: applied plan was
   `discharging 23:57→00:00, charging 00:00→04:30`.) `Control failed` is a side-effect: a 3-minute
   discharge is already expiring by the 45 s read-back.

**Fixes applied.**
- `_drop_short_control_windows` in the physical control path drops any control window shorter than
  the optimiser re-decision interval (the leading boundary fragment). Reported plan/costs untouched;
  the inverter holds its current window through the fragment. Commit on `dev`, tests in
  `tests/test_optimiser_control_windows.py`.
- Set `number.miser_whole_horizon_write_cost` = **5p** so the DP won't insert a mode change for
  sub-5p arbitrage (set via core.restore_state; confirmed live: "Whole-horizon inverter write cost:
  5.0p per control change").

**Still to verify:** tonight's charge window (should show a clean trace, no picket fence). If export
15p > import genuinely warrants overnight *export arbitrage* as a deliberate strategy, that's a
separate design decision — the write cost only stops the marginal churn.
