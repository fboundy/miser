import logging
from datetime import datetime

import aiohttp
import pandas as pd
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    IMPORT_EXPORT,
    OCTOPUS_ACCOUNT_URL,
    OCTOPUS_PRODUCT_PARAMS,
    OCTOPUS_RATE_URLS,
    AGILE_PREDICT_URL,
)

from .utils import log_config_entry, redact_sensitive

_LOGGER = logging.getLogger(__name__)


def _oct_time(self, d):
    # print(d)
    return datetime(
        year=pd.Timestamp(d).year,
        month=pd.Timestamp(d).month,
        day=pd.Timestamp(d).day,
    )


async def fetch_api_data(url: str, params: dict = None, api_key: str = None) -> dict:
    """
    Perform an asynchronous HTTP GET request.

    Args:
        url (str): The API endpoint URL.
        params (dict): Optional query parameters for the request.
        api_key (str): Optional API key for authentication.

    Returns:
        dict: The parsed JSON response from the API.

    Raises:
        Exception: If the request fails or the response cannot be parsed.
    """
    auth = aiohttp.BasicAuth(api_key, "") if api_key else None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, auth=auth) as response:
                _LOGGER.debug(f"Request URL: {response.url}")
                response.raise_for_status()  # Raise an error for HTTP errors
                data = await response.json()  # Parse JSON response
                _LOGGER.debug(f"Response Data: {redact_sensitive(data)}")
                return data
    except Exception as e:
        _LOGGER.error(f"Error fetching data from {url}: {e}")
        raise


async def get_octopus_integration_data(hass: HomeAssistant) -> dict:
    """
    Interrogate the Octopus Energy intergration and extract as much data as possible

    # Need to add:

    - Get intelligent entities and save to hass.data[DOMAIN]
    - Get saver event entities and save to hass.data[DOMAIN]

    """

    config_entries = hass.config_entries.async_entries("octopus_energy")

    # Log ConfigEntry contents
    if config_entries:
        _LOGGER.debug("Octopus Energy integration:")
        entry = config_entries[0]
        log_config_entry(entry)

        config_keys = ["account_id", "api_key"]
        octopus_info = {key: entry.data.get(key, None) for key in config_keys}

        entity_registry = er.async_get(hass=hass)
        octopus_entities = [
            entity for entity in entity_registry.entities.values() if entity.config_entry_id == entry.entry_id
        ]

        current_day_entities = [entity for entity in octopus_entities if "current_day_rates" in entity.entity_id]
        entity_keys = ["mpan", "serial_number", "tariff_code"]
        for key in entity_keys:
            octopus_info[key] = {direction: {} for direction in IMPORT_EXPORT}

            for entity in current_day_entities:
                state = hass.states.get(entity.entity_id)
                _LOGGER.debug(f"{entity.entity_id}")
                if "xport" in state.attributes.get("friendly_name", ""):
                    direction = "export"
                else:
                    direction = "import"
                octopus_info[key][direction] = state.attributes.get(key, None)
            if all([octopus_info[key][direction] is None for direction in IMPORT_EXPORT]):
                octopus_info[key] = None

        all_keys = config_keys + entity_keys

        if any([octopus_info[key] is None for key in all_keys]):
            _LOGGER.debug("  Incomplete Octopus data")
            return None
        else:
            _LOGGER.debug(f"  Octopus Data: {redact_sensitive(octopus_info)}")
            return octopus_info

    else:
        _LOGGER.debug("  No config entries found")


