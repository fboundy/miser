import copy
import logging
from math import isfinite

import pandas as pd
from numpy import isnan

from .const import (
    DEFAULT_BATTERY_VOLTAGE,
    EARLY_DISCHARGE_COST,
    LOAD_UNCERTAINTY,
    MODEL_MIN_SLOT_POWER,
)

_LOGGER = logging.getLogger(__name__)


def _is_finite(value) -> bool:
    try:
        return isfinite(float(value))
    except (TypeError, ValueError):
        return False


def get_dt_hours(df: pd.DataFrame | pd.Series) -> pd.Series:
    df = pd.DataFrame(df)
    df["dt_hours"] = -df.index.diff(-1) / pd.Timedelta("60min")
    return df["dt_hours"].ffill()


class InverterModel:
    """Describes the inverter

    Attributes:
        inverter_efficiency: A float describing the DC-AC efficiency of the inverter.
        charger_efficiency: A float describing the AC-DC efficiency of the inverter.
        inverter_power: An int describing the DC-AC power of the inverter.
        charger_power: An int describing the maximum battery-side (DC) charge power.
    """

    def __init__(
        self,
        inverter_efficiency: float = 0.97,
        charger_efficiency: float = 0.91,
        inverter_power: int = 3000,
        charger_power: int = 3500,
    ) -> None:
        self.inverter_efficiency = inverter_efficiency / 100
        self.charger_efficiency = charger_efficiency / 100
        self.inverter_power = inverter_power
        self.charger_power = charger_power


class BatteryModel:
    """Describes the battery system attached to the inverter

    Attributes:
        capacity: An integer describing the Wh capacity of the battery.
        max_dod: A float describing the maximum depth of discharge of the battery.
        current_limit_amps: An int describing the maximum amps at which the battery can charge/discharge.
        voltage: An int describing the voltage of the battery system.
    """

    def __init__(
        self,
        battery_capacity: int,
        battery_minimum_soc: int,
        battery_current_limit: int,
        voltage: int = DEFAULT_BATTERY_VOLTAGE,
    ) -> None:
        self.capacity = battery_capacity
        self.max_dod = battery_minimum_soc / 100
        self.current_limit_amps = battery_current_limit
        self.voltage = voltage

    @property
    def max_charge_power(self) -> int:
        """returns the maximum watts at which the battery can charge."""
        try:
            max_charge_power = self.current_limit_amps * self.voltage
        except:
            _LOGGER.warning(
                f"Unable to calculate max_charge_power from current limit {self.current_limit_amps} x voltage {self.voltage}"
            )
            max_charge_power = 100000
        return max_charge_power

    @property
    def max_discharge_power(self) -> int:
        """returns the maximum watts at which the battery can discharge."""
        return self.max_charge_power


