import logging
from math import isfinite

import pandas as pd
from numpy import isnan

from .const import (
    TIME_FORMAT,
    OPTIMISER_HIGH_COST_MAX_ITERS,
    CONTROL_PASS_THREHOLD,
    CONTROL_SLOT_THRESHOLD,
    DEFAULT_BATTERY_VOLTAGE,
    MODEL_MIN_SLOT_POWER,
    MODEL_MAX_SEARCH_WINDOW_SOC,
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
        return min(self.battery_discharge_limit * efficiency, self.inverter.inverter_power)

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

        # Plain floats: a per-row .loc lookup made this loop most of a run's time.
        for flow, dt_hours in zip(df["battery_temp"].tolist(), df["dt_hours"].tolist()):
            if flow > 0:
                flow = flow / self.inverter.inverter_efficiency
            else:
                flow = flow * self.inverter.charger_efficiency

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
        df.loc[df["battery"] > 0, "battery"] = df["battery"] * self.inverter.inverter_efficiency
        df.loc[df["battery"] < 0, "battery"] = df["battery"] / self.inverter.charger_efficiency
        df["grid"] = (df["battery_grid_requirement"] - df["battery"]).round(0)
        df["soc"] = (df["chg"] / self.battery.capacity) * 100
        df["soc_end"] = (df["chg_end"] / self.battery.capacity) * 100
        # _LOGGER.debug(f"Model flows\n{df.to_string()}")
        df["import_cost"] = ((df["grid"] * df["dt_hours"] * df["import"]).clip(lower=0)) / 1000
        df["export_cost"] = ((df["grid"] * df["dt_hours"] * df["export"]).clip(upper=0)) / 1000
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

    async def high_cost_swaps(self) -> list:
        # --------------------------------------------------------------------------------------------
        #  Charging 1st Pass
        # --------------------------------------------------------------------------------------------
        _LOGGER.info("")
        _LOGGER.info("High Cost Usage Swaps")
        _LOGGER.info("---------------------")
        _LOGGER.info("")

        done = False
        i = 0
        slots = []
        forced = await self.forced(slots=[])
        df = pd.DataFrame(forced)
        df["available"] = True
        df["tested"] = False
        slot_count = [0]
        best_cost = self.base_cost
        self.net_costs = []

        while not done:
            i += 1
            if (i > OPTIMISER_HIGH_COST_MAX_ITERS) or (df["available"].sum() == 0):
                done = True

            flows = await self.flows(slots=slots)
            # This needs looking at!
            high_cost_flows = flows[~df["tested"]]
            high_cost_flows = high_cost_flows[high_cost_flows["forced"] == 0]

            if len(high_cost_flows) > 0:
                max_import_cost = high_cost_flows["import_cost"].max()
                max_cost_flows = high_cost_flows[high_cost_flows["import_cost"] == max_import_cost]
                if len(max_cost_flows) > 0:
                    max_slot = max_cost_flows.index[0]
                    max_slot_energy = round(
                        max_cost_flows["grid"].loc[max_slot] / 1000 * max_cost_flows["dt_hours"].loc[max_slot], 2
                    )  # kWh

                    str_log = (
                        f"{i:3d} {df['available'].sum():3d} {max_slot.tz_convert(self._tz).strftime(TIME_FORMAT)}:"
                    )

                    if max_slot_energy > 0:
                        round_trip_energy_required = (
                            max_slot_energy / self.inverter.charger_efficiency / self.inverter.inverter_efficiency
                        )

                        search_window = flows.loc[df["available"]].loc[: max_slot - pd.Timedelta("30min")].copy()
                        if len(search_window) > 0:
                            str_log += f"searching: {search_window.index[0].strftime(TIME_FORMAT)}-{search_window.index[-1].strftime(TIME_FORMAT)}"
                            search_window["countback"] = (
                                search_window["soc_end"] >= MODEL_MAX_SEARCH_WINDOW_SOC
                            ).sum() - (search_window["soc_end"] >= 97).cumsum()
                            search_window = search_window[search_window["countback"] == 0]
                            search_window = search_window[search_window["forced"] < (self.charge_power_limit)]
                            search_window = search_window[search_window["soc_end"] <= MODEL_MAX_SEARCH_WINDOW_SOC]
                            # The filters above can leave nothing, e.g. a battery
                            # already full before an expensive evening slot.
                            if search_window.empty:
                                _LOGGER.info(str_log + " No slot with charge headroom")
                                df.loc[max_slot, "tested"] = True
                                continue

                            str_log += f" {round_trip_energy_required:5.2f} kWh at {max_import_cost:6.2f}p."

                            min_price = search_window["import"].min()
                            charge_window = search_window[search_window["import"] == min_price].index
                            start_window = charge_window[0]
                            end_window = charge_window[-1]

                            cost_at_min_price = round_trip_energy_required * min_price
                            str_log += f"<==> {start_window.tz_convert(self._tz).strftime(TIME_FORMAT)}: {min_price:5.2f}p/kWh {cost_at_min_price:5.2f}p "
                            str_log += f" SOC: {search_window.loc[start_window]['soc']:5.1f}%->{search_window.loc[end_window]['soc_end']:5.1f}% "

                            factors = [1 / len(charge_window)] * len(charge_window)

                            if round(cost_at_min_price, 1) < round(max_import_cost, 1):
                                slots_added = 0
                                for slot, factor in zip(charge_window, factors):
                                    slot_power_required = max(
                                        round_trip_energy_required * 1000 / flows["dt_hours"].loc[slot] * factor, 0
                                    )
                                    slot_charger_power_available = max(
                                        self.charge_power_limit
                                        - search_window["forced"].loc[slot]
                                        - search_window["solar"].loc[slot],
                                        0,
                                    )
                                    slot_available_capacity = max(
                                        ((100 - search_window["soc_end"].loc[slot]) / 100 * self.battery.capacity)
                                        * 2
                                        * factor,
                                        0,
                                    )

                                    min_power = min(
                                        slot_power_required,
                                        slot_charger_power_available,
                                        slot_available_capacity,
                                    )
                                    remaining_slot_capacity = slot_charger_power_available - min_power

                                    if remaining_slot_capacity < MODEL_MIN_SLOT_POWER:
                                        df.loc[slot, "available"] = False

                                    slots.append(
                                        (
                                            slot,
                                            round(min_power, 0),
                                        )
                                    )
                                    slots_added += 1

                                net_cost = await self.net_cost(slots=slots)
                                self.net_costs.append(net_cost.sum())
                                flows = await self.flows(slots=slots)
                                slot_count.append(len(factors))

                                str_log += f"New SOC: {flows.loc[start_window]['soc']:5.1f}%->{flows.loc[end_window]['soc_end']:5.1f}% "
                                best_cost = self.net_costs[-1]
                                str_log += f"Net: {best_cost:6.1f}"

                                _LOGGER.info(str_log)

                            else:
                                _LOGGER.info(str_log + "No cheaper slots")
                                df.loc[max_slot, "tested"] = True
                        else:
                            _LOGGER.info(str_log + "No search window")
                            df.loc[max_slot, "tested"] = True
                else:
                    done = True
            else:
                _LOGGER.info("No slots available")
                done = True

        net_cost = await self.net_cost(slots=slots)
        self.best_cost = net_cost.sum()

        if self.base_cost - best_cost <= CONTROL_PASS_THREHOLD:
            _LOGGER.info(
                f"Charge net cost delta:  {self.base_cost - best_cost:0.1f}p: < Pass Threshold ({CONTROL_PASS_THREHOLD:0.1f}p) => Slots Excluded"
            )
            slots = []
            self.best_cost = self.base_cost

        return slots

    async def low_cost_charging(self, base_slots=None):
        best_cost = self.best_cost
        slots_added = 0

        base_slots = list(base_slots or [])
        slots = list(base_slots)

        flows = await self.flows(slots=slots)

        # Check how many slots which aren't full are at an import price less than any export price:
        max_export_price = flows[flows["forced"] <= 0]["export"].max()
        _LOGGER.info("")
        _LOGGER.info("Low Cost Charging")
        _LOGGER.info("------------------")
        _LOGGER.info("")

        _LOGGER.info(f"Max export price when there is no forced charge: {max_export_price:0.2f}p/kWh.")

        i = 0
        available = (
            (flows["import"] < max_export_price)  # import price < max export price
            & (flows["forced"] < self.charge_power_limit)  # forced charge capacity available
            & (flows["forced"] >= 0)  # not forced discharging
        )

        a0 = available.sum()
        _LOGGER.info(f"{a0} slots have an import price less than the max export price")
        done = a0 == 0

        while not done:
            low_cost_import_slots = flows.loc[
                available
                & (flows["import"] < max_export_price)
                & (flows["forced"] < self.charge_power_limit)
                & (flows["forced"] >= 0)
            ]
            i += 1
            done = i > a0

            min_price = low_cost_import_slots["import"].min()

            if len(low_cost_import_slots[low_cost_import_slots["import"] == min_price]) > 0:
                start_window = low_cost_import_slots[low_cost_import_slots["import"] == min_price].index[0]
                available.loc[start_window] = False
                str_log = ""
                str_log = f"{available.sum():>2d} Min import price {min_price:5.2f}p/kWh at {start_window.strftime(TIME_FORMAT)} {flows.loc[start_window]['forced']:4.0f}W "

                str_log += "  "
                factor = 1

                str_log += f"SOC: {flows.loc[start_window]['soc']:5.1f}%->{flows.loc[start_window]['soc_end']:5.1f}% "

                forced_charge = min(
                    self.charge_power_limit
                    - flows["forced"].loc[start_window]
                    - flows["solar"].loc[start_window],
                    ((100 - flows["soc_end"].loc[start_window]) / 100 * self.battery.capacity) * 2 * factor,
                )

                slot = (
                    start_window,
                    forced_charge,
                )

                slots.append(slot)

                flows = await self.flows(slots=slots)

                net_cost = (await self.net_cost(slots=slots)).sum()

                str_log += f"Net: {net_cost:5.1f} "
                if _is_finite(net_cost) and _is_finite(best_cost) and net_cost < best_cost - CONTROL_SLOT_THRESHOLD:
                    str_log += f"New SOC: {flows.loc[start_window]['soc']:5.1f}%->{flows.loc[start_window]['soc_end']:5.1f}% "
                    str_log += f"Max export: {-flows['grid'].min():0.0f}W "
                    best_cost = net_cost
                    slots_added += 1

                else:
                    slots = slots[:-1]
                    flows = await self.flows(slots=slots)

                done = available.sum() == 0
            else:
                done = True

        cost_delta = best_cost - self.best_cost
        str_log = f"Charge net cost delta:{(-cost_delta):5.1f}p"

        if cost_delta > -CONTROL_PASS_THREHOLD:
            slots_added = 0
            slots = list(base_slots)
            str_log += f": < Pass Threshold {CONTROL_PASS_THREHOLD:0.1f}p => Slots Excluded"

        else:
            str_log += f": > Pass Threshold {CONTROL_PASS_THREHOLD:0.1f}p => Slots Included"
            self.best_cost = best_cost

            _LOGGER.info("")
            _LOGGER.info(str_log)

        return slots

    async def fill_first_charging(self, base_slots=None):
        base_slots = list(base_slots or [])
        slots = list(base_slots)
        slots_added = 0

        flows = await self.flows(slots=slots)

        max_export_price = flows[flows["forced"] <= 0]["export"].max()
        _LOGGER.info("")
        _LOGGER.info("Fill First Charging")
        _LOGGER.info("-------------------")
        _LOGGER.info("")
        _LOGGER.info(f"Max export price when there is no forced charge: {max_export_price:0.2f}p/kWh.")

        available = (
            (flows["import"] < max_export_price)
            & (flows["forced"] < self.charge_power_limit)
            & (flows["forced"] >= 0)
        )

        _LOGGER.info(f"{available.sum()} slots have an import price less than the max export price")

        while available.sum() > 0:
            fill_slots = flows.loc[
                available
                & (flows["import"] < max_export_price)
                & (flows["forced"] < self.charge_power_limit)
                & (flows["forced"] >= 0)
            ]
            if fill_slots.empty:
                break

            min_price = fill_slots["import"].min()
            start_window = fill_slots[fill_slots["import"] == min_price].index[0]
            available.loc[start_window] = False

            str_log = (
                f"{available.sum():>2d} Min import price {min_price:5.2f}p/kWh at "
                f"{start_window.strftime(TIME_FORMAT)} {flows.loc[start_window]['forced']:4.0f}W   "
                f"SOC: {flows.loc[start_window]['soc']:5.1f}%->{flows.loc[start_window]['soc_end']:5.1f}% "
            )

            dt_hours = flows["dt_hours"].loc[start_window]
            charger_capacity = (
                self.charge_power_limit
                - flows["forced"].loc[start_window]
                - flows["solar"].loc[start_window]
            )
            battery_capacity = (
                (100 - flows["soc_end"].loc[start_window])
                / 100
                * self.battery.capacity
                / dt_hours
            )
            forced_charge = max(min(charger_capacity, battery_capacity), 0)

            if _is_finite(forced_charge) and forced_charge > MODEL_MIN_SLOT_POWER:
                slots.append((start_window, forced_charge))
                slots_added += 1
                flows = await self.flows(slots=slots)
                net_cost = (await self.net_cost(slots=slots)).sum()
                str_log += (
                    f"New SOC: {flows.loc[start_window]['soc']:5.1f}%"
                    f"->{flows.loc[start_window]['soc_end']:5.1f}% Net: {net_cost:5.1f}"
                )
            else:
                str_log += "No charge capacity"

            _LOGGER.info(str_log)

        net_cost = (await self.net_cost(slots=slots)).sum()
        self.best_cost = net_cost

        _LOGGER.info("")
        _LOGGER.info(f"Added {slots_added} fill-first charging slots. Best cost with charging = {net_cost:6.1f}p")

        return slots

    async def discharging(self, base_slots=None):
        best_cost = self.best_cost
        slots_added = 0

        base_slots = list(base_slots or [])
        slots = list(base_slots)

        flows = await self.flows(slots=slots)

        # Check how many slots which aren't full are at an export price less than any import price:
        min_import_price = flows[flows["forced"] >= 0]["import"].min()
        _LOGGER.info("")
        _LOGGER.info("Forced Discharging")
        _LOGGER.info("------------------")
        _LOGGER.info("")

        _LOGGER.info(f"Min import price when there is no forced discharge: {min_import_price:0.2f}p/kWh.")

        i = 0
        available = (
            (flows["export"] > min_import_price)
            & (-flows["forced"] < self.discharge_power_limit)
            & (flows["solar"] <= flows["consumption"])
        )

        a0 = available.sum()
        _LOGGER.info(f"{available.sum()} slots have an export price greater than the min import price")
        done = a0 == 0

        while not done:
            potential_discharge_slots = flows.loc[available]
            i += 1
            done = i > a0
            max_export_price = potential_discharge_slots["export"].max()

            if len(potential_discharge_slots[potential_discharge_slots["export"] == max_export_price]) > 0:
                start_window = potential_discharge_slots[
                    potential_discharge_slots["export"] == max_export_price
                ].index[0]
                available.loc[start_window] = False
                str_log = f"{available.sum():>2d} Max export price {max_export_price:5.2f}p/kWh at {start_window.strftime(TIME_FORMAT)} "
                str_log += "  "

                factor = 1
                str_log += f"SOC: {flows.loc[start_window]['soc']:5.1f}%->{flows.loc[start_window]['soc_end']:5.1f}% "

                kept_slots = list(slots)  # slots.append below must not change the copy we revert to
                if flows.loc[start_window, "forced"] > 0:
                    # An earlier pass planned a charge here; try exporting instead.
                    slots = [s for s in slots if s[0] != start_window]
                    flows = await self.flows(slots=slots)

                forced_discharge = min(
                    self.discharge_power_limit
                    + flows["forced"].loc[start_window]
                    - flows["solar"].loc[start_window],
                    (
                        (flows["soc_end"].loc[start_window] - self.battery.max_dod * 100)
                        / 100
                        * self.battery.capacity
                    )
                    * 2
                    * factor,
                )
                forced_discharge = max(forced_discharge, 0)

                # Not enough stored energy for a full-power export: try filling from cheaper earlier slots.
                power_room = self.discharge_power_limit + flows["forced"].loc[start_window] - flows["solar"].loc[start_window]
                if power_room - forced_discharge >= MODEL_MIN_SLOT_POWER:
                    slots_before = len(slots)
                    slots = await self._precharge_before(
                        slots,
                        start_window,
                        self.battery.max_dod * self.battery.capacity + power_room * flows["dt_hours"].loc[start_window],
                        flows["export"].loc[start_window],
                    )
                    flows = await self.flows(slots=slots)
                    forced_discharge = max(
                        min(
                            power_room,
                            (flows["soc_end"].loc[start_window] - self.battery.max_dod * 100) / 100 * self.battery.capacity * 2 * factor,
                        ),
                        0,
                    )
                    if len(slots) > slots_before:
                        str_log += f"Pre-charged to {flows.loc[start_window]['soc_end']:5.1f}% "

                slot = (
                    start_window,
                    -forced_discharge,
                )

                slots.append(slot)

                flows = await self.flows(slots=slots)

                net_cost = (await self.net_cost(slots=slots)).sum()

                str_log += f"Net: {net_cost:5.1f} "
                if _is_finite(net_cost) and _is_finite(best_cost) and net_cost < best_cost - CONTROL_SLOT_THRESHOLD:
                    str_log += f"New SOC: {flows.loc[start_window]['soc']:5.1f}%->{flows.loc[start_window]['soc_end']:5.1f}% "
                    str_log += f"Max export: {-flows['grid'].min():0.0f}W "
                    best_cost = net_cost
                    slots_added += 1
                else:
                    slots = kept_slots
                    flows = await self.flows(slots=slots)

                _LOGGER.info(str_log)
                done = available.sum() == 0
            else:
                done = True

        paired_slots, paired_cost, paired_added = await self._paired_discharge_refill(
            slots=slots,
            best_cost=best_cost,
        )
        if paired_added:
            slots = paired_slots
            best_cost = paired_cost
            slots_added += paired_added

        cost_delta = best_cost - self.best_cost
        str_log = f"Discharge net cost delta:{(-cost_delta):5.1f}p"
        if cost_delta > -CONTROL_PASS_THREHOLD:
            slots_added = 0
            slots = list(base_slots)
            str_log += f": < Pass Threshold {CONTROL_PASS_THREHOLD:0.1f}p => Slots Excluded"
        else:
            str_log += f": > Pass Threshold {CONTROL_PASS_THREHOLD:0.1f}p => Slots Included"
            self.best_cost = best_cost

            _LOGGER.info("")
            _LOGGER.info(str_log)

        return slots

    async def _precharge_before(self, slots: list, discharge_slot, target_wh: float, export_price: float) -> list:
        """Add charge in the cheapest slots before discharge_slot until the battery holds target_wh
        at the end of that slot (before its discharge), allowing for the load it covers on the way.

        Only slots whose import price, after round-trip losses, is below the export price are used,
        and never more than fits under 100% between the charge and the discharge.
        """
        round_trip = self.inverter.charger_efficiency * self.inverter.inverter_efficiency
        slots = list(slots)
        flows = await self.flows(slots=slots)
        candidates = flows[
            (flows.index < discharge_slot)
            & (flows["forced"] >= 0)
            & (flows["import"] / round_trip < export_price)
        ]
        for start in candidates.sort_values("import", kind="stable").index:
            shortfall = target_wh - flows.at[discharge_slot, "chg_end"]
            if shortfall < 1:
                break
            dt_hours = flows.at[start, "dt_hours"]
            headroom = self.battery.capacity - flows.loc[start:discharge_slot, "chg_end"].iloc[:-1].max()
            power = min(
                self.charge_power_limit - flows.at[start, "forced"] - flows.at[start, "solar"],
                shortfall / self.inverter.charger_efficiency / dt_hours,
                headroom / self.inverter.charger_efficiency / dt_hours,
            )
            if power < MODEL_MIN_SLOT_POWER:
                continue
            slots.append((start, round(power, 0)))
            flows = await self.flows(slots=slots)
        return slots

    async def _paired_discharge_refill(self, slots: list, best_cost: float) -> tuple[list, float, int]:
        base_best_cost = self.best_cost
        best_slots = list(slots)
        paired_slots_added = 0
        flows = await self.flows(slots=slots)

        min_import_price = flows[flows["forced"] >= 0]["import"].min()
        cheap_slots = flows[flows["import"] <= min_import_price + 0.01]
        if cheap_slots.empty:
            return best_slots, best_cost, paired_slots_added

        first_cheap_slot = cheap_slots.index[0]
        candidate_flows = flows[
            (flows.index < first_cheap_slot)
            & (flows["forced"] <= 0)
            & (-flows["forced"] < self.discharge_power_limit)
            & (flows["soc_end"] > self.battery.max_dod * 100)
            & (flows["solar"] <= flows["consumption"])
        ]

        if candidate_flows.empty:
            return best_slots, best_cost, paired_slots_added

        _LOGGER.info("")
        _LOGGER.info("Paired Discharge/Refill")
        _LOGGER.info("-----------------------")

        for start_window, row in candidate_flows.sort_values("export", ascending=False).iterrows():
            forced_discharge = min(
                self.discharge_power_limit
                + row["forced"]
                - row["solar"],
                ((row["soc_end"] - self.battery.max_dod * 100) / 100 * self.battery.capacity) * 2,
            )
            forced_discharge = max(forced_discharge, 0)
            if forced_discharge < MODEL_MIN_SLOT_POWER:
                continue

            candidate_slots = list(best_slots) + [(start_window, -forced_discharge)]
            previous_best_cost = self.best_cost
            self.best_cost = best_cost
            candidate_slots = await self.fill_first_charging(base_slots=candidate_slots)
            candidate_cost = (await self.net_cost(slots=candidate_slots)).sum()
            self.best_cost = previous_best_cost

            str_log = (
                f"{start_window.strftime(TIME_FORMAT)} discharge {-forced_discharge:5.0f}W "
                f"then refill from {first_cheap_slot.strftime(TIME_FORMAT)} Net: {candidate_cost:6.1f}"
            )

            if _is_finite(candidate_cost) and _is_finite(best_cost) and candidate_cost < best_cost - CONTROL_SLOT_THRESHOLD:
                _LOGGER.info(str_log + " Included")
                best_slots = candidate_slots
                best_cost = candidate_cost
                paired_slots_added += 1
                flows = await self.flows(slots=best_slots)
            else:
                _LOGGER.info(str_log)

        self.best_cost = base_best_cost
        return best_slots, best_cost, paired_slots_added

    async def whole_horizon(self, soc_step_percent: int = 5, state_resolution_wh: int = 50) -> list:
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

    def _whole_horizon_compute(
        self,
        flows: pd.DataFrame,
        soc_step_percent: int,
        state_resolution_wh: int,
        write_cost: float,
    ) -> list:
        required_cols = ["dt_hours", "consumption", "solar", "import", "export"]
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

        # A state is (energy, control in force); the control is "idle" or a rounded forced power,
        # so the write cost can price every change the inverter would have to be sent.
        initial_key = (self._whole_horizon_state_key(initial_energy, state_resolution_wh), "idle")
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
                    control = self._whole_horizon_control(forced_power)
                    candidate_cost = cost + slot_cost + (write_cost if control != previous_control else 0.0)
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

    def _whole_horizon_control(self, forced_power: float | None) -> object:
        """What the inverter is told to do: "idle" or a forced power to the watt."""
        if forced_power is None or abs(forced_power) < MODEL_MIN_SLOT_POWER:
            return "idle"
        return round(forced_power)

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
            energy = sum(p * flows.at[start, "dt_hours"] for start, p in run)
            if run[0][1] >= 0:
                idle_drain = sum(drained[start] for start, p in run if p == 0) / self.inverter.charger_efficiency
                energy = max(energy - idle_drain, 0)
            power = round(energy / hours, 0)
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
                    (max_energy - energy) / self.inverter.charger_efficiency / dt_hours,
                )
            else:
                forced_power = max(
                    forced_power,
                    (min_energy - energy) * self.inverter.inverter_efficiency / dt_hours,
                )

            if abs(forced_power) < MODEL_MIN_SLOT_POWER:
                continue

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
                forced_power = delta / self.inverter.charger_efficiency / dt_hours
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

        battery_temp = requirement if forced_power is None else -forced_power
        if battery_temp > 0:
            battery_flow = battery_temp / self.inverter.inverter_efficiency
        else:
            battery_flow = battery_temp * self.inverter.charger_efficiency

        next_energy = round(min(max(energy - battery_flow * dt_hours, min_energy), max_energy), 1)
        battery_power = (energy - next_energy) / dt_hours
        if battery_power > 0:
            battery_power *= self.inverter.inverter_efficiency
        elif battery_power < 0:
            battery_power /= self.inverter.charger_efficiency

        grid = round(requirement - battery_power, 0)
        return next_energy, grid

    def terminal_soc_value(self, terminal_energy: float) -> float:
        """Signed cost adjustment for the battery's end-of-horizon energy.

        Anchored to the run-start SoC. Ending below it is penalised by the grid
        cost of buying the shortfall back at the cheapest forward-window prices
        (its replacement cost). Ending above it is symmetrically credited with
        the replacement cost of that surplus - the cheapest a rational operator
        could rebuy it - but only when `value_surplus_soc` is enabled; with the
        toggle off the behaviour is the original deficit-only penalty.

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

        if not getattr(self, "value_surplus_soc", False):
            return 0.0
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

