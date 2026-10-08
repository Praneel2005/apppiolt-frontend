import pytest

from contracts.dates import previous_period, resolve

AS_OF = "2018-08-31"


@pytest.mark.parametrize(
    "preset, expected",
    [
        ("last_month", ("2018-07-01", "2018-07-31")),
        ("this_month", ("2018-08-01", "2018-08-31")),
        ("last_quarter", ("2018-04-01", "2018-06-30")),
        ("this_quarter", ("2018-07-01", "2018-08-31")),
        ("last_12_months", ("2017-09-01", "2018-08-31")),
        ("last_90_days", ("2018-06-03", "2018-08-31")),
        ("year_to_date", ("2018-01-01", "2018-08-31")),
        ("last_year", ("2017-01-01", "2017-12-31")),
        ("month:2017-11", ("2017-11-01", "2017-11-30")),
        ("quarter:2017Q4", ("2017-10-01", "2017-12-31")),
        ("year:2017", ("2017-01-01", "2017-12-31")),
        ("between:2018-01-05:2018-01-20", ("2018-01-05", "2018-01-20")),
    ],
)
def test_resolve(preset, expected):
    assert resolve(preset, AS_OF) == expected


def test_last_quarter_wraps_year():
    assert resolve("last_quarter", "2018-02-10") == ("2017-10-01", "2017-12-31")


def test_leap_february():
    assert resolve("month:2016-02", AS_OF) == ("2016-02-01", "2016-02-29")


def test_unknown_preset():
    with pytest.raises(ValueError):
        resolve("next_week", AS_OF)


def test_previous_period_calendar_aware():
    assert previous_period("2018-04-01", "2018-06-30") == ("2018-01-01", "2018-03-31")
    assert previous_period("2018-01-01", "2018-03-31") == ("2017-10-01", "2017-12-31")
    assert previous_period("2018-07-01", "2018-07-31") == ("2018-06-01", "2018-06-30")
    assert previous_period("2018-03-01", "2018-03-31") == ("2018-02-01", "2018-02-28")
    assert previous_period("2017-01-01", "2017-12-31") == ("2016-01-01", "2016-12-31")


def test_previous_period_rolling_same_length():
    assert previous_period("2018-06-03", "2018-08-31") == ("2018-03-05", "2018-06-02")