async def get_octopus_info_from_account(hass: HomeAssistant, account_id: str, api_key: str) -> dict:
    _LOGGER.debug("Loading Octopus account data")
    if any([account_id is None, api_key is None]):
        _LOGGER.error("Unable to get Octopus account data")
        return None

    octopus_info = {"account_id": account_id, "api_key": api_key}

    url = f"{OCTOPUS_ACCOUNT_URL}/{account_id}/"
    _LOGGER.debug(f"Connecting to {url}")

    data = await fetch_api_data(url=url, api_key=api_key)
    _LOGGER.debug(redact_sensitive(data))

    mpans = data["properties"][0]["electricity_meter_points"]
    # for mpan in mpans:
    #     redact_patterns.append(mpan["mpan"])

    entity_keys = ["mpan", "serial_number", "tariff_code"]
    octopus_info = octopus_info | {key: {direction: {} for direction in IMPORT_EXPORT} for key in entity_keys}
    for mpan_data in mpans:
        df = pd.DataFrame(mpan_data["agreements"])
        df = df.set_index("valid_from")
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        tariff_code = df["tariff_code"].iloc[-1]

        _LOGGER.debug(f"Retrieved most recent tariff code {tariff_code}")
        if mpan_data["is_export"]:
            direction = "export"
        else:
            direction = "import"

        octopus_info["mpan"][direction] = mpan_data["mpan"]
        octopus_info["tariff_code"][direction] = tariff_code
        octopus_info["serial_number"][direction] = mpan_data["meters"][0]["serial_number"]

    return octopus_info


