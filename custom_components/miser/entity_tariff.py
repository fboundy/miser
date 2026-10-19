"""Tariffs whose prices come from Home Assistant entities.

Drop-in alternative to octopus.Tariff: exposes `tariff_code` (used to detect
configuration changes) and `to_df(start, end)` returning 30-minute slots with a
`unit` column in p/kWh (plus a zero `fixed` column for import), read fresh from
the entities on every call.

Supported entity shapes, in order of preference:
- a `rates` attribute: list of {start, end, value_inc_vat | value | price}
  (Octopus Energy integration `*_current_day_rates` / `*_next_day_rates`);
- `raw_today` / `raw_tomorrow` attributes with the same item shape (Nordpool);
- a numeric state, used as a flat price.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PRICE_UNIT_AUTO = "auto"
PRICE_UNIT_GBP = "gbp_per_kwh"
PRICE_UNIT_PENCE = "pence_per_kwh"
PRICE_UNITS = [PRICE_UNIT_AUTO, PRICE_UNIT_GBP, PRICE_UNIT_PENCE]

RATE_LIST_ATTRIBUTES = ["rates", "raw_today", "raw_tomorrow"]
START_KEYS = ["start", "valid_from", "from"]
END_KEYS = ["end", "valid_to", "till", "to"]
VALUE_KEYS = ["value_inc_vat", "value", "price"]
SLOT = pd.Timedelta(minutes=30)
# Below this, an auto-detected price is taken to be in pounds rather than pence.
GBP_DETECTION_LIMIT = 2.0


class EntityTariff:
    def __init__(
        self,
        hass: HomeAssistant,
        entity_ids: list[str],
        export: bool = False,
        price_unit: str = PRICE_UNIT_AUTO,
    ) -> None:
        self.hass = hass
        self.entity_ids = list(entity_ids)
        self._export = export
        self.price_unit = price_unit if price_unit in PRICE_UNITS else PRICE_UNIT_AUTO

    @property
    def tariff_code(self) -> str:
        return "entities:" + ",".join(self.entity_ids)

    def read_rates(self) -> tuple[list[tuple[pd.Timestamp, pd.Timestamp, float]], float | None]:
        """Return (rates as (start, end, p/kWh), flat price in p/kWh or None)."""
        rates: list[tuple[pd.Timestamp, pd.Timestamp, float]] = []
        flat: float | None = None
        for entity_id in self.entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None:
                _LOGGER.debug("Price entity %s not found", entity_id)
                continue
            entity_rates = _rates_from_attributes(state.attributes)
            scale = self._scale(state.attributes, [value for _, _, value in entity_rates] or [_number(state.state)])
            if entity_rates:
                rates.extend((start, end, value * scale) for start, end, value in entity_rates)
            elif flat is None:
                value = _number(state.state)
                if value is not None:
                    flat = value * scale
        return rates, flat

    async def to_df(self, start=None, end=None, **kwargs) -> pd.DataFrame:
        start = pd.Timestamp(start).tz_convert("UTC").floor("30min")
        end = pd.Timestamp(end).tz_convert("UTC")
        index = pd.date_range(start, end, freq="30min")

        rates, flat = self.read_rates()
        if not rates and flat is None:
            raise ValueError(f"No prices available from {', '.join(self.entity_ids)}")

        series = pd.Series(index=index, dtype=float)
        if rates:
            series = _fill_from_rates(index, rates)
            series = extend_daily_pattern(series)
            series = series.ffill().bfill()
        else:
            series[:] = flat

        df = pd.DataFrame({"unit": series})
        if not self._export:
            df["fixed"] = 0.0
        return df

    def _scale(self, attributes: dict[str, Any], values: list[float | None]) -> float:
        """Multiplier to convert this entity's prices to pence."""
        if self.price_unit == PRICE_UNIT_GBP:
            return 100.0
        if self.price_unit == PRICE_UNIT_PENCE:
            return 1.0

        unit = str(attributes.get("unit_of_measurement") or "").lower()
        if unit:
            if unit.startswith(("p/", "p ", "gbp/100", "pence", "c/", "cent")) or unit in ("p", "gbx/kwh"):
                return 1.0
            if any(token in unit for token in ("gbp", "£", "eur", "€", "usd", "$")):
                return 100.0

        known = [abs(v) for v in values if v is not None]
        if known and max(known) < GBP_DETECTION_LIMIT:
            return 100.0
        return 1.0


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _timestamp(value) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def _first(item: dict, keys: list[str]):
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _rates_from_attributes(attributes: dict[str, Any]) -> list[tuple[pd.Timestamp, pd.Timestamp, float]]:
    rates = []
    for attribute in RATE_LIST_ATTRIBUTES:
        items = attributes.get(attribute)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            start = _timestamp(_first(item, START_KEYS))
            end = _timestamp(_first(item, END_KEYS))
            value = _number(_first(item, VALUE_KEYS))
            if start is None or value is None:
                continue
            if end is None or end <= start:
                end = start + SLOT
            rates.append((start, end, value))
    return rates


def _fill_from_rates(index: pd.DatetimeIndex, rates) -> pd.Series:
    series = pd.Series(index=index, dtype=float)
    for start, end, value in sorted(rates, key=lambda rate: rate[0]):
        series[(index >= start) & (index < end)] = value
    return series


def extend_daily_pattern(series: pd.Series) -> pd.Series:
    """Fill slots after the last published price by repeating the previous 24 h."""
    known = series.dropna()
    if known.empty:
        return series
    last = known.index[-1]
    for slot in series.index[series.index > last]:
        if pd.isna(series[slot]):
            series[slot] = series.get(slot - pd.Timedelta(hours=24), float("nan"))
    return series
