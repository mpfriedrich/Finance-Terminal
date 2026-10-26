"""NYSE hours: the 13:00 early closes and the special closures (audit 24.10.2026,
low item 10), and the forward books' close time built on them."""

import datetime as dt

from portfolio_lab import nyse
from portfolio_lab.forward import engine as en


def test_early_closes_of_2026_and_2027_end_at_one():
    for d in (dt.date(2026, 11, 27), dt.date(2026, 12, 24), dt.date(2027, 11, 26)):
        assert nyse.is_session(d) and nyse.close_hour(d) == 13
    assert nyse.close_hour(dt.date(2026, 11, 25)) == 16
    # 27.11.2026, 13:00 New York (EST, UTC-5) is 18:00 UTC; a normal day 21:00 UTC
    assert en.close_time(dt.date(2026, 11, 27)) == dt.datetime(2026, 11, 27, 18)
    assert en.close_time(dt.date(2026, 11, 25)) == dt.datetime(2026, 11, 25, 21)


def test_holidays_of_2027_and_special_closures_are_not_sessions():
    for d in (dt.date(2027, 3, 26), dt.date(2027, 6, 18), dt.date(2027, 7, 5),
              dt.date(2027, 12, 24), dt.date(2025, 1, 9), dt.date(2018, 12, 5)):
        assert not nyse.is_session(d), d
    assert nyse.next_session(dt.date(2025, 1, 8)) == dt.date(2025, 1, 10)
    assert nyse.prev_session(dt.date(2027, 7, 6)) == dt.date(2027, 7, 2)
    assert nyse.sessions_from(dt.date(2027, 12, 23), 2) == [dt.date(2027, 12, 23),
                                                             dt.date(2027, 12, 27)]
