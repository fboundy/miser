import pandas as pd
from numpy import isnan
import logging
import asyncio

_LOGGER = logging.getLogger(__name__)


class BatteryModel:
    """Describes the battery system attached to the inverter

    Attributes:
        capacity: An integer describing the Wh capacity of the battery.
        max_dod: A float describing the maximum depth of discharge of the battery.
        current_limit_amps: An int describing the maximum amps at which the battery can charge/discharge.
        voltage: An int describing the voltage of the battery system.
    """

    def __init__(self, capacity: int, max_dod: float = 0.15, current_limit_amps: int = 100, voltage: int = 50) -> None:
        self.capacity = capacity
        self.max_dod = max_dod
        self.current_limit_amps = current_limit_amps
        self.voltage = voltage

    def __str__(self):
        pass

    @property
    def max_charge_power(self) -> int:
        """returns the maximum watts at which the battery can charge."""
        return self.current_limit_amps * self.voltage

    @property
    def max_discharge_power(self) -> int:
        """returns the maximum watts at which the battery can discharge."""
        return self.max_charge_power


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


class PVsystemModel:
    def __init__(self, name: str, inverter: InverterModel, battery: BatteryModel) -> None:
        self.name = name
        self.inverter = inverter
        self.battery = battery
        self.tz = "GB"

    def __str__(self):
        pass

    def flows(
        self,
        initial_soc: float,
        df_solar_and_consumption: pd.DataFrame,
        forced_charge_slots: list[tuple] = [],
        soc_now: float = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Applies the forced charge described in forced_charge_slots to a DataFrame containing the solar and consumption estinmates

        Parameters:

        initial_soc: the battery SOC at the start of the DataFrame
        df_solar_and_consumption: a DataFrame with a DateTimeIndex of fixed freq 30 minutes with two columns "solar" and "consumption".
                                  Each entry is average power for the 30 minute slot in W
        forced_charge_slots: a list of tuples of (start_time, power) +ve power is charge, -ve power is forced discharge
        soc_now: a tuplee of (the time that the method is called, the battery SOC at that time)
        """

        solar = df_solar_and_consumption["solar"]
        consumption = df_solar_and_consumption["consumption"]

        df_flows = df_solar_and_consumption.copy()

        battery_flows = solar - consumption
        forced_charge = pd.Series(index=df_flows.index, data=0)

        if len(forced_charge_slots) > 0:
            timed_slot_flows = pd.Series(index=df_flows.index, data=0)

            for t, c in forced_charge_slots:
                if not isnan(c):
                    timed_slot_flows.loc[t] += int(c)

            battery_charge_wh_mask = timed_slot_flows != 0
            battery_flows[battery_charge_wh_mask] = timed_slot_flows[battery_charge_wh_mask]
            forced_charge[battery_charge_wh_mask] = timed_slot_flows[battery_charge_wh_mask]

        if soc_now is None:
            battery_charge_wh = [initial_soc / 100 * self.battery.capacity]
            freq = pd.infer_freq(df_solar_and_consumption.index) / pd.Timedelta(60, "minutes")

        else:
            battery_charge_wh = [soc_now[1] / 100 * self.battery.capacity]
            freq = (soc_now[0] - df_flows.index[0]) / pd.Timedelta(60, "minutes")

        for i, flow in enumerate(battery_flows):
            if flow < 0:
                flow = flow / self.inverter.inverter_efficiency
            else:
                flow = flow * self.inverter.charger_efficiency

            battery_charge_wh.append(
                round(
                    max(
                        [
                            min(
                                [
                                    battery_charge_wh[-1] + flow * freq,
                                    self.battery.capacity,
                                ]
                            ),
                            self.battery.max_dod * self.battery.capacity,
                        ]
                    ),
                    1,
                )
            )
            if (soc_now is not None) and (i == 0):
                freq = pd.infer_freq(df_solar_and_consumption.index) / pd.Timedelta(60, "minutes")

        if soc_now is not None:
            battery_charge_wh[0] = [initial_soc / 100 * self.battery.capacity]

        df_flows["battery_charge_wh"] = battery_charge_wh[:-1]
        df_flows["battery_charge_wh"] = df_flows["battery_charge_wh"].ffill()
        df_flows["battery_charge_wh_end"] = battery_charge_wh[1:]
        df_flows["battery_charge_wh_end"] = df_flows["battery_charge_wh_end"].bfill()
        df_flows["battery"] = (pd.Series(battery_charge_wh).diff(-1) / freq)[:-1].to_list()
        df_flows.loc[df_flows["battery"] > 0, "battery"] = df_flows["battery"] * self.inverter.inverter_efficiency
        df_flows.loc[df_flows["battery"] < 0, "battery"] = df_flows["battery"] / self.inverter.charger_efficiency
        df_flows["grid"] = -(solar - consumption + df_flows["battery"]).round(0)
        df_flows["forced"] = forced_charge
        df_flows["soc"] = (df_flows["battery_charge_wh"] / self.battery.capacity) * 100
        df_flows["soc_end"] = (df_flows["cbattery_charge_wh_end"] / self.battery.capacity) * 100

        return df_flows

    def optimised_force(self, initial_soc, df_solar_and_consumption, contract: Contract, log=True, **kwargs):
        consumption = df_solar_and_consumption["consumption"]

        discharge = kwargs.pop("discharge", False)
        use_export = kwargs.pop("export", True)
        max_iters = kwargs.pop("max_iters", MAX_OPTIMISATION_ITERS)

        start = df_solar_and_consumption.index[0]
        end = df_solar_and_consumption.index[-1]

        prices = contract.get_prices(start=start, end=end)

        if log:
            _LOGGER.info(
                f"Optimiser prices loaded for period {start.strftime(TIME_FORMAT)} - {end.strftime(TIME_FORMAT)}"
            )

        prices = prices.set_axis([t for t in contract.tariffs.keys() if contract.tariffs[t] is not None], axis=1)

        if not use_export:
            if log:
                _LOGGER.info(f"Ignoring export pricing because Use Export is turned off")
            discharge = False
            prices["export"] = 0

        df_flows, base_cost = self._calc(prices, initial_soc, df_solar_and_consumption, contract)
        if log:
            _LOGGER.info(f"Base cost:  {base_cost}")

        slots = []

        # --------------------------------------------------------------------------------------------
        #  Charging 1st Pass
        # --------------------------------------------------------------------------------------------
        if log:
            _LOGGER.info("")
            _LOGGER.info("High Cost Usage Swaps")
            _LOGGER.info("---------------------")
            _LOGGER.info("")

        available = pd.Series(index=df_flows.index, data=(df_flows["forced"] == 0))
        net_cost = [base_cost]
        net_cost_opt = base_cost

        slot_count = [0]
        done = False
        i = 0
        while not done:
            i += 1
            if (i > 96) or (available.sum() == 0):
                done = True

            import_cost = ((df_flows["import"] * df_flows["grid"]).clip(0) / 2000)[available]

            if len(import_cost[df_flows["forced"] == 0]) > 0:
                max_import_cost = import_cost[df_flows["forced"] == 0].max()
                if len(import_cost[import_cost == max_import_cost]) > 0:
                    max_slot = import_cost[import_cost == max_import_cost].index[0]
                    max_slot_energy = round(df_flows["grid"].loc[max_slot] / 2000, 2)  # kWh

                    if max_slot_energy > 0:
                        round_trip_energy_required = (
                            max_slot_energy / self.inverter.charger_efficiency / self.inverter.inverter_efficiency
                        )

                        # potential windows end at the max_slot
                        pre_max = df_flows.loc[:max_slot].copy()
                        pre_max = pre_max[available.loc[:max_slot]]

                        # count back to find the slots where soc_end < 100
                        pre_max["countback"] = (pre_max["soc_end"] >= 97).sum() - (pre_max["soc_end"] >= 97).cumsum()

                        pre_max = pre_max[pre_max["countback"] == 0]
                        # ignore slots which are already fully charging
                        pre_max = pre_max[pre_max["forced"] < (self.inverter.charger_power)]
                        pre_max = pre_max[pre_max["soc_end"] <= 97]
                        search_window = x.index

                        str_log = f"{i:3d} {available.sum():3d} {max_slot.tz_convert(self.tz).strftime(TIME_FORMAT)}: {round_trip_energy_required:5.2f} kWh at {max_import_cost:6.2f}p. "
                        if len(search_window) == 0:
                            done = True

                        if len(x) > 0:
                            min_price = pre_max["import"].min()

                            window = pre_max[pre_max["import"] == min_price].index
                            start_window = window[0]

                            cost_at_min_price = round_trip_energy_required * min_price

                            str_log += f"<==> {start_window.tz_convert(self.tz).strftime(TIME_FORMAT)}: {min_price:5.2f}p/kWh {cost_at_min_price:5.2f}p "
                            str_log += f" SOC: {x.loc[window[0]]['soc']:5.1f}%->{x.loc[window[-1]]['soc_end']:5.1f}% "
                            factors = []

                            for slot in window:
                                factors.append(1)

                            factors = [f / sum(factors) for f in factors]

                            if round(cost_at_min_price, 1) < round(max_import_cost, 1):
                                for slot, factor in zip(window, factors):
                                    slot_power_required = max(round_trip_energy_required * 2000 * factor, 0)
                                    slot_charger_power_available = max(
                                        self.inverter.charger_power
                                        - pre_max["forced"].loc[slot]
                                        - pre_max["solar"].loc[slot],
                                        0,
                                    )

                                    slot_available_capacity = max(
                                        ((100 - pre_max["soc_end"].loc[slot]) / 100 * self.battery.capacity) * 2 * factor, 0
                                    )

                                    min_power = min(
                                        slot_power_required, slot_charger_power_available, slot_available_capacity
                                    )
                                    remaining_slot_capacity = slot_charger_power_available - min_power

                                    if remaining_slot_capacity < 10:
                                        available[slot] = False

                                    if log:
                                        str_log_x = (
                                            f">>> {i:3d} Slot: {slot.strftime(TIME_FORMAT)} Factor: {factor:0.3f} Forced: {pre_max['forced'].loc[slot]:6.0f}W  "
                                            + f"End SOC: {pre_max['soc_end'].loc[slot]:4.1f}%  SPR: {slot_power_required:6.0f}W  "
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

                                df_flows, nc = self._calc(prices, initial_soc, df_solar_and_consumption, slots, contract)
                                net_cost.append(nc)
                                slot_count.append(len(factors))
                                str_log += f"New SOC: {df.loc[start_window]['soc']:5.1f}%->{df.loc[start_window]['soc_end']:5.1f}% "
                                net_cost_opt = net_cost[-1]
                                str_log += f"Net: {net_cost_opt:6.1f}"
                                if log:
                                    _LOGGER.info(str_log)
                            else:
                                available[max_slot] = False
                else:
                    done = True
            else:
                _LOGGER.info("No slots available")
                done = True

        df = pd.concat(
            [
                prices,
                consumption,
                self.flows(initial_soc, static_flows, slots=slots, **kwargs),
            ],
            axis=1,
        )
        net_cost_opt = round(contract.net_cost(df).sum(), 1)

        if base_cost - net_cost_opt <= self.host.get_config("pass_threshold_p"):
            if log:
                _LOGGER.info(
                    f"Charge net cost delta:  {base_cost - net_cost_opt:0.1f}p: < Pass Threshold ({self.host.get_config('pass_threshold_p'):0.1f}p) => Slots Excluded"
                )
            slots = plunge_slots
            net_cost_opt = base_cost
            df = pd.concat(
                [
                    prices,
                    self.flows(initial_soc, static_flows, slots=slots, **kwargs),
                ],
                axis=1,
            )

        slots_added = 999

        # Only do the rest if there is an export tariff:
        # _LOGGER.info(f"Sum of Export Prices = {prices['export'].sum()}")

        if prices["export"].sum() > 0:
            j = 0
        else:
            j = max_iters

        while (slots_added > 0) and (j < max_iters):
            slots_added = 0
            j += 1
            # No need to iterate if this is charge only
            if not discharge:
                j += max_iters

            # Check how many slots which aren't full are at an import price less than any export price:
            max_export_price = df[df["forced"] <= 0]["export"].max()
            if log:
                _LOGGER.info("")
                _LOGGER.info("Low Cost Charging")
                _LOGGER.info("------------------")
                _LOGGER.info("")

            net_cost_pre = net_cost_opt
            slots_pre = copy(slots)

            if log:
                _LOGGER.info(f"Max export price when there is no forced charge: {max_export_price:0.2f}p/kWh.")

            i = 0
            available = (
                (df["import"] < max_export_price) & (df["forced"] < self.inverter.charger_power) & (df["forced"] >= 0)
            )

            # _LOGGER.info(df["import"]<max_export_price)
            a0 = available.sum()
            if log:
                _LOGGER.info(f"{available.sum()} slots have an import price less than the max export price")
            done = available.sum() == 0

            if self.host.debug and "C" in self.host.debug_cat:
                _LOGGER.info(f"\n{df.to_string()}")

            while not done:
                x = (
                    df.loc[available]
                    .loc[df["import"] < max_export_price]
                    .loc[df["forced"] < self.inverter.charger_power]
                    .loc[df["forced"] >= 0]
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
                    # _LOGGER.info(f"{available.sum():>2d} Min import price {min_price:5.2f}p/kWh at {start_window.strftime(TIME_FORMAT)} {x.loc[start_window]['forced']:4.0f}W ")

                    # SVB changed so all times are in naive UTC
                    # I don't think factoring is necessary here, as self.initial_soc doesnt change partway through a slot.
                    # if Timenow_utc_naive > start_window.tz_localize(None) and (
                    #    Timenow_utc_naive < start_window.tz_localize(None) + pd.Timedelta(30, "minutes")
                    # ):
                    #    str_log += "* "
                    #    factor = (
                    #        (start_window.tz_localize(None) + pd.Timedelta(30, "minutes"))
                    #        - Timenow_utc_naive
                    #    ).total_seconds() / 1800
                    # else:
                    #    str_log += "  "
                    #    factor = 1

                    str_log += "  "
                    factor = 1

                    str_log += f"SOC: {x.loc[start_window]['soc']:5.1f}%->{x.loc[start_window]['soc_end']:5.1f}% "

                    if self.host.debug and "C" in self.host.debug_cat:
                        _LOGGER.info(
                            f"SOC (before modelling Forced Charge): {x.loc[start_window]['soc']:5.1f}%->{x.loc[start_window]['soc_end']:5.1f}% "
                        )

                    forced_charge = min(
                        min(self.battery.max_charge_power, self.inverter.charger_power)
                        - x["forced"].loc[start_window]
                        - x[cols["solar"]].loc[start_window],
                        ((100 - x["soc_end"].loc[start_window]) / 100 * self.battery.capacity) * 2 * factor,
                    )
                    if self.host.debug and "C" in self.host.debug_cat:
                        _LOGGER.info(f"Forced Charge = {forced_charge}")
                    slot = (
                        start_window,
                        forced_charge,
                    )

                    slots.append(slot)

                    df = pd.concat(
                        [
                            prices,
                            self.flows(initial_soc, static_flows, slots=slots, **kwargs),
                        ],
                        axis=1,
                    )

                    if self.host.debug and "F" in self.host.debug_cat:
                        _LOGGER.info("Df after flows called = ")
                        _LOGGER.info(f"\n{df.to_string()}")

                    net_cost = contract.net_cost(df).sum()

                    # _LOGGER.info(f"Net Cost: {net_cost:5.1f} ")
                    # _LOGGER.info(f"Net Cost Opt: {net_cost_opt:5.1f} ")

                    str_log += f"Net: {net_cost:5.1f} "
                    if net_cost < net_cost_opt - self.host.get_config("slot_threshold_p"):
                        str_log += (
                            f"New SOC: {df.loc[start_window]['soc']:5.1f}%->{df.loc[start_window]['soc_end']:5.1f}% "
                        )
                        str_log += f"Max export: {-df['grid'].min():0.0f}W "
                        net_cost_opt = net_cost
                        slots_added += 1
                        if log:
                            _LOGGER.info(str_log)
                    else:
                        # done = True
                        slots = slots[:-1]
                        df = pd.concat(
                            [
                                prices,
                                self.flows(initial_soc, static_flows, slots=slots, **kwargs),
                            ],
                            axis=1,
                        )

                    done = available.sum() == 0
                else:
                    done = True

            cost_delta = net_cost_opt - net_cost_pre
            str_log = f"Charge net cost delta:{(-cost_delta):5.1f}p"
            if cost_delta > -self.host.get_config("pass_threshold_p"):
                slots = slots_pre
                slots_added = 0
                net_cost_opt = net_cost_pre
                str_log += f": < Pass Threshold {self.host.get_config('pass_threshold_p'):0.1f}p => Slots Excluded"
            else:
                str_log += f": > Pass Threshold {self.host.get_config('pass_threshold_p'):0.1f}p => Slots Included"

            if log:
                _LOGGER.info("")
                _LOGGER.info(str_log)

            # -----------
            # Discharging
            # -----------
            if discharge:
                net_cost_pre = net_cost_opt
                slots_pre = copy(slots)
                slots_added_pre = slots_added
                net_cost_pre = net_cost_opt

                # Check how many slots which aren't full are at an export price less than any import price:
                min_import_price = df["import"].min()
                if log:
                    _LOGGER.info("")
                    _LOGGER.info("Forced Discharging")
                    _LOGGER.info("------------------")
                    _LOGGER.info("")

                i = 0
                available = (df["export"] > min_import_price) & (df["forced"] == 0)
                a0 = available.sum()
                if log:
                    _LOGGER.info(f"{available.sum()} slots have an export price greater than the min import price")
                done = available.sum() == 0

                # Reload Timenow into variables (makes sure all discharge processing on slots already started (partial slots) uses a consistent value)

                Timenow = pd.Timestamp.now(tz=self.tz)
                Timenow_utc = pd.Timestamp.now(tz="UTC")
                Timenow_utc_naive = pd.Timestamp.utcnow().tz_localize(None)

                z = df
                z.index = pd.to_datetime(z.index)
                z["start"] = z.index.tz_convert(self.tz)
                discharge_start_datetime = z["start"].iloc[0]

                # if log:
                #    _LOGGER.info(f"Timenow is {Timenow}, discharge_start_datetime is {discharge_start_datetime}")

                # Calculate how much of the slot is left
                # if Timenow > discharge_start_datetime:
                #    slot_amount_left = ((discharge_start_datetime + pd.Timedelta(30, "minutes") - Timenow).total_seconds()) / 1800

                # Create a multiplier that is the inverse of slot_amount_left
                # slot_left_multiplier_discharge = 1 / slot_amount_left

                # if (self.host.debug and "D" in self.host.debug_cat):
                #    if log:
                #        _LOGGER.info("")
                #        _LOGGER.info(f"Slot left = {slot_amount_left}, Time now = {pd.Timestamp.now(self.tz)}, Charge_start_datetime = {discharge_start_datetime}")

                while not done:
                    x = df[available].copy()
                    i += 1
                    done = i > a0
                    max_price = x["export"].max()

                    if len(x[x["export"] == max_price]) > 0:
                        # _LOGGER.info("Entered routine successfully")
                        start_window = x[x["export"] == max_price].index[0]
                        available.loc[start_window] = False
                        str_log = f"{available.sum():>2d} Max export price {max_price:5.2f}p/kWh at {start_window.strftime(TIME_FORMAT)} "

                        # Given that self.initial_soc does not change partway through a slot, factoring is not needed. Commenting out.
                        # if (Timenow_utc_naive > start_window.tz_localize(None)) and (Timenow_utc_naive < start_window.tz_localize(None) + pd.Timedelta(30, "minutes")
                        #   ):
                        #    str_log += "* "
                        #    factor = (
                        #        (start_window.tz_localize(None) + pd.Timedelta(30, "minutes")) - Timenow_utc_naive
                        #    ).total_seconds() / 1800
                        # else:
                        #    str_log += "  "
                        #    factor = 1

                        str_log += "  "
                        factor = 1

                        str_log += f"SOC: {x.loc[start_window]['soc']:5.1f}%->{x.loc[start_window]['soc_end']:5.1f}% "

                        slot = (
                            start_window,
                            -min(
                                min(self.battery.max_discharge_power, self.inverter.charger_power)
                                - x[kwargs.get("solar", "solar")].loc[start_window],
                                ((x["soc_end"].loc[start_window] - self.battery.max_dod) / 100 * self.battery.capacity)
                                * 2
                                * factor,
                            ),
                        )

                        slots.append(slot)

                        df = pd.concat(
                            [
                                prices,
                                self.flows(initial_soc, static_flows, slots=slots, **kwargs),
                            ],
                            axis=1,
                        )

                        if self.host.debug and "F" in self.host.debug_cat:
                            _LOGGER.info("Df after flows called = ")
                            _LOGGER.info(f"\n{df.to_string()}")

                        net_cost = contract.net_cost(df).sum()

                        # _LOGGER.info(f"Net Cost: {net_cost:5.1f} ")
                        # _LOGGER.info(f"Net Cost Opt: {net_cost_opt:5.1f} ")

                        str_log += f"Net: {net_cost:5.1f} "
                        if net_cost < net_cost_opt - self.host.get_config("slot_threshold_p"):
                            str_log += f"New SOC: {df.loc[start_window]['soc']:5.1f}%->{df.loc[start_window]['soc_end']:5.1f}% "
                            str_log += f"Max export: {-df['grid'].min():0.0f}W "
                            net_cost_opt = net_cost
                            slots_added += 1

                            if self.host.debug and "D" in self.host.debug_cat:
                                _LOGGER.info(str_log)
                        else:
                            # done = True
                            slots = slots[:-1]
                            df = pd.concat(
                                [
                                    prices,
                                    self.flows(initial_soc, static_flows, slots=slots, **kwargs),
                                ],
                                axis=1,
                            )
                            if self.host.debug and "D" in self.host.debug_cat:
                                _LOGGER.info(str_log)
                    else:
                        done = True

                cost_delta = net_cost_opt - net_cost_pre
                str_log = f"Discharge net cost delta:{(-cost_delta):5.1f}p"
                if cost_delta > -self.host.get_config("discharge_threshold_p"):
                    slots = slots_pre
                    slots_added = slots_added_pre
                    str_log += f": < Discharge threshold ({self.host.get_config('discharge_threshold_p'):0.1f}p) => Slots excluded"
                    net_cost_opt = net_cost_pre
                else:
                    str_log += f": > Discharge Threshold ({self.host.get_config('discharge_threshold_p'):0.1f}p) => Slots included"

                if log:
                    _LOGGER.info("")
                    _LOGGER.info(str_log)

            if log:
                _LOGGER.info(f"Iteration {j:2d}: Slots added: {slots_added:3d}")

        # if log:
        #    _LOGGER.info(f"df before final concat = ")
        #    _LOGGER.info(f"\n{df.to_string()}")
        #
        #    _LOGGER.info("Slots before final concat = ")
        #    temp = pd.DataFrame(slots)
        #    _LOGGER.info(f"\n{temp.to_string()}")

        df = pd.concat(
            [
                prices,
                self.flows(initial_soc, static_flows, slots=slots, **kwargs),
            ],
            axis=1,
        )

        # if log:
        #    _LOGGER.info(f"df after final concat = ")
        #    _LOGGER.info(f"\n{df.to_string()}")

        # If in a partial slot, remove the factor applied during SPR assignment so the inverter stays at a constant charge power all the way through the slot.

        # if slot_left_multiplier_charge > 6:
        #    slot_left_multiplier_charge = 6

        # if log:
        #    _LOGGER.info(f"Slot_left_multiplier_charge = {slot_left_multiplier_charge}")
        #    _LOGGER.info(f"Forced in current slot = {df['forced'].iloc[0]}")

        # if df["forced"].iloc[0] > 1:   # only apply to slots that are charging.
        #    df["forced"].iloc[0] = df["forced"].iloc[0] * slot_left_multiplier_charge

        # if log:
        #    _LOGGER.info(f"Forced after applying charge multiplier = {df['forced'].iloc[0]}")
        #    _LOGGER.info(f"\n{df.to_string()}")

        # If in a partial slot, remove the factor applied during SPR assignment so the inverter stays at a constant discharge power all the way through the slot.

        # if slot_left_multiplier_discharge > 6:
        #    slot_left_multiplier_discharge = 6

        # if log:
        #    _LOGGER.info(f"Slot_left_multiplier_discharge = {slot_left_multiplier_discharge}")
        #    _LOGGER.info(f"Forced in current slot = {df['forced'].iloc[0]}")

        # if df["forced"].iloc[0] < 0:   # only apply to slots that are discharging.
        #    df["forced"].iloc[0] = df["forced"].iloc[0] * slot_left_multiplier_discharge

        # if log:
        #    _LOGGER.info(f"Forced after applying discharge multiplier = {df['forced'].iloc[0]}")
        #    _LOGGER.info(f"\n{df.to_string()}")

        df.index = pd.to_datetime(df.index)

        if (not self.host.get_config("allow_cyclic")) and (len(slots) > 0) and discharge:
            if log:
                _LOGGER.info("")
                _LOGGER.info("Removing cyclic charge/discharge")
            a = df["forced"][df["forced"] != 0].to_dict()
            new_slots = [(k, a[k]) for k in a]

            revised_slots = []
            skip_flag = False
            for i, x in enumerate(zip(new_slots[:-1], new_slots[1:])):

                if (
                    (int(x[0][1]) == self.inverter.charger_power)
                    & (int(-x[1][1]) == self.inverter.inverter_power)
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

            df = pd.concat(
                [
                    prices,
                    self.flows(initial_soc, static_flows, slots=revised_slots, **kwargs),
                ],
                axis=1,
            )
            net_cost_opt_new = contract.net_cost(df).sum()
            if log:
                _LOGGER.info(f"  Net cost revised from {net_cost_opt:0.1f}p to {net_cost_opt_new:0.1f}p")
            slots = revised_slots
            df.index = pd.to_datetime(df.index)
        return df

    def _calc(
        self, prices: pd.DataFrame, initial_soc: float, df_solar_and_consumption: pd.DataFrame, forced_charge_slots = [], contract: Contract
    ):
        """
        There mus be a tidier way of doing this
        """
        df = pd.concat(
            [
                prices,
                df_solar_and_consumption["consumption"],
                self.flows(initial_soc, df_solar_and_consumption, forced_charge_slots=forced_charge_slots),
            ],
            axis=1,
        )
        net_cost = round(contract.net_cost(df).sum(), 1)
        return df, net_cost


class Contract:
    def prices(self, start: pd.Timestamp, end: pd.Timestamp):
        prices = pd.DataFrame()
        for direction in self.tariffs:
            if self.tariffs[direction] is not None:
                prices = pd.concat(
                    [prices, self.tariffs[direction].to_df(start=start, end=end)["unit"]],
                    axis=1,
                )
        return prices