class Tariff:
    def __init__(
        self,
        tariff_code,
        export=False,
        fixed=0,
        unit=0,
        valid_from=pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(hours=24),
        day=None,
        night=None,
        eco7_start="01:00",
        tz="GB",
        manual=False,
        **kwargs,
    ) -> None:

        self._tariff_code = tariff_code
        self._tz = "GB"
        self._export = export
        self._area = kwargs.get("area", None)
        self._eco7_start = pd.Timestamp(eco7_start, tz="UTC")
        self._manual = manual

    @classmethod
    async def create(cls, tariff_code, **kwargs):
        """
        Factory method to asynchronously create an instance of Tariff.
        """
        instance = cls(tariff_code, **kwargs)
        await instance._get_from_api()  # Call the async method
        return instance

        # self.host.io_prices = {}

    @property
    def tariff_code(self):
        return self._tariff_code

    @property
    def product(self):
        return self._tariff_code[5:-2]

    @property
    def is_eco7(self):
        return self._tariff_code[:4] == "E-2R"

    @property
    def is_agile(self):
        return "AGILE" in self._tariff_code

    @property
    def is_intelligent(self):
        return "INTELLI" in self._tariff_code and not self._export

    @property
    def manual(self):
        return self._manual

    @property
    def dno_region(self):
        return self._tariff_code[-1]

        # if "INTELLI" in name and not self._export:
        #     if self.host.get_config("octopus_auto"):
        #         try:
        #             self.log(f"    Trying to find Octopus Intelligent Entities from Octopus Energy Integration:")
        #             self.host.octopus_import_entity = [
        #                 name
        #                 for name in self.host.get_state_retry(BOTTLECAP_DAVE["domain"]).keys()
        #                 if (
        #                     "octopus_energy_electricity" in name
        #                     and BOTTLECAP_DAVE["rates"] in name
        #                     and not "export" in name
        #                 )
        #             ]
        #             self.rlog(f"      Octopus Intelligent Import Entity found: {self.host.octopus_import_entity}")

        #             self.host.io_prices = self.host.get_io_tariffs(self.host.octopus_import_entity[0])
        #             # self.host.io_prices = self.host.get_io_tariffs(self.host.octopus_import_entity)        # Error forcing: failure to load prices

        #         except Exception as e:
        #             self.log(f"{e.__traceback__.tb_lineno}: {e}", level="ERROR")
        #             self.log(
        #                 "Failed to find Octopus Intellgient tariffs from Octopus Energy Integration, extra IO slots will not be loaded",
        #                 level="WARNING",
        #             )

    async def _get_from_api(self, period_from: pd.Timestamp | None = None, **kwargs) -> dict:
        params = OCTOPUS_PRODUCT_PARAMS | {
            k: _oct_time(kwargs.get(k, None)) for k in ["period_from", "period_to"] if kwargs.get(k, None) is not None
        }

        if not self._export:
            url = OCTOPUS_RATE_URLS["fixed"].format(product=self.product, code=self.tariff_code)
            data = await fetch_api_data(url=url, params=params)
            self.fixed = [x for x in data["results"] if x["payment_method"] != "NON_DIRECT_DEBIT"]

        if self.is_eco7:
            url = OCTOPUS_RATE_URLS["day"].format(product=self.product, code=self.tariff_code)
            data = await fetch_api_data(url=url, params=params)
            self.day = [x for x in data["results"] if x["payment_method"] == "DIRECT_DEBIT"]

            url = OCTOPUS_RATE_URLS["night"].format(product=self.product, code=self.tariff_code)
            data = await fetch_api_data(url=url, params=params)
            self.night = [x for x in data["results"] if x["payment_method"] == "DIRECT_DEBIT"]
            self.unit = self.day

        else:
            url = OCTOPUS_RATE_URLS["unit"].format(product=self.product, code=self.tariff_code)
            data = await fetch_api_data(url=url, params=params)
            self.unit = data["results"]

    @property
    def start(self):
        if self.manual:
            return pd.Timestamp("2020-01-01", tz=self.tz)
        else:
            return min([pd.Timestamp(x["valid_from"]) for x in self.unit])

    @property
    def end(self):
        if self.manual:
            return pd.Timestamp.now(tz=self.tz)
        else:
            return max([pd.Timestamp(x["valid_to"]) for x in self.unit])

    async def to_df(self, start=None, end=None, **kwargs):
        time_now = pd.Timestamp.now(tz="UTC")
        if start is None:
            if self.is_eco7:
                start = min([pd.Timestamp(x["valid_from"]) for x in self.day])

            # elif self.manual:
            #     start = pd.Timestamp.now(tz=self.tz).floor("1D")

            else:
                start = min([pd.Timestamp(x["valid_from"]) for x in self.unit])

        if end is None:
            end = pd.Timestamp.now(tz=start.tzinfo).ceil("30min")

        # use_day_ahead = kwargs.get("day_ahead", ((start > time_now) or (end > time_now)))

        if self.is_eco7:
            df = pd.concat(
                [pd.DataFrame(x).set_index("valid_from")["value_inc_vat"] for x in [self.day, self.night]],
                axis=1,
            ).set_axis(["unit", "Night"], axis=1)
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            df = df.reindex(
                index=pd.date_range(
                    min([pd.Timestamp(x["valid_from"]) for x in self.day]),
                    end,
                    freq="30min",
                )
            ).ffill()
            mask = (df.index.time >= self._eco7_start.time()) & (
                df.index.time < (self._eco7_start + pd.Timedelta(7, "hours")).time()
            )
            df.loc[mask, "unit"] = df.loc[mask, "Night"]
            df = df["unit"].loc[start:end]

        # elif self.manual:
        #     df = (
        #         pd.concat(
        #             [
        #                 pd.DataFrame(
        #                     index=[midnight + pd.Timedelta(f"{x['period_start']}:00") for x in self.unit],
        #                     data=[{"unit": x["price"]} for x in self.unit],
        #                 ).sort_index()
        #                 for midnight in pd.date_range(
        #                     start.floor("1D") - pd.Timedelta("1D"),
        #                     end.ceil("1D"),
        #                     freq="1D",
        #                 )
        #             ]
        #         )
        #         .resample("30min")
        #         .ffill()
        #         .loc[start:end]
        #     )

        else:
            df = pd.DataFrame(self.unit).set_index("valid_from")["value_inc_vat"]
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            if self.is_agile:
                self.agile_predict = await self._get_agile_predict()

                if self.agile_predict is not None:
                    df = pd.concat(
                        [
                            df,
                            self.agile_predict.loc[df.index[-1] + pd.Timedelta("30min") : end],
                        ]
                    )

            # If the index frequency >30 minutes so we need to just extend it:
            if (len(df) > 1 and ((df.index[-1] - df.index[-2]).total_seconds() / 60) > 30) or len(df) == 1:
                newindex = pd.date_range(df.index[0], end, freq="30min")
                df = df.reindex(index=newindex).ffill().loc[start:]
            else:
                i = 0
                while df.index[-1] < end and i < 7:
                    i += 1
                    extended_index = pd.date_range(
                        df.index[-1] + pd.Timedelta(30, "minutes"),
                        df.index[-1] + pd.Timedelta(24, "hours"),
                        freq="30min",
                    )
                    dfx = pd.concat([df, pd.DataFrame(index=extended_index)]).shift(48).loc[extended_index[0] :]
                    df = pd.concat([df, dfx])
                    df = df[df.columns[0]]
                df = df.loc[start:end]
            df.name = "unit"

            # # SVB logging
            # # self.log("")
            # # self.log("Printin df just before concat.....")
            # # self.log(df.to_string())

            # # SVB #
            # # It is at this point that df now looks like the Dataframe that compare_tariffs loads. This is the point
            # # to overwrite the Df with IOG data from the BottlecapDave integration, loaded in pv_opt.py and passed in here via self.host.io_prices.
            # # (SVB Note: io_prices should be passed in via Class, but I cannot figure out the structure of Tariff and Contract Classes to do this)

            # if len(self.host.io_prices) > 0:
            #     # Add IO slot prices as a column to dataframe.
            #     df = pd.concat([df, self.host.io_prices], axis=1).set_axis(["unit", "io_unit"], axis=1)

            #     df = df.dropna(subset=["unit"])  # Drop Nans
            #     mask = df["io_unit"] < df["unit"]  # Mask is true if an IOslot
            #     df.loc[mask, "unit"] = df[
            #         "io_unit"
            #     ]  # Overwrite unit (prices from website) with io_unit (prices from OE integration) if in an IOslot.
            #     df = df.drop(["io_unit"], axis=1)  # remove IO prices column

            #     # self.log("To_df, Printing result")
            #     # self.log(df.to_string())

        # Add a column "fixed" for the standing charge.
        if not self._export:
            if not self.manual:
                x = pd.DataFrame(self.fixed).set_index("valid_from")["value_inc_vat"].sort_index()
                x.index = pd.to_datetime(x.index)
                newindex = pd.date_range(x.index[0], df.index[-1], freq="30min")
                x = x.reindex(newindex).sort_index()
                x = x.ffill().loc[df.index[0] :]
            else:
                x = pd.DataFrame(index=df.index, data={"fixed": self.fixed})

            df = pd.concat([df, x], axis=1).set_axis(["unit", "fixed"], axis=1)
            mask = df.index.time != pd.Timestamp("00:00", tz="UTC").time()
            df.loc[mask, "fixed"] = 0

        df = pd.DataFrame(df)
        # # SVB logging
        # # self.log("")
        # # self.log("Printing final result of to_df.....")
        # # self.log(df.to_string())

        # # Update for Octopus Savings Events if they exists
        # if (self.host is not None) and ("unit" in df.columns):
        #     events = self.host.saving_events
        #     for id in events:
        #         event_start = pd.Timestamp(events[id]["start"]).floor("30min")
        #         event_end = pd.Timestamp(events[id]["end"]).ceil("30min")
        #         event_value = int(events[id]["octopoints_per_kwh"]) / 8

        #         if event_start <= end or event_end > start and event_value > 0:
        #             event_start = max(event_start, start)
        #             event_end = min(event_end - pd.Timedelta(30, "minutes"), end)
        #             df["unit"].loc[event_start:event_end] += event_value

        return df

    async def _get_agile_predict(self):
        url = f"{AGILE_PREDICT_URL}{self.dno_region}?days=2&high_low=false"
        data = await fetch_api_data(url=url)

        df = pd.DataFrame(data[0]["prices"]).set_index("date_time")
        df.index = pd.to_datetime(df.index, utc=True)

        return df["agile_pred"]
