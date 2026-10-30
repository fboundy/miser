"""optimise() runs one at a time; triggers that land mid-run collapse into one re-run.

Regression: a config change and the timer started two runs on the shared model
and they crashed each other (KeyError in forced(), IndexingError in discharging()).
"""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.miser import optimiser
from custom_components.miser.const import DOMAIN


@pytest.mark.asyncio
async def test_overlapping_triggers_run_one_after_another(monkeypatch) -> None:
    hass = SimpleNamespace(data={DOMAIN: {"uuid": "test"}})
    running = 0
    overlaps = 0
    runs = 0
    release = asyncio.Event()

    async def fake_optimise(_hass):
        nonlocal running, overlaps, runs
        running += 1
        overlaps += running > 1
        runs += 1
        if runs == 1:
            await release.wait()
        running -= 1

    monkeypatch.setattr(optimiser, "_optimise", fake_optimise)

    first = asyncio.create_task(optimiser.optimise(hass))
    await asyncio.sleep(0)
    # Three triggers while the first run is busy...
    await asyncio.gather(*(optimiser.optimise(hass) for _ in range(3)))
    release.set()
    await first

    assert overlaps == 0
    # ...become exactly one re-run.
    assert runs == 2
    assert not hass.data[DOMAIN]["optimise_lock"].locked()


@pytest.mark.asyncio
async def test_failed_run_releases_the_lock(monkeypatch) -> None:
    hass = SimpleNamespace(data={DOMAIN: {"uuid": "test"}})

    async def boom(_hass):
        raise RuntimeError("model error")

    monkeypatch.setattr(optimiser, "_optimise", boom)
    with pytest.raises(RuntimeError):
        await optimiser.optimise(hass)
    assert not hass.data[DOMAIN]["optimise_lock"].locked()