class PVsystemModel:
    def __init__(
        self,
        inverter: InverterModel,
        battery: BatteryModel,
        tz: str = "UTC",
    ) -> None:
        self._inverter = inverter
        self._battery = battery
        self._tz = tz
        self._slots = []
        self.solar: pd.Series | None = None
        self.consumption: pd.Series | None = None
        self.prices: pd.DataFrame | None = None
        self.initial_soc = battery.max_dod
        self.export_limit: float | None = None  # W at the grid; None = no cap

    @property
    def tz(self):
        return self._tz

    @property
    def battery(self):
        return self._battery

    @property
    def inverter(self):
        return self._inverter

    # The charge/discharge limits are battery-side (DC) constraints, but forced
    # power throughout the model is grid-side (AC). These return the grid-side
    # forced power that drives the battery exactly to its limit, so the plan can
    # use the battery's full rate: e.g. a 3000 W battery charge limit at 91%
    # charger efficiency allows ~3297 W from the grid.

    @property
    def battery_charge_limit(self) -> float:
        """Maximum power into the battery (DC)."""
        return min(self.battery.max_charge_power, self.inverter.charger_power)

    @property
    def battery_discharge_limit(self) -> float:
        """Maximum power out of the battery (DC)."""
        # Not the inverter's AC rating: the RHI-3.6K-48ES-5G battery side is 3 kW / 62.5 A (datasheet).
        return self.battery.max_discharge_power

    @property
    def charge_power_limit(self) -> float:
        """Grid-side forced charge power that puts battery_charge_limit into the battery."""
        efficiency = self.inverter.charger_efficiency or 1
        return self.battery_charge_limit / efficiency

    @property
    def discharge_power_limit(self) -> float:
        """Grid-side forced discharge power at the battery limit, capped by the inverter's AC rating."""
        efficiency = self.inverter.inverter_efficiency or 1
        limit = min(self.battery_discharge_limit * efficiency, self.inverter.inverter_power)
        # Capping forced export at the grid limit is conservative: house load uses some of it first.
        return min(limit, self.export_limit) if self.export_limit else limit

    def _battery_dc(self, power: float, surplus: float) -> float:
        """AC-side battery power (+ out, - in) to the DC power at the battery.

        Charging takes the solar surplus first, DC to DC with no loss; only the rest comes through the
        charger. Before this, solar was charged at the charger's 91% and the plan capped the battery to
        export solar instead (2026-11-09 12:08-13:28).
        """
        if power >= 0:
            return power / self.inverter.inverter_efficiency
        solar = min(-power, surplus)
        return -(solar + (-power - solar) * self.inverter.charger_efficiency)

    def _battery_ac(self, power: float, surplus: float) -> float:
        """Inverse of _battery_dc: DC battery power to its AC-side equivalent."""
        if power >= 0:
            return power * self.inverter.inverter_efficiency
        solar = min(-power, surplus)
        return -(solar + (-power - solar) / self.inverter.charger_efficiency)

    def set_start(self, start: pd.Timestamp) -> bool:
        self._index = [start.floor("1min")] + list(self.consumption.index[1:])

    @property
    def index(self):
        try:
            return self._index
        except:
            return self.consumption.index

    async def forced(self, slots=None) -> pd.Series:
        slots = slots or []
        forced = pd.Series(index=self.index, data=0)

        for t, c in slots:
            if not isnan(c):
                forced.loc[t] += int(c)

        return forced

    async def flows(self, *args, **kwargs):
        df = pd.concat([self.solar, self.consumption, self.prices], axis=1)
        df.index = self.index
        for col in ["solar", "consumption", "import", "export"]:
            if col not in df.columns:
                df[col] = 0
            df[col] = pd.to_numeric(df[col], errors="coerce")

        missing_counts = df[["solar", "consumption", "import", "export"]].isna().sum()
        missing_counts = missing_counts[missing_counts > 0]
        if not missing_counts.empty:
            _LOGGER.warning(
                "Model input contained missing values; filling gaps before optimisation: %s",
                ", ".join(f"{col}={count}" for col, count in missing_counts.items()),
            )

        df["solar"] = df["solar"].fillna(0)
        df["consumption"] = df["consumption"].fillna(0)
        df["import"] = df["import"].ffill().bfill().fillna(0)
        df["export"] = df["export"].ffill().bfill().fillna(0)
        df["dt_hours"] = get_dt_hours(df)
        df["battery_grid_requirement"] = df["consumption"] - df["solar"]
        df["battery_temp"] = df["consumption"] - df["solar"]

        df["forced"] = await self.forced(*args, **kwargs)
        solar_surplus_discharge = (df["forced"] < 0) & (df["solar"] > df["consumption"])
        if solar_surplus_discharge.any():
            _LOGGER.debug(
                "Ignoring %d forced discharge slots while solar exceeds consumption",
                int(solar_surplus_discharge.sum()),
            )
            df.loc[solar_surplus_discharge, "forced"] = 0

        chg_mask = df["forced"] != 0
        df.loc[chg_mask, "battery_temp"] = -df.loc[chg_mask, "forced"]
        df["battery_temp"] = df["battery_temp"].clip(
            lower=-self.charge_power_limit,
            upper=self.discharge_power_limit,
        )

        chg = [self.initial_soc / 100 * self.battery.capacity]

        surplus = (df["solar"] - df["consumption"]).clip(lower=0).tolist()
        # Plain floats: a per-row .loc lookup made this loop most of a run's time.
        for flow, dt_hours, slot_surplus in zip(df["battery_temp"].tolist(), df["dt_hours"].tolist(), surplus):
            flow = max(self._battery_dc(flow, slot_surplus), -self.battery_charge_limit)

            chg.append(
                round(
                    max(
                        [
                            min(
                                [
                                    chg[-1] - flow * dt_hours,
                                    self.battery.capacity,
                                ]
                            ),
                            self.battery.max_dod * self.battery.capacity,
                        ]
                    ),
                    1,
                )
            )
            # _LOGGER.debug(f"{idx} {flow:8.1f}")

        df["chg"] = chg[:-1]
        df["chg"] = df["chg"].ffill()
        df["chg_end"] = chg[1:]
        df["chg_end"] = df["chg_end"].bfill()
        df["battery"] = pd.Series(chg).diff(-1)[:-1].to_list()
        df["battery"] /= df["dt_hours"]
        df["battery"] = [self._battery_ac(power, slot_surplus) for power, slot_surplus in zip(df["battery"], surplus)]
        df["grid"] = (df["battery_grid_requirement"] - df["battery"]).round(0)
        df["soc"] = (df["chg"] / self.battery.capacity) * 100
        df["soc_end"] = (df["chg_end"] / self.battery.capacity) * 100
        # _LOGGER.debug(f"Model flows\n{df.to_string()}")
        # Split by the direction of flow, not the sign of the cost: prices can be negative.
        df["import_cost"] = df["grid"].clip(lower=0) * df["dt_hours"] * df["import"] / 1000
        df["export_cost"] = df["grid"].clip(upper=0) * df["dt_hours"] * df["export"] / 1000
        df["net_cost"] = df["import_cost"] + df["export_cost"]
        return df

    async def net_cost(self, *args, **kwargs) -> pd.Series:
        """
        Returns a series of the net cost

        """
        use_export = kwargs.pop("use_export", True)
        flows = await self.flows(*args, **kwargs)
        if use_export:
            return flows["net_cost"]
        else:
            return flows["import_cost"]

    async def whole_horizon(self, soc_step_percent: float = 2.5, state_resolution_wh: int = 50) -> list:
        # Pure CPU-bound work; the optimiser runs this via _run_compute so it is
        # already off the event loop (see optimiser._run_compute). Only self is
        # read - no hass, no shared mutation - so it is safe in a worker thread.
        flows = await self.flows(slots=[])
        write_cost = getattr(self, "whole_horizon_write_cost", 0) or 0
        path = self._whole_horizon_compute(
            flows,
            soc_step_percent,
            state_resolution_wh,
            write_cost,
        )
        return await self._average_same_price_runs(flows, path)

    async def robust_whole_horizon(self) -> list:
        """Plan for the forecast load and for LOAD_UNCERTAINTY more and less, and keep the plan that is
        cheapest averaged over all three loads, writes included.

        Plans that tie at the forecast often differ when it is wrong: one that empties the battery
        early buys at the peak if the load is 10% higher (2026-11-05/06: an export straight after the
        night charge, and a charge stopped at 85%).
        """
        scenarios = [self] + [self._with_load(1 + change) for change in (LOAD_UNCERTAINTY, -LOAD_UNCERTAINTY)]
        plans: list[list] = []
        for scenario in scenarios:
            plan = await scenario.whole_horizon()
            if plan not in plans:
                plans.append(plan)
        if len(plans) == 1:
            return plans[0]

        scores = [await self._scenario_score(plan, scenarios) for plan in plans]
        _LOGGER.info("Whole-horizon load scenarios: plan scores %s", ", ".join(f"{s:.1f}p" for s in scores))
        return plans[scores.index(min(scores))]

    def _with_load(self, factor: float) -> "PVsystemModel":
        scenario = copy.copy(self)
        scenario.consumption = self.consumption * factor
        return scenario

    async def _scenario_score(self, plan: list, scenarios: list) -> float:
        total = 0.0
        for scenario in scenarios:
            flows = await scenario.flows(slots=plan)
            cost = (await scenario.net_cost(slots=plan)).sum()
            total += cost + scenario.terminal_soc_value(flows["soc_end"].iloc[-1] / 100 * self.battery.capacity)
        writes = self._plan_writes(await self.flows(slots=[]), plan)
        return total / len(scenarios) + writes * (getattr(self, "whole_horizon_write_cost", 0) or 0)

    def _plan_writes(self, flows: pd.DataFrame, plan: list) -> int:
        """Writes the plan needs, by the same rules Whole Horizon prices them."""
        forced = dict(plan)
        held_end = getattr(self, "whole_horizon_initial_end", None)
        direction = getattr(self, "whole_horizon_initial_control", "idle")
        control = self._whole_horizon_control({"charge": 1000.0, "discharge": -1000.0}.get(direction), flows.iloc[0])
        if control != "idle":
            control = ("held", control)
        writes = 0
        for start, row in flows.iterrows():
            control, write = self._whole_horizon_write(
                control, self._whole_horizon_control(forced.get(start), row), start, held_end
            )
            writes += write
        return writes

    def _whole_horizon_compute(
        self,
        flows: pd.DataFrame,
        soc_step_percent: float,
        state_resolution_wh: int,
        write_cost: float,
    ) -> list:
        required_cols = ["dt_hours", "consumption", "solar", "import", "export"]
        if flows.empty:
            return []
        invalid = flows[required_cols].isna().any(axis=1)
        if invalid.any():
            _LOGGER.warning("Whole-horizon skipped because model inputs still contain missing values")
            return []

        min_energy = self.battery.max_dod * self.battery.capacity
        max_energy = self.battery.capacity
        step_wh = self.battery.capacity * soc_step_percent / 100
        initial_energy = self.initial_soc / 100 * self.battery.capacity

        if not all(_is_finite(v) for v in [min_energy, max_energy, step_wh, initial_energy]) or step_wh <= 0:
            _LOGGER.warning("Whole-horizon skipped because battery state inputs are invalid")
            return []

        energy_levels = sorted(
            {
                round(min_energy + i * step_wh, 1)
                for i in range(int((max_energy - min_energy) / step_wh) + 1)
            }
            | {round(initial_energy, 1), round(max_energy, 1)}
        )
        initial_energy = round(initial_energy, 1)

        # A state is (energy, control in force). A write is a change to the inverter's windows: starting
        # a window or changing its direction or price costs write_cost, and a window ending costs nothing.
        # The window the inverter already holds is ("held", control): carrying it on is free, and ending
        # it costs a write unless it ends at its programmed time.
        direction = getattr(self, "whole_horizon_initial_control", "idle")
        held_end = getattr(self, "whole_horizon_initial_end", None)
        initial_control = self._whole_horizon_control({"charge": 1000.0, "discharge": -1000.0}.get(direction), flows.iloc[0])
        if initial_control != "idle":
            initial_control = ("held", initial_control)
        initial_key = (self._whole_horizon_state_key(initial_energy, state_resolution_wh), initial_control)
        costs: dict[tuple[float, object], float] = {initial_key: 0.0}
        energies: dict[tuple[float, object], float] = {initial_key: initial_energy}
        paths: dict[tuple[float, object], list[tuple[pd.Timestamp, float]]] = {initial_key: []}

        _LOGGER.info("")
        _LOGGER.info("Whole Horizon Optimisation (Beta)")
        _LOGGER.info("---------------------------------")
        if not _is_finite(write_cost):
            write_cost = 0
        write_cost = float(write_cost)
        if write_cost:
            _LOGGER.info("Whole-horizon inverter write cost: %.1fp per control change", write_cost)

        # Hours from each slot's start to the end of the horizon, for the early-discharge tie-break.
        hours_left = flows["dt_hours"][::-1].cumsum()[::-1]

        for start, row in flows.iterrows():
            next_costs: dict[tuple[float, str], float] = {}
            next_energies: dict[tuple[float, str], float] = {}
            next_paths: dict[tuple[float, str], list[tuple[pd.Timestamp, float]]] = {}
            dt_hours = float(row["dt_hours"])
            requirement = float(row["consumption"] - row["solar"])
            if not all(_is_finite(v) for v in [dt_hours, requirement, row["import"], row["export"]]):
                _LOGGER.warning("Whole-horizon skipped invalid interval at %s", start)
                continue

            for state_key, cost in costs.items():
                previous_control = state_key[1]
                energy = energies[state_key]
                for next_energy, forced_power, grid in self._whole_horizon_actions(
                    energy=energy,
                    energy_levels=energy_levels,
                    requirement=requirement,
                    dt_hours=dt_hours,
                ):
                    slot_cost = (
                        max(grid, 0) * dt_hours * float(row["import"])
                        + min(grid, 0) * dt_hours * float(row["export"])
                    ) / 1000
                    if forced_power is not None and forced_power < 0:
                        # Same value later is safer: the battery keeps its energy if the forecast is wrong.
                        slot_cost += EARLY_DISCHARGE_COST * -forced_power * dt_hours / 1000 * hours_left[start]
                    control, write = self._whole_horizon_write(
                        previous_control, self._whole_horizon_control(forced_power, row), start, held_end
                    )
                    candidate_cost = cost + slot_cost + (write_cost if write else 0.0)
                    if not all(_is_finite(v) for v in [next_energy, grid, candidate_cost]):
                        continue
                    next_key = (
                        self._whole_horizon_state_key(next_energy, state_resolution_wh),
                        control,
                    )
                    if candidate_cost >= next_costs.get(next_key, float("inf")):
                        continue

                    next_costs[next_key] = candidate_cost
                    next_energies[next_key] = next_energy
                    path = list(paths[state_key])
                    if forced_power is not None and abs(forced_power) >= MODEL_MIN_SLOT_POWER:
                        path.append((start, round(forced_power, 0)))
                    next_paths[next_key] = path

            # Prune: a state that costs at least write_cost more than the cheapest state at the same
            # energy can never win, because that cheapest state can switch to its control for write_cost.
            best_at_energy: dict[float, float] = {}
            for (energy_key, _control), cost in next_costs.items():
                best_at_energy[energy_key] = min(cost, best_at_energy.get(energy_key, float("inf")))
            keep = [
                key for key, cost in next_costs.items()
                if cost == best_at_energy[key[0]] or cost < best_at_energy[key[0]] + write_cost
            ]
            costs = {key: next_costs[key] for key in keep}
            energies = {key: next_energies[key] for key in keep}
            paths = {key: next_paths[key] for key in keep}

            if not costs:
                _LOGGER.warning("Whole-horizon found no valid states at %s", start)
                return []

        best_key = min(
            costs,
            key=lambda state_key: costs[state_key] + self.terminal_soc_value(energies[state_key]),
        )
        best_terminal = energies[best_key]
        _LOGGER.info(
            "Whole-horizon cost estimate: %6.1fp raw, %6.1fp adjusted, terminal SOC: %5.1f%%",
            costs[best_key],
            costs[best_key] + self.terminal_soc_value(best_terminal),
            best_terminal / self.battery.capacity * 100,
        )
        return paths[best_key]

    def _whole_horizon_state_key(self, energy: float, state_resolution_wh: int) -> float:
        return round(energy / state_resolution_wh) * state_resolution_wh

    @staticmethod
    def _whole_horizon_write(previous: object, control: object, start: pd.Timestamp, held_end) -> tuple[object, bool]:
        """The next state's control, and whether getting there writes to the inverter."""
        if isinstance(previous, tuple) and previous[0] == "held":
            ended = held_end is not None and start >= held_end
            if control == previous[1] and not ended:
                return previous, False
            if control == "idle":
                return control, not ended and held_end != start
            return control, True  # a new window, or this one changed or extended past its end
        if control == "idle" or control == previous:
            return control, False
        return control, True

    def _whole_horizon_control(self, forced_power: float | None, row=None) -> object:
        """What the inverter is told to do: "idle", or a charge or discharge at the slot's price.

        _average_same_price_runs gives each same-price charge or export run one power, so a power
        change inside a run is never written. Pricing it made a late export cost an extra write: after
        a day's drain the battery is off the SOC steps and only full power keeps one power (2026-11-05).
        """
        if forced_power is None or abs(forced_power) < MODEL_MIN_SLOT_POWER:
            return "idle"
        if forced_power > 0:
            return ("charge", None if row is None else float(row["import"]))
        return ("discharge", None if row is None else float(row["export"]))

    async def _average_same_price_runs(self, flows: pd.DataFrame, path: list) -> list:
        """Spread each charge over its whole same-price period, idle slots included, and each run of
        adjacent same-price exports over that run, at one constant power.

        Same energy at the same price costs the same, and one power needs one inverter write. Whole
        Horizon charges only at full power, so without the idle slots a flat cheap period kept gaps
        that moved each run (2026-11-03: 00:06-01:06, idle, 02:00-06:06).
        """
        if not path:
            return path
        forced = dict(path)
        runs: list[list[tuple[pd.Timestamp, float]]] = []
        for start in flows.index:
            power = forced.get(start, 0.0)
            if runs:
                prev_start, prev_power = runs[-1][-1]
                charge = power >= 0
                price_col = "import" if charge else "export"
                if (prev_power >= 0) == charge and flows.at[start, price_col] == flows.at[prev_start, price_col]:
                    runs[-1].append((start, power))
                    continue
            runs.append([(start, power)])

        # An idle slot that becomes a charge serves the house from the grid, not the battery, so take
        # that battery energy off the charge; otherwise the averaged plan ends fuller and buys more.
        path_flows = await self.flows(slots=path)
        drained = ((path_flows["soc"] - path_flows["soc_end"]).clip(lower=0) * self.battery.capacity / 100)
        averaged = []
        for run in runs:
            if not any(power for _start, power in run):
                continue
            hours = sum(flows.at[start, "dt_hours"] for start, _ in run)
            if run[0][1] >= 0:
                # Count only the charge the battery takes: a slot clipped at full (an export dropped from the
                # path but not the charge that refilled it) would otherwise fill the battery before the run ends.
                energy = sum(
                    min(p, max(-path_flows.at[start, "battery"], 0.0)) * flows.at[start, "dt_hours"] for start, p in run
                )
                idle_drain = sum(drained[start] for start, p in run if p == 0) / self.inverter.charger_efficiency
                energy = max(energy - idle_drain, 0)
            else:
                energy = sum(p * flows.at[start, "dt_hours"] for start, p in run)
            power = round(energy / hours, 0)
            if run[0][1] >= 0 and energy <= 0:
                power = MODEL_MIN_SLOT_POWER  # a run that only holds a full battery stays a charge window
            averaged.extend((start, power) for start, _ in run)

        # Solar or load can still make a slot cost differ; keep the original if averaging costs more.
        if (await self.net_cost(slots=averaged)).sum() > (await self.net_cost(slots=path)).sum() + 0.1:
            return path
        return averaged

    def _whole_horizon_actions(
        self,
        energy: float,
        energy_levels: list[float],
        requirement: float,
        dt_hours: float,
    ) -> list[tuple[float, float | None, float]]:
        actions: list[tuple[float, float | None, float]] = []

        natural_energy, natural_grid = self._whole_horizon_transition(
            energy=energy,
            requirement=requirement,
            forced_power=None,
            dt_hours=dt_hours,
        )
        actions.append((natural_energy, None, natural_grid))

        min_energy = self.battery.max_dod * self.battery.capacity
        max_energy = self.battery.capacity

        for forced_power in (
            self.charge_power_limit,
            -self.discharge_power_limit,
        ):
            if forced_power < 0 and requirement < 0:
                continue

            # Clip to the energy the battery can actually take or give this slot.
            # Without this a full battery is still offered a full-power charge:
            # _whole_horizon_transition clamps the energy, battery_power comes out as
            # zero and the command costs the same as idling, so the plan emits forced
            # charging at 100% SOC.
            if forced_power > 0:
                forced_power = min(
                    forced_power,
                    -self._battery_ac(-(max_energy - energy) / dt_hours, max(-requirement, 0)),
                )
            else:
                forced_power = max(
                    forced_power,
                    (min_energy - energy) * self.inverter.inverter_efficiency / dt_hours,
                )

            if abs(forced_power) < MODEL_MIN_SLOT_POWER:
                if forced_power < 0 or requirement <= 0:
                    continue
                # Hold: a charge window at a full battery keeps it full and the grid supplies the house.
                # Idle would let the house drain it at cheap-rate end (2026-11-07: 0.8 kWh before 07:00;
                # tested 2026-11-08: a Solis charge window at 100% held with the grid taking the load).
                forced_power = float(MODEL_MIN_SLOT_POWER)

            actual_energy, grid = self._whole_horizon_transition(
                energy=energy,
                requirement=requirement,
                forced_power=forced_power,
                dt_hours=dt_hours,
            )
            actions.append((actual_energy, forced_power, grid))

        for next_energy in energy_levels:
            delta = next_energy - energy
            if abs(delta) < 1:
                continue

            if delta > 0:
                forced_power = -self._battery_ac(-delta / dt_hours, max(-requirement, 0))
                max_power = self.charge_power_limit
            else:
                if requirement < 0:
                    continue

                forced_power = delta * self.inverter.inverter_efficiency / dt_hours
                max_power = self.discharge_power_limit

            if abs(forced_power) <= max_power:
                actual_energy, grid = self._whole_horizon_transition(
                    energy=energy,
                    requirement=requirement,
                    forced_power=forced_power,
                    dt_hours=dt_hours,
                )
                actions.append((actual_energy, forced_power, grid))

        return actions

    def _whole_horizon_transition(
        self,
        energy: float,
        requirement: float,
        forced_power: float | None,
        dt_hours: float,
    ) -> tuple[float, float]:
        min_energy = self.battery.max_dod * self.battery.capacity
        max_energy = self.battery.capacity

        surplus = max(-requirement, 0)
        battery_temp = requirement if forced_power is None else -forced_power
        battery_flow = self._battery_dc(battery_temp, surplus)

        next_energy = round(min(max(energy - battery_flow * dt_hours, min_energy), max_energy), 1)
        battery_power = self._battery_ac((energy - next_energy) / dt_hours, surplus)

        grid = round(requirement - battery_power, 0)
        return next_energy, grid

    def terminal_soc_value(self, terminal_energy: float) -> float:
        """Signed cost adjustment for the battery's end-of-horizon energy.

        Anchored to the run-start SoC. Ending below it is penalised by the grid
        cost of buying the shortfall back at the cheapest forward-window prices
        (its replacement cost). Ending above it is symmetrically credited with
        the replacement cost of that surplus - the cheapest a rational operator
        could rebuy it.

        Positive = penalty (added to cost); negative = credit (reduces cost).
        Both directions price |terminal - target| with the same cheapest-forward
        fill (`_terminal_fill_cost`), so a Wh is valued identically whichever
        side of the target it lands on.
        """
        valuation_prices = getattr(self, "valuation_prices", None)
        if valuation_prices is None or valuation_prices.empty or not _is_finite(terminal_energy):
            return 0.0

        target_energy = self.initial_soc / 100 * self.battery.capacity
        if not _is_finite(target_energy):
            return 0.0

        delta_wh = terminal_energy - target_energy
        if abs(delta_wh) < 1:
            return 0.0

        if delta_wh < 0:
            return self._terminal_fill_cost(-delta_wh)

        return -self._terminal_fill_cost(delta_wh)

    def _terminal_fill_cost(self, energy_wh: float) -> float:
        """Grid cost of importing `energy_wh` into the battery at the cheapest
        forward-window prices - the replacement cost of that much energy."""
        valuation_prices = getattr(self, "valuation_prices", None)
        if valuation_prices is None or valuation_prices.empty or energy_wh <= 0:
            return 0.0

        charge_power = self.charge_power_limit
        remaining_wh = energy_wh
        cost = 0.0
        prices = valuation_prices.copy()
        if "dt_hours" not in prices.columns:
            prices["dt_hours"] = self._price_dt_hours(prices)

        for _start, row in prices.sort_values("import").iterrows():
            if remaining_wh <= 0:
                break

            dt_hours = float(row["dt_hours"])
            if not _is_finite(dt_hours) or not _is_finite(row["import"]):
                continue
            battery_wh = min(remaining_wh, charge_power * dt_hours * self.inverter.charger_efficiency)
            grid_kwh = battery_wh / self.inverter.charger_efficiency / 1000
            cost += grid_kwh * float(row["import"])
            remaining_wh -= battery_wh

        if remaining_wh > 0:
            fallback_price = float(prices["import"].max())
            if _is_finite(fallback_price):
                cost += remaining_wh / self.inverter.charger_efficiency / 1000 * fallback_price

        return cost

    def _price_dt_hours(self, prices: pd.DataFrame) -> pd.Series:
        if len(prices.index) < 2:
            return pd.Series(index=prices.index, data=0.5)

        dt_hours = -prices.index.to_series().diff(-1) / pd.Timedelta("60min")
        return dt_hours.ffill().fillna(0.5)

