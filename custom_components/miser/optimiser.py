import logging
import pandas as pd
from numpy import arange
from datetime import datetime, timedelta
from homeassistant.core import HomeAssistant
from homeassistant.components.recorder import history
from homeassistant.util.dt import parse_datetime as dt_util

from .const import (
    DOMAIN,
    DATETIME_FORMAT_LONG,
    MODEL_DURATION_HOURS,
    MODEL_PERIOD_MINUTES,
    DEFAULTS,
    CONSUMPTION_SHAPE,
)
from .utils import get_value, get_entity_for_key

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}")


async def optimise(hass: HomeAssistant, now=None):
    data = hass.data[DOMAIN]
    freq = pd.Timedelta(minutes=MODEL_PERIOD_MINUTES)
    # Access hass.data
    uuid = data["uuid"]
    model = data["model"]
    tz = model.tz

    str_log = f"optiMISER executed at: {datetime.now().strftime(DATETIME_FORMAT_LONG)}. UUID: {uuid}"
    _LOGGER.debug("*" * len(str_log))
    _LOGGER.debug(str_log)
    _LOGGER.debug("*" * len(str_log))

    start = pd.Timestamp.now(tz=tz).floor(freq)
    end = start.normalize() + pd.Timedelta(hours=MODEL_DURATION_HOURS)
    _LOGGER.debug(f"Model Start: {start.strftime(DATETIME_FORMAT_LONG)}")
    _LOGGER.debug(f"Model End  : {end.strftime(DATETIME_FORMAT_LONG)}")

    await _get_consumption(hass=hass, start=start, end=end, freq=freq)


async def _get_consumption(hass: HomeAssistant, start: pd.Timestamp, end: pd.Timestamp, freq: pd.Timedelta) -> bool:
    """
    Get consumption and save it to hass.data[DOMAIN]['consumption']

    ** Requires EV logic **
    """
    index = pd.date_range(start=start, end=end, freq=freq, inclusive="left")
    # Set up a template dataframe with just index and time of day
    consumption = pd.DataFrame(index=index)
    consumption["time_of_day"] = consumption.index.time
    consumption["dow_tod"] = consumption.index.day_of_week + consumption.index.hour / 24

    _LOGGER.debug("Consumption Template:")
    _LOGGER.debug(f"{consumption}")

    use_consumption = await get_value(hass, "USE_CONSUMPTION_HISTORY")
    if use_consumption:
        entity_id = get_entity_for_key(hass, "CONSUMPTION_TODAY")
        history_days = await get_value(hass, "HISTORY_DAYS")
        load_margin = await get_value(hass, "LOAD_MARGIN")
        weekday_weighting = await get_value(hass, "WEEKDAY_WEIGHTING")
        if entity_id is not None:
            _LOGGER.debug(f"Loading {history_days} days consumption history from {entity_id}")
            consumption_history = await _get_hass_power_from_daily_kwh(hass, entity_id, history_days, freq=freq)

            # Add consumption margin
            consumption_history = consumption_history * (1 + load_margin / 100)

            # Group by time, take the mean and merge with the template
            consumption_by_time = consumption_history.groupby(consumption_history.index.time).mean().rename("mean")
            consumption = consumption.merge(consumption_by_time, "left", left_on="time_of_day", right_index=True)

            if history_days >= 7:
                consumption_dow = consumption_history.set_axis(
                    consumption_history.index.day_of_week + consumption_history.index.hour / 24
                )
                consumption_dow = consumption_dow.groupby(consumption_dow.index).mean().rename("dow")
                consumption = consumption.merge(consumption_dow, "left", left_on="dow_tod", right_index=True)
                consumption["final"] = consumption["mean"] * (1 - weekday_weighting / 100) + consumption["dow"] * (
                    weekday_weighting / 100
                )
                _LOGGER.debug(f"{consumption.to_string()}")

            else:
                _LOGGER.debug(
                    f"  - Ignoring 'Day of Week Weighting' because only {history_days} days of history is available"
                )
                consumption["final"] = consumption["mean"]

    if "final" not in consumption.columns:
        # Need to add config entities for manual case
        daily_consumption = await get_value(hass, "DAILY_CONSUMPTION_KWH")
        if get_value("SHAPE_CONSUMPTION"):
            daily = (
                pd.DataFrame(CONSUMPTION_SHAPE)
                .set_index("hours")
                .reindex(arange(0, 24.5, 0.5))
                .interpolate()
                .iloc[:-1]
            )
            daily["final"] = daily["consumption"] * daily_consumption / (daily["consumption"].sum() / 2000)
            daily.index = pd.to_datetime(daily.index, unit="h").time
            consumption = consumption.merge(daily, left_on="time_of_day", right_index=True)

        else:
            consumption["final"] = daily_consumption / 24

    hass.data[DOMAIN]["consumption"] = consumption["final"]

    return True


async def _get_hass_power_from_daily_kwh(hass, entity_id, days=DEFAULTS["HISTORY_DAYS"], freq=pd.Timedelta("30min")):
    df = await _hass_to_df(hass, entity_id, days=days)
    if df is not None:
        x = df.diff().clip(0).fillna(0).cumsum() + df.iloc[0]
        x.index = x.index.round("1s")
        x = x[~x.index.duplicated()]
        y = -pd.concat([x.resample("1s").interpolate().resample(freq).asfreq(), x.iloc[-1:]]).diff(-1)
        dt = y.index.diff().total_seconds() / pd.Timedelta("60min").total_seconds() / 1000
        df = y[1:-1] / dt[2:]

    return df


async def _hass_to_df(
    hass: HomeAssistant,
    entity_id: str,
    end_time: datetime = None,
    days: int = 1,
    start_time: datetime = None,
) -> list:
    """Fetch the state history for an entity."""
    # Specify a time range (optional)
    end_time = end_time or pd.Timestamp.now(tz="UTC")
    start_time = start_time or end_time - pd.Timedelta(hours=days * 24)

    # Get history data
    states = await hass.async_add_executor_job(
        history.get_significant_states,
        hass,
        start_time,
        end_time,
        [entity_id],
    )

    states = states.get(entity_id, [])

    # Return state history for the specified entity
    df = pd.Series(index=[state.last_updated for state in states], data=[state.state for state in states])
    df.index = pd.to_datetime(df.index)
    df = pd.to_numeric(df, errors="coerce").dropna()
    return df
