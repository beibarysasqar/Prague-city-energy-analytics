"""End-to-end smoke test of the Streamlit app against the local warehouse (skipped without it)."""

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from app import data

pytestmark = pytest.mark.skipif(
    not data.warehouse_path().exists(), reason="no local warehouse (run make extract && make dbt)"
)

# AppTest resolves relative paths against this test file, so use an absolute one.
APP = str(data.PROJECT_ROOT / "app" / "streamlit_app.py")


@pytest.fixture
def app_test() -> AppTest:
    return AppTest.from_file(APP, default_timeout=120)


def test_default_view_renders_every_section(app_test: AppTest) -> None:
    at = app_test.run()
    assert not at.exception
    assert [h.value for h in at.header][:3] == [
        "Air quality and electricity prices",
        "What moves with pollution? Correlations by district",
        "Where is bicycle traffic growing?",
    ]
    assert len(at.metric) == 4 and all(m.value != "—" for m in at.metric)


@pytest.mark.parametrize(
    "pollutant, districts, period",
    [
        ("PM2_5", ["praha-8"], None),  # PM2.5 not measured in Praha 8 -> empty states
        ("NO2", [], (date(2026, 9, 28), date(2026, 10, 2))),  # short period
    ],
)
def test_sparse_selections_show_messages_not_errors(
    app_test: AppTest,
    pollutant: str,
    districts: list[str],
    period: tuple[date, date] | None,
) -> None:
    at = app_test.run()
    at.selectbox[0].set_value(pollutant).run()
    at.multiselect[0].set_value(districts).run()
    if period:
        at.date_input[0].set_value(period).run()
    assert not at.exception
    assert at.info  # at least one empty-state message instead of a broken chart
