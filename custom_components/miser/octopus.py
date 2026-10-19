import logging

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

from .entity_tariff import extend_daily_pattern
from .utils import log_config_entry, redact_sensitive

_LOGGER = logging.getLogger(__name__)

# How often a Tariff re-fetches its rates. Octopus publishes Agile and Flux
# rates daily and changes fixed rates from time to time; fetching only at setup
# left Miser on stale prices until the next restart.
RATE_REFRESH_INTERVAL = pd.Timedelta(hours=1)
# Only rates overlapping this look-back are fetched (open-ended current rates
# are still returned), instead of the tariff's whole history.
RATE_LOOKBACK = pd.Timedelta(days=2)


def _utc(value) -> pd.Timestamp | None:
    if value is None:
        return None
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def _usable_rates(results: list[dict]) -> list[dict]:
    """Drop non-direct-debit duplicates; keep rows with no payment method."""
    return [x for x in results if x.get("payment_method") != "NON_DIRECT_DEBIT"]


def rates_to_series(rates: list[dict], index: pd.DatetimeIndex) -> pd.Series:
    """Price for each slot from Octopus rates, honouring valid_from AND valid_to.

    Each rate fills only its own [valid_from, valid_to) interval; an open-ended
    rate (valid_to None) runs to the end. Rates are applied oldest first so a
    newer rate overrides an older one. Slots no rate covers stay NaN.
    """
    series = pd.Series(index=index, dtype=float)
    for rate in sorted(rates, key=lambda x: _utc(x["valid_from"])):
        start = _utc(rate["valid_from"])
        end = _utc(rate.get("valid_to"))
        mask = index >= start
        if end is not None:
            mask &= index < end
        series[mask] = float(rate["value_inc_vat"])
    return series


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
                if state is None:
                    _LOGGER.debug(f"  - State unavailable for {entity.entity_id}")
                    continue
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


def current_tariff_code(agreements: list[dict], now: pd.Timestamp) -> str | None:
    """Return the tariff code of the agreement in force at `now`.

    Octopus lists agreements in no guaranteed order and includes zero-length
    ones (valid_from == valid_to) and future-dated ones for booked switches.
    Taking the latest start date could pick a switch that has not happened yet,
    or - when several agreements share a start - an arbitrary zero-length one.
    Prefer the agreement whose [valid_from, valid_to) contains now; if there is
    a gap, fall back to the most recently started one.
    """

    def _ts(value):
        if value is None:
            return None
        try:
            stamp = pd.Timestamp(value)
        except (TypeError, ValueError):
            return None
        return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")

    usable = []
    for agreement in agreements:
        code = agreement.get("tariff_code")
        start = _ts(agreement.get("valid_from"))
        end = _ts(agreement.get("valid_to"))
        if not code or start is None:
            continue
        if end is not None and end <= start:
            continue  # zero-length placeholder
        usable.append((start, end, code))

    in_force = [a for a in usable if a[0] <= now and (a[1] is None or now < a[1])]
    if in_force:
        return max(in_force, key=lambda a: a[0])[2]

    started = [a for a in usable if a[0] <= now]
    if started:
        return max(started, key=lambda a: a[0])[2]
    return None


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
    now = pd.Timestamp.now(tz="UTC")
    for mpan_data in mpans:
        tariff_code = current_tariff_code(mpan_data.get("agreements", []), now)
        if tariff_code is None:
            _LOGGER.warning("No usable tariff agreement for meter point; skipping")
            continue

        _LOGGER.debug(f"Retrieved current tariff code {tariff_code}")
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
        self._fetched_at: pd.Timestamp | None = None
        self.agile_predict = None

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

    async def _get_from_api(self, period_from: pd.Timestamp | None = None, **kwargs) -> None:
        if period_from is None:
            period_from = pd.Timestamp.now(tz="UTC").normalize() - RATE_LOOKBACK
        params = OCTOPUS_PRODUCT_PARAMS | {"period_from": _utc(period_from).strftime("%Y-%m-%dT%H:%M:%SZ")}

        if not self._export:
            url = OCTOPUS_RATE_URLS["fixed"].format(product=self.product, code=self.tariff_code)
            data = await fetch_api_data(url=url, params=params)
            self.fixed = _usable_rates(data["results"])

        if self.is_eco7:
            url = OCTOPUS_RATE_URLS["day"].format(product=self.product, code=self.tariff_code)
            data = await fetch_api_data(url=url, params=params)
            self.day = _usable_rates(data["results"])

            url = OCTOPUS_RATE_URLS["night"].format(product=self.product, code=self.tariff_code)
            data = await fetch_api_data(url=url, params=params)
            self.night = _usable_rates(data["results"])
            self.unit = self.day

        else:
            url = OCTOPUS_RATE_URLS["unit"].format(product=self.product, code=self.tariff_code)
            data = await fetch_api_data(url=url, params=params)
            self.unit = _usable_rates(data["results"])

        self._fetched_at = pd.Timestamp.now(tz="UTC")

    async def _refresh_if_due(self) -> None:
        """Re-fetch rates periodically; keep the last good rates if that fails."""
        if self._fetched_at is not None and pd.Timestamp.now(tz="UTC") - self._fetched_at < RATE_REFRESH_INTERVAL:
            return
        try:
            await self._get_from_api()
        except Exception as err:  # noqa: BLE001 - fall back to the rates we have
            if self._fetched_at is None:
                raise
            _LOGGER.warning("Unable to refresh %s rates; using rates from %s: %s", self.tariff_code, self._fetched_at, err)

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
        await self._refresh_if_due()

        now = pd.Timestamp.now(tz="UTC")
        start = _utc(start) if start is not None else now.floor("30min")
        end = _utc(end) if end is not None else now.ceil("30min")
        index = pd.date_range(start.floor("30min"), end, freq="30min")

        if self.is_eco7:
            day = rates_to_series(self.day, index)
            night = rates_to_series(self.night, index)
            night_start = self._eco7_start.time()
            night_end = (self._eco7_start + pd.Timedelta(7, "hours")).time()
            in_night = (index.time >= night_start) & (index.time < night_end)
            unit = day.where(~in_night, night)
        else:
            unit = rates_to_series(self.unit, index)
            if self.is_agile and unit.isna().any():
                self.agile_predict = await self._get_agile_predict()
                if self.agile_predict is not None:
                    unit = unit.fillna(self.agile_predict.reindex(index))

        # Slots after the last published rate repeat the previous 24 hours.
        unit = extend_daily_pattern(unit).ffill().bfill()
        df = pd.DataFrame({"unit": unit})

        if not self._export:
            fixed = rates_to_series(self.fixed, index).ffill().bfill().fillna(0)
            # Standing charge (p/day) is booked once a day, at midnight UTC.
            fixed[index.time != pd.Timestamp("00:00").time()] = 0
            df["fixed"] = fixed

        return df

    async def _get_agile_predict(self):
        url = f"{AGILE_PREDICT_URL}{self.dno_region}?days=2&high_low=false"
        data = await fetch_api_data(url=url)

        df = pd.DataFrame(data[0]["prices"]).set_index("date_time")
        df.index = pd.to_datetime(df.index, utc=True)

        return df["agile_pred"]
