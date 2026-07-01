import logging

import pandas as pd
from numpy import isnan

from .const import (
    TIME_FORMAT,
    OPTIMISER_MAX_ITERS,
    OPTIMISER_HIGH_COST_MAX_ITERS,
    CONTROL_PASS_THREHOLD,
    CONTROL_SLOT_THRESHOLD,
    MODEL_MIN_SLOT_POWER,
    MODEL_MAX_SEARCH_WINDOW_SOC,
)
from .utils import get_value

_LOGGER = logging.getLogger(__name__)


def get_dt_hours(df: pd.DataFrame | pd.Series) -> pd.Series:
    df = pd.DataFrame(df)
    df["dt_hours"] = -df.index.diff(-1) / pd.Timedelta("60min")
    return df["dt_hours"].ffill()


class InverterModel:
    """Describes the inverter

    Attributes:
        inverter_efficiency: A float describing the DC-AC efficiency of the inverter.
        charger_efficiency: A float describing the AC-DC efficiency of the inverter.
        inverter_loss: An int describing the internal power consumption of the inverter at zero load.
        inverter_power: An int describing the DC-AC power of the inverter.
        charger_power: An int describing the AC-CC power of the inverter.
    """

    def __init__(
        self,
        inverter_efficiency: float = 0.97,
        charger_efficiency: float = 0.91,
        inverter_loss: int = 100,
        inverter_power: int = 3000,
        charger_power: int = 3500,
    ) -> None:
        self.inverter_efficiency = inverter_efficiency / 100
        self.charger_efficiency = charger_efficiency / 100
        self.inverter_power = inverter_power
        self.charger_power = charger_power
        self.inverter_loss = inverter_loss

    def __str__(self):
        pass


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
        # voltage: int = 50,
    ) -> None:
        self.capacity = battery_capacity
        self.max_dod = battery_minimum_soc / 100
        self.current_limit_amps = battery_current_limit
        # self.voltage = voltage

    def __str__(self):
        pass

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

    def __str__(self):
        pass

    @property
    def tz(self):
        return self._tz

    @property
    def battery(self):
        return self._battery

    @property
    def inverter(self):
        return self._inverter

    def set_start(self, start: pd.Timestamp) -> bool:
        self._index = [start.floor("1min")] + list(self.consumption.index[1:])

    @property
    def index(self):
        try:
            return self._index
        except:
            return self.consumption.index

    async def forced(self, slots=[]) -> pd.Series:
        forced = pd.Series(index=self.index, data=0)

        for t, c in slots:
            if not isnan(c):
                forced.loc[t] += int(c)

        return forced

    async def flows(self, *args, **kwargs):
        df = pd.concat([self.solar, self.consumption, self.prices], axis=1)
        df.index = self.index
        df["dt_hours"] = get_dt_hours(df)
        df["battery_grid_requirement"] = df["consumption"] - df["solar"]
        df["battery_temp"] = df["consumption"] - df["solar"]

        df["forced"] = await self.forced(*args, **kwargs)
        chg_mask = df["forced"] != 0
        df["battery_temp"][chg_mask] = -df["forced"][chg_mask]

        chg = [self.initial_soc / 100 * self.battery.capacity]

        for idx in df.index:
            flow = df["battery_temp"].loc[idx]
            dt_hours = df["dt_hours"].loc[idx]

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
                            search_window = search_window[search_window["forced"] < (self.inverter.charger_power)]
                            search_window = search_window[search_window["soc_end"] <= MODEL_MAX_SEARCH_WINDOW_SOC]

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
                                        self.inverter.charger_power
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
                                        df["available"][slot] = False

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
                                df["tested"].loc[max_slot] = True
                        else:
                            _LOGGER.info(str_log + "No search window")
                            df["tested"].loc[max_slot] = True
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

    async def low_cost_charging(self, base_slots=[]):
        best_cost = self.best_cost
        slots_added = 0

        slots = base_slots

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
            & (flows["forced"] < self.inverter.charger_power)  # forced charge capacity available
            & (flows["forced"] >= 0)  # not forced discharging
        )

        a0 = available.sum()
        _LOGGER.info(f"{a0} slots have an import price less than the max export price")
        done = a0 == 0

        while not done:
            low_cost_import_slots = flows.loc[
                available
                & (flows["import"] < max_export_price)
                & (flows["forced"] < self.inverter.charger_power)
                & (flows["forced"] >= 0)
            ]
            i += 1
            done = i > a0

            min_price = low_cost_import_slots["import"].min()

            # # Add rounding to ensure matching (may not be needed)
            # x["import"] = x["import"].round(2)
            # min_price = min_price.round(2)

            if len(low_cost_import_slots[low_cost_import_slots["import"] == min_price]) > 0:
                start_window = low_cost_import_slots[low_cost_import_slots["import"] == min_price].index[0]
                available.loc[start_window] = False
                str_log = ""
                str_log = f"{available.sum():>2d} Min import price {min_price:5.2f}p/kWh at {start_window.strftime(TIME_FORMAT)} {flows.loc[start_window]['forced']:4.0f}W "

                str_log += "  "
                factor = 1

                str_log += f"SOC: {flows.loc[start_window]['soc']:5.1f}%->{flows.loc[start_window]['soc_end']:5.1f}% "

                forced_charge = min(
                    min(self.battery.max_charge_power, self.inverter.charger_power)
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

                net_cost = await self.net_cost(slots=slots)

                str_log += f"Net: {net_cost:5.1f} "
                if net_cost < best_cost - CONTROL_SLOT_THRESHOLD:
                    str_log += f"New SOC: {self.flows.loc[start_window]['soc']:5.1f}%->{self.flows.loc[start_window]['soc_end']:5.1f}% "
                    str_log += f"Max export: {-self.flows['grid'].min():0.0f}W "
                    best_cost = net_cost
                    slots_added += 1

                else:
                    # done = True
                    slots = slots[:-1]
                    flows = await self.flows(slots=slots)

                done = available.sum() == 0
            else:
                done = True

        cost_delta = best_cost - self.best_cost
        str_log = f"Charge net cost delta:{(-cost_delta):5.1f}p"

        if cost_delta > -CONTROL_PASS_THREHOLD:
            slots_added = 0
            slots = base_slots
            str_log += f": < Pass Threshold {CONTROL_PASS_THREHOLD:0.1f}p => Slots Excluded"

        else:
            str_log += f": > Pass Threshold {CONTROL_PASS_THREHOLD:0.1f}p => Slots Included"
            self.best_cost = best_cost

            _LOGGER.info("")
            _LOGGER.info(str_log)

        return slots

    async def discharging(self, base_slots=[]):
        best_cost = self.best_cost
        slots_added = 0

        slots = base_slots

        flows = await self.flows(slots=slots)

        # Check how many slots which aren't full are at an export price less than any import price:
        min_import_price = self.flows["import"].min()
        _LOGGER.info("")
        _LOGGER.info("Forced Discharging")
        _LOGGER.info("------------------")
        _LOGGER.info("")

        _LOGGER.info(f"Min import price when there is no forced discharge: {min_import_price:0.2f}p/kWh.")

        i = 0
        available = (
            (self.flows["export"] > min_import_price)
            & (-flows["forced"] < self.inverter.inverter_power)
            & (self.flows["forced"] <= 0)
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

                slot = (
                    start_window,
                    -min(
                        min(
                            self.battery.max_discharge_power,
                            self.inverter.charger_power,
                        )
                        - flows["solar"].loc[start_window],
                        ((flows["soc_end"].loc[start_window] - self.battery.max_dod) / 100 * self.battery.capacity)
                        * 2
                        * factor,
                    ),
                )

                slots.append(slot)

                self.calculate_flows(slots=slots)

                if "F" in self._debug_cat:
                    _LOGGER.debug("self.flows after flows called = ")
                    _LOGGER.debug(f"\n{self.flows.to_string()}")

                net_cost = self.net_cost

                str_log += f"Net: {net_cost:5.1f} "
                if net_cost < best_cost - self._get_config("slot_threshold_p"):
                    str_log += f"New SOC: {self.flows.loc[start_window]['soc']:5.1f}%->{self.flows.loc[start_window]['soc_end']:5.1f}% "
                    str_log += f"Max export: {-self.flows['grid'].min():0.0f}W "
                    best_cost = net_cost
                    slots_added += 1
                    _LOGGER.debug(str_log)
                else:
                    # done = True
                    slots = slots[:-1]
                    self.calculate_flows(slots=slots)
            else:
                done = True

        cost_delta = best_cost - self.best_cost
        str_log = f"Discharge net cost delta:{(-cost_delta):5.1f}p"
        if cost_delta > -self._get_config("discharge_threshold_p"):
            str_log += f": < Discharge threshold ({self._get_config('discharge_threshold_p'):0.1f}p) => Slots excluded"
        else:
            str_log += f": > Discharge Threshold ({self._get_config('discharge_threshold_p'):0.1f}p) => Slots included"
            self.slots = slots
            self.slots_added = slots_added
            self.best_cost = best_cost

        if log:
            _LOGGER.info("")
            _LOGGER.info(str_log)


#     async def optimised_force(
#         self,
#         discharge=False,
#         use_export=True,
#     ):

#         self.calculate_flows()
#         self.base_cost = self.net_cost
#         self.best_cost = self.base_cost
#         self.net_costs = [self.base_cost]

#         if log:
#             _LOGGER.info(f"Base cost:  {self.base_cost}")

#         self._high_cost_swaps(log=log)

#         if self.prices["export"].sum() > 0:
#             j = 0
#         else:
#             j = max_iters

#         self.slots_added = 999

#         while (self.slots_added > 0) and (j < max_iters):
#             j += 1
#             # No need to iterate if this is charge only
#             if not discharge:
#                 j += max_iters

#             self._low_cost_charging(log=log)

#             if log:
#                 _LOGGER.info(f"Iteration {j:2d}: Slots added: {self.slots_added:3d}")

#             if discharge:
#                 self._discharging(log=log)

#         self.calculate_flows(slots=self.slots)

#         # df.index = pd.to_datetime(df.index)

#         if (not self._get_config("allow_cyclic")) and (len(self.slots) > 0) and discharge:
#             if log:
#                 _LOGGER.info("")
#                 _LOGGER.info("Removing cyclic charge/discharge")
#             a = self.flows["forced"][self.flows["forced"] != 0].to_dict()
#             new_slots = [(k, a[k]) for k in a]

#             revised_slots = []
#             skip_flag = False
#             for i, x in enumerate(zip(new_slots[:-1], new_slots[1:])):

#                 if (
#                     (int(x[0][1]) == self.inverter.charger_power)
#                     & (int(-x[1][1]) == self.inverter.charger_power)
#                     & (x[1][0] - x[0][0] == pd.Timedelta("30min"))
#                 ):
#                     skip_flag = True
#                     if log:
#                         _LOGGER.info(
#                             f"  Skipping slots at {x[0][0].strftime(TIME_FORMAT)} ({x[0][1]}W) and {x[1][0].strftime(TIME_FORMAT)} ({x[1][1]}W)"
#                         )
#                 elif skip_flag:
#                     skip_flag = False
#                 else:
#                     revised_slots.append(x[0])
#                     if i == len(new_slots) - 2:
#                         revised_slots.append(x[1])

#             self.calculate_flows(slots=revised_slots)

#             best_cost_new = self.net_cost
#             if log:
#                 _LOGGER.info(f"  Net cost revised from {self.best_cost:0.1f}p to {best_cost_new:0.1f}p")
#             slots = revised_slots
#             # self.flows.index = pd.to_datetime(df.index)
#         return self.flows

# # %%
