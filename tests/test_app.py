"""THE integration test: boots app.py headless, (1) with whatever is in the repo and (2) with a test-only grid
pointed at a temporary folder (nothing is written to data/ or out/). Must pass before every push."""
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from src import contracts as c
from src.contracts import ROOT
from tests.fixtures import fixture_grid

APP = str(ROOT / "app.py")


def boot():
    st.cache_data.clear()
    return AppTest.from_file(APP, default_timeout=90).run()


def test_app_boots_with_repo_contents():
    at = boot()
    assert not at.exception, at.exception
    assert len(at.tabs) == 6
    assert not at.error, [e.value for e in at.error]


def test_app_survives_slider_moves():
    at = boot()
    at.slider(key="it_mw").set_value(2000).run()
    at.slider(key="carbon_price_usd_t").set_value(50).run()
    assert not at.exception, at.exception


@pytest.fixture
def temp_grid(tmp_path, monkeypatch):
    if c.ready("grid_hourly"):
        yield; return
    path = tmp_path / "grid_hourly.parquet"
    fixture_grid().to_parquet(path, index=False)
    monkeypatch.setitem(c.SCHEMAS["grid_hourly"], "path", path)
    yield


def test_plan_the_week_works_with_a_grid(temp_grid):
    at = boot()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    at.slider(key="week_hour").set_value(15).run()
    at.radio(key="week_mode").set_value("Coming week (live)").run()
    assert not at.exception and not at.error
