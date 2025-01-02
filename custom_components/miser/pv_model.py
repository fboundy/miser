import logging

import pandas as pd
from numpy import isnan

from .const import TIME_FORMAT, OPTIMISER_MAX_ITERS

_LOGGER = logging.getLogger(__name__)


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
        self.inverter_efficiency = inverter_efficiency
        self.charger_efficiency = charger_efficiency
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
        capacity: int,
        max_dod: float = 0.15,
        current_limit_amps: int = 100,
        # voltage: int = 50,
    ) -> None:
        self.capacity = capacity
        self.max_dod = max_dod
        self.current_limit_amps = current_limit_amps
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
        tz: str = "GB",
        debug_cat: list = [],
    ) -> None:
        self.inverter = inverter
        self.battery = battery
        self._tz = tz
        self.prices = None
        self.static_flows = None
        self.flows = None
        self.contract = None
        self._debug_cat = debug_cat

    def __str__(self):
        pass

    def calculate_flows(self, slots=[], solar_id="solar", consumption_id="consumption", **kwargs):
        solar = self.static_flows[solar_id]
        consumption = self.static_flows[consumption_id]

        self.flows = self.static_flows.copy()

        battery_flows = solar - consumption
        forced_charge = pd.Series(index=self.flows.index, data=0)

        if len(slots) > 0:
            timed_slot_flows = pd.Series(index=self.flows.index, data=0)

            for t, c in slots:
                if not isnan(c):
                    timed_slot_flows.loc[t] += int(c)

            chg_mask = timed_slot_flows != 0
            battery_flows[chg_mask] = timed_slot_flows[chg_mask]
            forced_charge[chg_mask] = timed_slot_flows[chg_mask]

        if self.soc_now is None:
            chg = [self.initial_soc / 100 * self.battery.capacity]
            freq = pd.infer_freq(self.static_flows.index) / pd.Timedelta(60, "minutes")

        else:
            chg = [self.soc_now[1] / 100 * self.battery.capacity]
            freq = (self.soc_now[0] - self.flows.index[0]) / pd.Timedelta(60, "minutes")

        for i, flow in enumerate(battery_flows):
            if flow < 0:
                flow = flow / self.inverter.inverter_efficiency
            else:
                flow = flow * self.inverter.charger_efficiency

            chg.append(
                round(
                    max(
                        [
                            min(
                                [
                                    chg[-1] + flow * freq,
                                    self.battery.capacity,
                                ]
                            ),
                            self.battery.max_dod * self.battery.capacity,
                        ]
                    ),
                    1,
                )
            )
            if (self.soc_now is not None) and (i == 0):
                freq = pd.infer_freq(self.static_flows.index) / pd.Timedelta(60, "minutes")

        if self.soc_now is not None:
            chg[0] = self.initial_soc / 100 * self.battery.capacity

        self.flows["chg"] = chg[:-1]
        self.flows["chg"] = self.flows["chg"].ffill()
        self.flows["chg_end"] = chg[1:]
        self.flows["chg_end"] = self.flows["chg_end"].bfill()
        self.flows["battery"] = (pd.Series(chg).diff(-1) / freq)[:-1].to_list()
        self.flows.loc[self.flows["battery"] > 0, "battery"] = (
            self.flows["battery"] * self.inverter.inverter_efficiency
        )
        self.flows.loc[self.flows["battery"] < 0, "battery"] = self.flows["battery"] / self.inverter.charger_efficiency
        self.flows["grid"] = -(solar - consumption + self.flows["battery"]).round(0)
        self.flows["forced"] = forced_charge
        self.flows["soc"] = (self.flows["chg"] / self.battery.capacity) * 100
        self.flows["soc_end"] = (self.flows["chg_end"] / self.battery.capacity) * 100

        if self.prices is not None:
            self.flows = pd.concat(
                [self.prices, consumption, self.flows],
                axis=1,
            )

    @property
    def net_cost(self):
        if self.flows is not None:
            return self.contract.net_cost(self.flows)

    def optimised_force(
        self,
        log=True,
        discharge=False,
        use_export=True,
        max_iters=OPTIMISER_MAX_ITERS,
    ):

        if log and ("B" in self._debug_cat):
            _LOGGER.debug("Called optimised_force")

        start = self.static_flows.index[0]
        end = self.static_flows.index[-1]

        self.prices = self.contract.prices(start=start, end=end)
        self.prices = self.prices.set_axis(
            [t for t in self.contract.tariffs.keys() if self.contract.tariffs[t] is not None],
            axis=1,
        )

        if not use_export:
            if log:
                _LOGGER.info(f"Ignoring export pricing because Use Export is turned off")
            discharge = False
            self.prices["export"] = 0

        if log and ("B" in self._debug_cat):
            _LOGGER.debug("")
            _LOGGER.debug("Prices is")
            _LOGGER.debug(f"\n{self.prices.to_string()}")
            _LOGGER.debug("")

        if log:
            _LOGGER.info(
                f"Optimiser prices loaded for period {self.prices.index[0].strftime(TIME_FORMAT)} - {self.prices.index[-1].strftime(TIME_FORMAT)}"
            )

        self.calculate_flows()
        self.base_cost = self.net_cost
        self.best_cost = self.base_cost
        self.net_costs = [self.base_cost]

        if log:
            _LOGGER.info(f"Base cost:  {self.base_cost}")

        self._high_cost_swaps(log=log)

        if self.prices["export"].sum() > 0:
            j = 0
        else:
            j = max_iters

        self.slots_added = 999

        while (self.slots_added > 0) and (j < max_iters):
            j += 1
            # No need to iterate if this is charge only
            if not discharge:
                j += max_iters

            self._low_cost_charging(log=log)

            if log:
                _LOGGER.info(f"Iteration {j:2d}: Slots added: {self.slots_added:3d}")

            if discharge:
                self._discharging(log=log)

        self.calculate_flows(slots=self.slots)

        # df.index = pd.to_datetime(df.index)

        if (not self._get_config("allow_cyclic")) and (len(self.slots) > 0) and discharge:
            if log:
                _LOGGER.info("")
                _LOGGER.info("Removing cyclic charge/discharge")
            a = self.flows["forced"][self.flows["forced"] != 0].to_dict()
            new_slots = [(k, a[k]) for k in a]

            revised_slots = []
            skip_flag = False
            for i, x in enumerate(zip(new_slots[:-1], new_slots[1:])):

                if (
                    (int(x[0][1]) == self.inverter.charger_power)
                    & (int(-x[1][1]) == self.inverter.charger_power)
                    & (x[1][0] - x[0][0] == pd.Timedelta("30min"))
                ):
                    skip_flag = True
                    if log:
                        _LOGGER.info(
                            f"  Skipping slots at {x[0][0].strftime(TIME_FORMAT)} ({x[0][1]}W) and {x[1][0].strftime(TIME_FORMAT)} ({x[1][1]}W)"
                        )
                elif skip_flag:
                    skip_flag = False
                else:
                    revised_slots.append(x[0])
                    if i == len(new_slots) - 2:
                        revised_slots.append(x[1])

            self.calculate_flows(slots=revised_slots)

            best_cost_new = self.net_cost
            if log:
                _LOGGER.info(f"  Net cost revised from {self.best_cost:0.1f}p to {best_cost_new:0.1f}p")
            slots = revised_slots
            # self.flows.index = pd.to_datetime(df.index)
        return self.flows

    def _search_window(self, df: pd.DataFrame, available: pd.Series, max_slot):
        x = df.loc[: max_slot - pd.Timedelta("30min")].copy().iloc[:-1]
        if len(x) > 0:
            x = x[available.loc[: max_slot - pd.Timedelta("30min")]]
            x["countback"] = (x["soc_end"] >= 97).sum() - (x["soc_end"] >= 97).cumsum()
            x = x[x["countback"] == 0]
            x = x[x["forced"] < (self.inverter.charger_power)]
            x = x[x["soc_end"] <= 97]
        return x

    def _high_cost_swaps(self, log=True):
        # --------------------------------------------------------------------------------------------
        #  Charging 1st Pass
        # --------------------------------------------------------------------------------------------
        if log:
            _LOGGER.info("")
            _LOGGER.info("High Cost Usage Swaps")
            _LOGGER.info("---------------------")
            _LOGGER.info("")

            if log and ("C" in self._debug_cat):
                _LOGGER.info(
                    "SPR = Slot Power Required, SCPA = Slot Charger Power Available, SAC = Slot Available Capacity, RSC = Remaining Slot Capacity"
                )
                _LOGGER.info("")

        done = False
        i = 0
        slots = []
        available = pd.Series(index=self.flows.index, data=(self.flows["forced"] == 0))
        tested = pd.Series(index=self.flows.index, data=False)
        slot_count = [0]
        best_cost = self.base_cost

        while not done:
            i += 1

            if (i > 96) or (available.sum() == 0):
                done = True

            import_cost = ((self.flows["import"] * self.flows["grid"]).clip(0) / 2000)[~tested]

            if len(import_cost[self.flows["forced"] == 0]) > 0:
                max_import_cost = import_cost[self.flows["forced"] == 0].max()
                if len(import_cost[import_cost == max_import_cost]) > 0:
                    max_slot = import_cost[import_cost == max_import_cost].index[0]
                    max_slot_energy = round(self.flows["grid"].loc[max_slot] / 2000, 2)  # kWh
                    str_log = f"{i:3d} {available.sum():3d} {max_slot.tz_convert(self._tz).strftime(TIME_FORMAT)}:"

                    if max_slot_energy > 0:
                        round_trip_energy_required = (
                            max_slot_energy / self.inverter.charger_efficiency / self.inverter.inverter_efficiency
                        )

                        search_window = self._search_window(self.flows, available, max_slot)
                        str_log += f" {round_trip_energy_required:5.2f} kWh at {max_import_cost:6.2f}p. "

                        if len(search_window) > 0:
                            min_price = search_window["import"].min()

                            window = search_window[search_window["import"] == min_price].index
                            start_window = window[0]

                            cost_at_min_price = round_trip_energy_required * min_price

                            str_log += f"<==> {start_window.tz_convert(self._tz).strftime(TIME_FORMAT)}: {min_price:5.2f}p/kWh {cost_at_min_price:5.2f}p "
                            str_log += f" SOC: {search_window.loc[window[0]]['soc']:5.1f}%->{search_window.loc[window[-1]]['soc_end']:5.1f}% "

                            factors = [1 / len(window) for slot in window]

                            if round(cost_at_min_price, 1) < round(max_import_cost, 1):
                                slots_added = 0
                                for slot, factor in zip(window, factors):
                                    slot_power_required = max(round_trip_energy_required * 2000 * factor, 0)
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

                                    if remaining_slot_capacity < 10:
                                        available[slot] = False

                                    if log:
                                        # if log:
                                        str_log_x = (
                                            f">>> {i:3d} Slot: {slot.strftime(TIME_FORMAT)} Factor: {factor:0.3f} Forced: {search_window['forced'].loc[slot]:6.0f}W  "
                                            + f"End SOC: {search_window['soc_end'].loc[slot]:4.1f}%  SPR: {slot_power_required:6.0f}W  "
                                            + f"SCPA: {slot_charger_power_available:6.0f}W  SAC: {slot_available_capacity:6.0f}W  Min Power: {min_power:6.0f}W "
                                            + f"RSC: {remaining_slot_capacity:6.0f}W"
                                        )
                                        if not available[slot]:
                                            str_log_x += " <== FULL"
                                        _LOGGER.debug(str_log_x)

                                    slots.append(
                                        (
                                            slot,
                                            round(min_power, 0),
                                        )
                                    )
                                    slots_added += 1

                                self.calculate_flows(slots=slots)
                                self.net_costs.append(self.net_cost)

                                slot_count.append(len(factors))

                                str_log += f"New SOC: {self.flows.loc[start_window]['soc']:5.1f}%->{self.flows.loc[start_window]['soc_end']:5.1f}% "
                                best_cost = self.net_costs[-1]
                                str_log += f"Net: {best_cost:6.1f}"

                                if log:
                                    _LOGGER.info(str_log)

                            else:
                                if log:
                                    _LOGGER.info(str_log + "No cheaper slots")
                                tested.loc[max_slot] = True
                        else:
                            if log:
                                _LOGGER.info(str_log + "No search window")
                            done = True
                else:
                    done = True
            else:
                _LOGGER.info("No slots available")
                done = True

        self.calculate_flows(slots=slots)
        self.best_cost = self.net_cost

        if self.base_cost - best_cost <= self._get_config("pass_threshold_p"):
            if log:
                _LOGGER.info(
                    f"Charge net cost delta:  {self.base_cost - best_cost:0.1f}p: < Pass Threshold ({self._get_config('pass_threshold_p'):0.1f}p) => Slots Excluded"
                )
            slots = []
            self.best_cost = self.base_cost
            self.calculate_flows()

        self.slots = slots

    def _low_cost_charging(self, log=True):
        slots = [slot for slot in self.slots]
        best_cost = self.best_cost
        slots_added = 0

        # Check how many slots which aren't full are at an import price less than any export price:
        max_export_price = self.flows[self.flows["forced"] <= 0]["export"].max()
        if log:
            _LOGGER.info("")
            _LOGGER.info("Low Cost Charging")
            _LOGGER.info("------------------")
            _LOGGER.info("")

        # net_cost_previous = best_cost

        if log:
            _LOGGER.info(f"Max export price when there is no forced charge: {max_export_price:0.2f}p/kWh.")

        i = 0
        available = (
            (self.flows["import"] < max_export_price)
            & (self.flows["forced"] < self.inverter.charger_power)
            & (self.flows["forced"] >= 0)
        )

        a0 = available.sum()
        if log:
            _LOGGER.info(f"{available.sum()} slots have an import price less than the max export price")
        done = available.sum() == 0

        if "C" in self._debug_cat:
            _LOGGER.debug(f"\n{self.flows.to_string()}")

        while not done:
            x = (
                self.flows.loc[available]
                .loc[self.flows["import"] < max_export_price]
                .loc[self.flows["forced"] < self.inverter.charger_power]
                .loc[self.flows["forced"] >= 0]
                .copy()
            )
            i += 1
            done = i > a0

            min_price = x["import"].min()

            # Add rounding to ensure matching (may not be needed)
            x["import"] = x["import"].round(2)
            min_price = min_price.round(2)

            if len(x[x["import"] == min_price]) > 0:
                start_window = x[x["import"] == min_price].index[0]
                available.loc[start_window] = False
                str_log = ""
                str_log = f"{available.sum():>2d} Min import price {min_price:5.2f}p/kWh at {start_window.strftime(TIME_FORMAT)} {x.loc[start_window]['forced']:4.0f}W "

                str_log += "  "
                factor = 1

                str_log += f"SOC: {x.loc[start_window]['soc']:5.1f}%->{x.loc[start_window]['soc_end']:5.1f}% "

                if "C" in self._debug_cat:
                    _LOGGER.debug(
                        f"SOC (before modelling Forced Charge): {x.loc[start_window]['soc']:5.1f}%->{x.loc[start_window]['soc_end']:5.1f}% "
                    )

                forced_charge = min(
                    min(self.battery.max_charge_power, self.inverter.charger_power)
                    - x["forced"].loc[start_window]
                    - x["solar"].loc[start_window],
                    ((100 - x["soc_end"].loc[start_window]) / 100 * self.battery.capacity) * 2 * factor,
                )
                if "C" in self._debug_cat:
                    _LOGGER.debug(f"Forced Charge = {forced_charge}")
                slot = (
                    start_window,
                    forced_charge,
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
                    if log:
                        _LOGGER.info(str_log)
                else:
                    # done = True
                    slots = slots[:-1]
                    self.calculate_flows(slots=slots)

                done = available.sum() == 0
            else:
                done = True

        cost_delta = best_cost - self.best_cost
        str_log = f"Charge net cost delta:{(-cost_delta):5.1f}p"
        if cost_delta > -self._get_config("pass_threshold_p"):
            self.slots_added = 0
            str_log += f": < Pass Threshold {self._get_config('pass_threshold_p'):0.1f}p => Slots Excluded"
            self.calculate_flows(slots=self.slots)
        else:
            str_log += f": > Pass Threshold {self._get_config('pass_threshold_p'):0.1f}p => Slots Included"
            self.slots = slots
            self.slots_added = slots_added
            self.best_cost = best_cost

        if log:
            _LOGGER.info("")
            _LOGGER.info(str_log)

    def _discharging(self, log=True):
        # -----------
        # Discharging
        # -----------
        slots = [slot for slot in self.slots]
        best_cost = self.best_cost
        slots_added = self.slots_added

        # Check how many slots which aren't full are at an export price less than any import price:
        min_import_price = self.flows["import"].min()
        if log:
            _LOGGER.info("")
            _LOGGER.info("Forced Discharging")
            _LOGGER.info("------------------")
            _LOGGER.info("")

        i = 0
        available = (self.flows["export"] > min_import_price) & (self.flows["forced"] == 0)
        a0 = available.sum()
        if log:
            _LOGGER.info(f"{available.sum()} slots have an export price greater than the min import price")
        done = available.sum() == 0

        while not done:
            x = self.flows[available].copy()
            i += 1
            done = i > a0
            max_price = x["export"].max()

            if len(x[x["export"] == max_price]) > 0:
                start_window = x[x["export"] == max_price].index[0]
                available.loc[start_window] = False
                str_log = f"{available.sum():>2d} Max export price {max_price:5.2f}p/kWh at {start_window.strftime(TIME_FORMAT)} "
                str_log += "  "

                factor = 1
                str_log += f"SOC: {x.loc[start_window]['soc']:5.1f}%->{x.loc[start_window]['soc_end']:5.1f}% "

                slot = (
                    start_window,
                    -min(
                        min(
                            self.battery.max_discharge_power,
                            self.inverter.charger_power,
                        )
                        - x["solar"].loc[start_window],
                        ((x["soc_end"].loc[start_window] - self.battery.max_dod) / 100 * self.battery.capacity)
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


# %%
