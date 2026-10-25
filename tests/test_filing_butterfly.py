"""SEC Filing Butterfly tracker: dates, four contracts, two budgets, FX, fees, missing
quotes, duplicates, bookkeeping and restarts. Hand-computed values, no network."""

import datetime as dt

import duckdb
import pandas as pd
import pytest

from portfolio_lab import filing_butterfly as fb

UTC = dt.UTC
FILED = dt.date(2026, 10, 20)          # a Tuesday
ENTRY = dt.date(2026, 10, 21)          # the first session after it
EXPIRIES = ["2026-11-13", "2026-11-20", "2026-11-27", "2026-12-18"]


def at(day: dt.date, hour_et: int = 11) -> dt.datetime:
    """`hour_et` o'clock New York time on `day`, as an aware UTC datetime."""
    return dt.datetime(day.year, day.month, day.day, hour_et, tzinfo=fb.ET).astimezone(UTC)


def index_text(rows) -> str:
    head = ("Form Type   Company Name                                                  CIK"
            "         Date Filed  File Name\n" + "-" * 120 + "\n")
    body = "".join(f"{form:<12}{name:<62}{cik:<12}{filed:%Y%m%d}    "
                   f"edgar/data/{cik}/{acc}.txt\n" for form, name, cik, filed, acc in rows)
    return head + body


def calls_frame(quotes: dict, ticker: str = "AAA", expiry: str = "2026-11-20",
                oi: float = 1000, volume: float = 50,
                last_trade: str = "2026-10-21T14:00:00Z") -> pd.DataFrame:
    yymmdd = dt.date.fromisoformat(expiry).strftime("%y%m%d")
    rows = []
    for k, (bid, ask) in quotes.items():
        rows.append({"contractSymbol": f"{ticker}{yymmdd}C{int(k * 1000):08d}",
                     "strike": float(k), "bid": bid, "ask": ask, "lastPrice": (bid + ask) / 2,
                     "volume": volume, "openInterest": oi, "lastTradeDate": last_trade,
                     "impliedVolatility": 0.3, "contractSize": "REGULAR"})
    return pd.DataFrame(rows)


OPEN_QUOTES = {80: (20.8, 21.0), 90: (11.0, 11.2), 100: (4.0, 4.2), 110: (1.0, 1.1),
               120: (0.2, 0.3)}


class FakeLive:
    def __init__(self):
        self.index = {FILED: index_text([("10-Q", "AAA CORP", 111, FILED,
                                          "0000000111-26-000001")])}
        self.quotes = OPEN_QUOTES
        self.calls_kwargs = {}
        self.fx_rate = 1.10
        self.ex_div = None
        self.spot_px = 100.0
        self.stock_close = None

    def form_index(self, day):
        return self.index.get(day)

    def expiries(self, symbol):
        return EXPIRIES

    def calls(self, symbol, expiry):
        return calls_frame(self.quotes, symbol, expiry, **self.calls_kwargs)

    def spot(self, symbol):
        return self.spot_px

    def fx(self):
        return self.fx_rate

    def ex_dividend(self, symbol):
        return self.ex_div

    def history(self, symbol, start):
        days = pd.bdate_range("2026-10-16", "2026-11-30").date
        if symbol == "SPY":
            return pd.Series([500.0 + i for i in range(len(days))], index=days)
        return pd.Series([1.10] * len(days), index=days)

    def close_on(self, symbol, day):
        return self.stock_close


@pytest.fixture
def market():
    con = duckdb.connect()
    con.execute("CREATE TABLE securities (cik BIGINT, ticker VARCHAR)")
    con.execute("CREATE TABLE prices (ticker VARCHAR, date DATE, open DOUBLE, close DOUBLE, "
                "close_adj DOUBLE, close_raw DOUBLE, volume BIGINT)")
    con.execute("INSERT INTO securities VALUES (111, 'AAA'), (111, 'AAA-B'), (222, 'BBB')")
    days = pd.bdate_range(end="2026-10-20", periods=300).date
    for t, vol in (("AAA", 1_000_000), ("AAA-B", 1_000), ("BBB", 1_000_000)):
        con.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?)",
                        [(t, d, 100.0, 100.0, 100.0, 100.0, vol) for d in days])
    con.execute("INSERT INTO prices VALUES ('EURUSD=X', '2026-10-20', 1.1, 1.1, 1.1, 1.1, 0)")
    return con


@pytest.fixture
def path(tmp_path):
    return tmp_path / "paper_options.duckdb"


def read(path, sql, params=()):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql, list(params)).fetch_df()
    finally:
        con.close()


# -- calendar and EDGAR -------------------------------------------------------------------

def test_nyse_calendar_knows_the_2026_holidays():
    h = fb.nyse_holidays(2026)
    for d in ("2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
              "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"):
        assert dt.date.fromisoformat(d) in h
    assert fb.next_session(dt.date(2026, 11, 25)) == dt.date(2026, 11, 27)
    assert fb.next_session(dt.date(2026, 10, 23)) == dt.date(2026, 10, 26)   # weekend
    assert fb.prev_session(dt.date(2026, 11, 20)) == dt.date(2026, 11, 19)
    assert fb.prev_session(dt.date(2026, 4, 6)) == dt.date(2026, 4, 2)       # Good Friday


def test_session_hours_keep_a_margin_and_follow_new_york_time():
    assert fb.in_session(at(ENTRY, 11))
    assert not fb.in_session(at(ENTRY, 9).replace(minute=35))   # 09:35 ET: too early
    assert not fb.in_session(at(dt.date(2026, 10, 25), 11))     # Sunday
    assert not fb.in_session(at(ENTRY, 17))


def test_form_index_keeps_regular_10k_and_10q_only():
    text = index_text([
        ("10-Q", "AAA CORP", 111, FILED, "0000000111-26-000001"),
        ("10-K", "BIG HOLDING CO INC", 222, FILED, "0000000222-26-000009"),
        ("10-Q/A", "AAA CORP", 111, FILED, "0000000111-26-000002"),
        ("NT 10-K", "LATE INC", 333, FILED, "0000000333-26-000001"),
        ("8-K", "AAA CORP", 111, FILED, "0000000111-26-000003")])
    got = fb.parse_form_index(text)
    assert list(got["form"]) == ["10-Q", "10-K"]
    assert list(got["accession"]) == ["0000000111-26-000001", "0000000222-26-000009"]
    assert got["company"].iloc[1] == "BIG HOLDING CO INC"
    assert got["filed"].iloc[0] == FILED


# -- construction -------------------------------------------------------------------------

def test_expiry_is_the_listed_one_closest_to_thirty_days_within_25_to_40():
    assert fb.choose_expiry(EXPIRIES, ENTRY) == ("2026-11-20", "")      # 30 days
    exp, why = fb.choose_expiry(["2026-11-06", "2026-12-18"], ENTRY)    # 16 and 58 days
    assert exp is None and "25–40" in why


def test_strikes_are_listed_symmetric_and_about_ten_percent():
    assert fb.choose_strikes([80, 90, 95, 100, 105, 110, 120], 100.4) == ((90, 100, 110), "")
    # 110 is not listed: the symmetric pair closest to 10 % within 7-13 % is 92/100/108
    assert fb.choose_strikes([88, 92, 100, 108, 115], 100) == ((92, 100, 108), "")
    got, why = fb.choose_strikes([95, 100, 105], 100)                   # only 5 % wings
    assert got is None and "symmetrischen" in why


def test_butterfly_buys_at_the_ask_sells_at_the_bid_four_contracts():
    s, why = fb.build(calls_frame(OPEN_QUOTES), "AAA", 100.0, "2026-11-20", at(ENTRY))
    assert why == ""
    assert [(leg["strike"], leg["qty"], leg["price"]) for leg in s["legs"]] == [
        (90, 1, 11.2), (100, -2, 4.0), (110, 1, 1.1)]
    assert sum(abs(leg["qty"]) for leg in s["legs"]) == 4
    assert s["debit_usd"] == pytest.approx((11.2 - 2 * 4.0 + 1.1) * 100)      # 430
    assert s["fees_open_usd"] == pytest.approx(4 * 0.65)
    assert s["spread_reserve_usd"] == pytest.approx((0.2 + 2 * 0.2 + 0.1) * 100)
    assert s["close_on"] == "2026-11-19"           # last session before expiry


@pytest.mark.parametrize("change, reason", [
    ({100: (0.0, 4.2)}, "100C: kein Bid"),
    ({110: (1.0, 0.0)}, "110C: kein Ask"),
    ({90: (7.0, 11.2)}, "90C: Spanne"),
])
def test_a_bad_leg_quote_refuses_the_butterfly_with_its_reason(change, reason):
    q = {**OPEN_QUOTES, **change}
    s, why = fb.build(calls_frame(q), "AAA", 100.0, "2026-11-20", at(ENTRY))
    assert s is None and why.startswith(reason)


def test_open_interest_volume_age_and_multiplier_are_checked():
    now = at(ENTRY)
    _s, why = fb.build(calls_frame(OPEN_QUOTES, oi=50), "AAA", 100, "2026-11-20", now)
    assert "Open Interest 50" in why
    _s, why = fb.build(calls_frame(OPEN_QUOTES, volume=0), "AAA", 100, "2026-11-20", now)
    assert "kein Umsatz" in why
    _s, why = fb.build(calls_frame(OPEN_QUOTES, last_trade="2026-10-10T15:00:00Z"),
                      "AAA", 100, "2026-11-20", now)
    assert "zu alt" in why
    adjusted = calls_frame(OPEN_QUOTES)
    adjusted["contractSymbol"] = adjusted["contractSymbol"].str.replace("AAA", "AAA1")
    _s, why = fb.build(adjusted, "AAA", 100, "2026-11-20", now)
    assert "Standardkontrakt" in why


def test_eur_conversion_charges_the_fx_cost_both_ways():
    assert fb.pay(110.0, "EUR", 1.10) == pytest.approx(100 * 1.0025)
    assert fb.receive(110.0, "EUR", 1.10) == pytest.approx(100 * 0.9975)
    assert fb.pay(110.0, "USD", None) == 110.0
    with pytest.raises(ValueError):
        fb.pay(1.0, "EUR", None)


# -- the run: dates, both books, bookkeeping ---------------------------------------------

def open_books_and_enter(path, market, live):
    """Books opened on the filing day; the decision on the next session."""
    fb.run(path, market, live, now=at(FILED, 10), log=lambda *_: None)
    return fb.run(path, market, live, now=at(ENTRY, 11), log=lambda *_: None)


def test_no_decision_on_the_filing_day_itself(path, market):
    live = FakeLive()
    fb.run(path, market, live, now=at(FILED, 12), log=lambda *_: None)
    ev = read(path, "SELECT status, first_session FROM fb_events")
    assert list(ev["status"]) == ["pending"]
    assert pd.Timestamp(ev["first_session"].iloc[0]).date() == ENTRY
    assert read(path, "SELECT COUNT(*) n FROM fb_decisions")["n"].iloc[0] == 0


def test_same_candidate_opens_in_the_big_book_and_is_refused_in_the_small(path, market):
    live = FakeLive()
    summary = open_books_and_enter(path, market, live)
    assert summary["opened"] == 1
    dec = read(path, "SELECT book_id, opened, reason, units FROM fb_decisions "
                     "ORDER BY book_id").set_index("book_id")
    assert not dec.loc["fb-eur-1000", "opened"]
    # one butterfly can lose 430 + 2.60 + 2.60 + 70 = 505.20 USD = 460.42 EUR > 10 EUR
    assert dec.loc["fb-eur-1000", "reason"].startswith("Budget reicht nicht")
    assert "460,42" in dec.loc["fb-eur-1000", "reason"].replace(".", ",") or \
        "460.42" in dec.loc["fb-eur-1000", "reason"]
    assert dec.loc["fb-usd-100000", "opened"]
    assert dec.loc["fb-usd-100000", "units"] == 1         # 1 % of 100,000 / 505.20 = 1.98


def test_the_big_book_is_booked_once_with_four_contracts(path, market):
    live = FakeLive()
    open_books_and_enter(path, market, live)
    legs = read(path, "SELECT strike, qty, open_price FROM fb_legs ORDER BY leg")
    assert list(legs["qty"]) == [1, -2, 1] and legs["qty"].abs().sum() == 4
    led = read(path, "SELECT kind, amount FROM fb_ledger WHERE book_id = 'fb-usd-100000' "
                     "ORDER BY booked_at, kind")
    assert led["amount"].sum() == pytest.approx(100_000 - 430 - 2.6)
    con = fb.connect(path, read_only=True)
    st = fb.state(con, "fb-usd-100000", 1.10)
    con.close()
    assert st["reserved"] == pytest.approx(2.6 + 70)
    # closing now: longs at the bid (11 + 1), shorts at the ask (2 x 4.2), minus fees
    assert st["open_value"] == pytest.approx((12.0 - 8.4) * 100 - 2.6)
    assert st["equity"] == pytest.approx(100_000 - 432.6 + 357.4)


def test_the_two_books_keep_separate_money(path, market):
    live = FakeLive()
    open_books_and_enter(path, market, live)
    eur = read(path, "SELECT SUM(amount) s FROM fb_ledger WHERE book_id = 'fb-eur-1000'")
    assert eur["s"].iloc[0] == pytest.approx(1_000.0)
    pos = read(path, "SELECT book_id FROM fb_positions")
    assert list(pos["book_id"]) == ["fb-usd-100000"]


def test_repeating_the_run_books_nothing_twice_and_survives_a_restart(path, market):
    live = FakeLive()
    open_books_and_enter(path, market, live)
    before = read(path, "SELECT COUNT(*) n FROM fb_ledger")["n"].iloc[0]
    for minute in (5, 30, 55):
        fb.run(path, market, live, now=at(ENTRY, 11) + dt.timedelta(minutes=minute),
               log=lambda *_: None)
    assert read(path, "SELECT COUNT(*) n FROM fb_ledger")["n"].iloc[0] == before
    assert read(path, "SELECT COUNT(*) n FROM fb_positions")["n"].iloc[0] == 1
    assert read(path, "SELECT COUNT(*) n FROM fb_decisions")["n"].iloc[0] == 2
    assert read(path, "SELECT COUNT(*) n FROM fb_books")["n"].iloc[0] == 2
    # a fresh connection (the app after a restart) reads the same balance
    con = fb.connect(path, read_only=True)
    assert fb.state(con, "fb-usd-100000", 1.1)["cash"] == pytest.approx(100_000 - 432.6)
    con.close()


def test_missing_quotes_reject_the_event_for_both_books(path, market):
    live = FakeLive()
    live.quotes = {**OPEN_QUOTES, 100: (0.0, 0.0)}
    open_books_and_enter(path, market, live)
    ev = read(path, "SELECT status, quotes_ok, reason FROM fb_events")
    assert ev["status"].iloc[0] == "decided" and not ev["quotes_ok"].iloc[0]
    assert ev["reason"].iloc[0] == "100C: kein Bid"
    dec = read(path, "SELECT opened, reason FROM fb_decisions")
    assert len(dec) == 2 and not dec["opened"].any()


def test_duplicate_report_and_share_classes_make_one_event(path, market):
    live = FakeLive()
    live.index[FILED] = index_text([
        ("10-Q", "AAA CORP", 111, FILED, "0000000111-26-000001"),
        ("10-Q", "AAA CORP", 111, FILED, "0000000111-26-000004")])     # re-filed
    open_books_and_enter(path, market, live)
    ev = read(path, "SELECT accession, status, ticker FROM fb_events ORDER BY accession")
    assert list(ev["status"]) == ["decided", "duplicate"]
    # AAA trades 1,000x the value of AAA-B before the decision: AAA is the line used
    assert ev["ticker"].iloc[0] == "AAA"
    assert read(path, "SELECT COUNT(*) n FROM fb_positions")["n"].iloc[0] == 1


def test_a_window_without_a_run_in_hours_is_missed_not_filled_later(path, market):
    live = FakeLive()
    fb.run(path, market, live, now=at(FILED, 10), log=lambda *_: None)
    # next run only on the fourth session after the filing (window: 21, 22, 23 Oct)
    fb.run(path, market, live, now=at(dt.date(2026, 10, 26), 11), log=lambda *_: None)
    ev = read(path, "SELECT status, reason FROM fb_events")
    assert ev["status"].iloc[0] == "missed"
    assert read(path, "SELECT COUNT(*) n FROM fb_positions")["n"].iloc[0] == 0


def test_no_decision_while_the_market_is_closed(path, market):
    live = FakeLive()
    fb.run(path, market, live, now=at(FILED, 10), log=lambda *_: None)
    fb.run(path, market, live, now=at(ENTRY, 18), log=lambda *_: None)     # after close
    assert read(path, "SELECT status FROM fb_events")["status"].iloc[0] == "pending"


def test_ex_dividend_before_expiry_is_refused(path, market):
    market.execute("UPDATE prices SET close_adj = close * (1 + 0.0001 * "
                   "datediff('day', date '2025-01-01', date)) WHERE ticker = 'AAA'")
    live = FakeLive()
    live.ex_div = dt.date(2026, 11, 5)
    open_books_and_enter(path, market, live)
    assert "Ex-Dividende" in read(path, "SELECT reason FROM fb_events")["reason"].iloc[0]


def test_no_eur_rate_means_no_eur_trade(path, market):
    market.execute("DELETE FROM prices WHERE ticker = 'EURUSD=X'")
    live = FakeLive()
    live.fx_rate = None
    open_books_and_enter(path, market, live)
    dec = read(path, "SELECT book_id, reason FROM fb_decisions").set_index("book_id")
    assert dec.loc["fb-eur-1000", "reason"] == "kein aktueller EUR/USD-Kurs"


def test_one_open_position_per_company_and_book(path, market):
    live = FakeLive()
    open_books_and_enter(path, market, live)
    later = dt.date(2026, 11, 16)
    for d in pd.bdate_range("2026-10-21", "2026-11-16").date:
        market.execute("INSERT INTO prices VALUES ('AAA', ?, 100, 100, 100, 100, 1000000)",
                       [d])
    live.index[later] = index_text([("10-K", "AAA CORP", 111, later,
                                     "0000000111-26-000010")])
    live.calls_kwargs = {"last_trade": "2026-11-17T14:00:00Z"}
    fb.run(path, market, live, now=at(dt.date(2026, 11, 17), 11), log=lambda *_: None)
    dec = read(path, "SELECT reason FROM fb_decisions WHERE accession = "
                     "'0000000111-26-000010' AND book_id = 'fb-usd-100000'")
    assert dec["reason"].iloc[0] == "bereits offene Position im Unternehmen"


def test_planned_close_on_the_last_session_before_expiry(path, market):
    live = FakeLive()
    open_books_and_enter(path, market, live)
    live.quotes = {80: (24.8, 25.0), 90: (15.0, 15.2), 100: (6.0, 6.2), 110: (1.5, 1.6),
                   120: (0.2, 0.3)}
    live.calls_kwargs = {"last_trade": "2026-11-19T14:00:00Z"}
    fb.run(path, market, live, now=at(dt.date(2026, 11, 19), 11), log=lambda *_: None)
    p = read(path, "SELECT status, close_usd, close_fees_usd FROM fb_positions")
    assert p["status"].iloc[0] == "closed"
    # sell 90C at 15.0 and 110C at 1.5, buy back two 100C at 6.2
    assert p["close_usd"].iloc[0] == pytest.approx((15.0 + 1.5 - 12.4) * 100)
    assert p["close_fees_usd"].iloc[0] == pytest.approx(2.6)
    cash = read(path, "SELECT SUM(amount) s FROM fb_ledger WHERE book_id='fb-usd-100000'")
    assert cash["s"].iloc[0] == pytest.approx(100_000 - 432.6 + 410 - 2.6)
    con = fb.connect(path, read_only=True)
    assert fb.state(con, "fb-usd-100000", 1.1)["reserved"] == 0
    con.close()


def test_a_missed_close_is_settled_by_exercise_and_assignment(path, market):
    live = FakeLive()
    open_books_and_enter(path, market, live)
    market.execute("INSERT INTO prices VALUES ('AAA', '2026-11-20', 104, 104, 104, 104, 1)")
    fb.run(path, market, live, now=at(dt.date(2026, 11, 23), 11), log=lambda *_: None)
    p = read(path, "SELECT status, close_usd, close_fees_usd, close_reason FROM fb_positions")
    # 90C worth 14, two 100C owe 4 each: 600 USD; three contracts in the money:
    # fees 3 x 0.65, stock spread 0.05 % x 104 x 100 x 3 = 15.60
    assert p["close_usd"].iloc[0] == pytest.approx(600 - 15.6)
    assert p["close_fees_usd"].iloc[0] == pytest.approx(1.95)
    assert "Ausübung" in p["close_reason"].iloc[0]


def test_monthly_counts_and_the_benchmark_in_euros(path, market):
    live = FakeLive()
    open_books_and_enter(path, market, live)
    con = fb.connect(path, read_only=True)
    months, reasons = fb.monthly(con)
    perf = fb.performance(con, "fb-eur-1000")
    con.close()
    row = months.set_index("month").loc["2026-10"]
    assert (row["candidates"], row["with_quotes"]) == (1, 1)
    assert row["opened · USD"] == 1 and row["opened · EUR"] == 0
    assert reasons.set_index("book_id").loc["fb-eur-1000", "reason"] == \
        "Budget reicht nicht"
    # SPY 502 on 20 Oct (opening day) -> 503 on 21 Oct (the last valuation), EUR/USD
    # flat: 1,000 x 503/502; later SPY closes are not part of the comparison
    assert perf["benchmark"].index[-1] == ENTRY
    assert perf["benchmark"].iloc[-1] == pytest.approx(1000 * 503 / 502)
    assert any("nicht aussagekräftig" in n for n in perf["notes"])


def test_live_spot_reads_yfinance_fast_info_by_item(monkeypatch):
    """yfinance 1.7: fast_info["lastPrice"] works, .get("last_price") gives None."""
    import yfinance

    class Info(dict):
        def get(self, key, default=None):
            return None

    class Ticker:
        def __init__(self, symbol):
            self.fast_info = Info(lastPrice=101.5)

    monkeypatch.setattr(yfinance, "Ticker", Ticker)
    assert fb.Live("agent").spot("AAA") == 101.5
    assert fb.Live("agent").fx() == 101.5


# -- audit 24.10.2026 ---------------------------------------------------------------------

class _Response:
    def __init__(self, status: int, text: str = ""):
        self.status_code, self.text = status, text


def test_edgar_only_404_means_no_index_any_other_answer_raises(monkeypatch):
    """F15: SEC answers 403 to a rate limit or a rejected user agent. That is not a
    day without filings; it must raise, without the URL in the message (R24)."""
    import requests

    answers = {}
    monkeypatch.setattr(requests, "get", lambda url, **kw: answers["r"])
    live = fb.Live("agent")
    answers["r"] = _Response(404)
    assert live.form_index(FILED) is None
    answers["r"] = _Response(200, "index text")
    assert live.form_index(FILED) == "index text"
    for status in (403, 429, 503):
        answers["r"] = _Response(status)
        with pytest.raises(fb.EdgarError) as err:
            live.form_index(FILED)
        assert f"HTTP {status}" in str(err.value) and "sec.gov" not in str(err.value)


def test_a_failed_edgar_day_stays_open_and_is_read_on_the_next_run(path, market):
    """F15: the failing day gets a note and no fb_scan row; later days are still read;
    the next run reads the day and finds its filing."""
    live = FakeLive()
    later = dt.date(2026, 10, 21)
    live.index[later] = index_text([("10-K", "BBB INC", 222, later,
                                     "0000000222-26-000002")])
    real = live.form_index

    def refusing(day):
        if day == FILED:
            raise fb.EdgarError(f"HTTP 403 für den Index vom {day}")
        return real(day)

    live.form_index = refusing
    fb.run(path, market, live, now=at(FILED, 10), log=lambda *_: None)   # books open
    s = fb.run(path, market, live, now=at(later, 18), log=lambda *_: None)
    assert any("EDGAR 2026-10-20 nicht gelesen" in n and "HTTP 403" in n and
               "bleibt offen" in n for n in s["notes"])
    scanned = read(path, "SELECT day FROM fb_scan ORDER BY day")["day"]
    assert FILED not in set(pd.to_datetime(scanned).dt.date)
    assert list(read(path, "SELECT accession FROM fb_events")["accession"]) == [
        "0000000222-26-000002"]                       # the day after was still read
    live.form_index = real
    fb.run(path, market, live, now=at(later, 19), log=lambda *_: None)
    got = read(path, "SELECT accession FROM fb_events ORDER BY accession")["accession"]
    assert list(got) == ["0000000111-26-000001", "0000000222-26-000002"]


def test_a_missing_long_bid_keeps_the_last_mark_and_flags_it(path, market):
    """F18: a long leg without a bid is no price. The last mark stays and is marked
    uncertain; it is not revalued as if the call were worth 0."""
    live = FakeLive()
    open_books_and_enter(path, market, live)
    first = read(path, "SELECT mark_value FROM fb_positions")["mark_value"].iloc[0]
    assert first == pytest.approx((12.0 - 8.4) * 100 - 2.6)          # 357.40
    live.quotes = {**OPEN_QUOTES, 90: (float("nan"), 11.2)}
    live.calls_kwargs = {"last_trade": "2026-10-22T14:00:00Z"}
    fb.run(path, market, live, now=at(dt.date(2026, 10, 22), 11), log=lambda *_: None)
    p = read(path, "SELECT mark_value, mark_note FROM fb_positions")
    assert p["mark_value"].iloc[0] == pytest.approx(first)
    assert p["mark_note"].iloc[0] == "keine handelbare Quote, letzte Bewertung gilt"
    con = fb.connect(path, read_only=True)
    assert fb.state(con, "fb-usd-100000", 1.1)["uncertain"] == 1
    con.close()


def test_a_quoted_bid_of_zero_is_a_price_of_zero():
    """F18: a real bid of 0 stays 0; a missing one gives no value."""
    legs = [{"qty": 1, "bid": 0.0, "ask": 0.05}, {"qty": -2, "bid": 0.0, "ask": 0.05},
            {"qty": 1, "bid": 0.0, "ask": 0.05}]
    assert fb.liquidation_usd(legs) == pytest.approx(-2 * 0.05 * 100)
    legs[0]["bid"] = None
    assert fb.liquidation_usd(legs) is None
    legs[0]["bid"] = float("nan")
    assert fb.liquidation_usd(legs) is None


def test_an_ended_line_with_more_old_turnover_does_not_win(market):
    """F20: an ended line of the same CIK traded far more years ago; the line still
    trading is chosen, not the ended one (which would read as stale prices)."""
    market.execute("INSERT INTO securities VALUES (111, 'AAA-OLD')")
    old_days = pd.bdate_range(end="2024-12-31", periods=300).date
    market.executemany("INSERT INTO prices VALUES ('AAA-OLD',?,100,100,100,100,?)",
                       [(d, 50_000_000) for d in old_days])
    line, why = fb.pick_line(market, 111, ENTRY)
    assert (line["ticker"], why) == ("AAA", "")
    assert line["classes"] == 3
    # with no current line at all, the newest stale line is reported as stale
    market.execute("DELETE FROM prices WHERE ticker IN ('AAA', 'AAA-B')")
    line, why = fb.pick_line(market, 111, ENTRY)
    assert line["ticker"] == "AAA-OLD" and why.startswith("Kursdaten veraltet")


def _old_store(path):
    """A store as the tracker wrote it before named migrations: the ten tables, no
    fb_migrations, one book already opened."""
    con = duckdb.connect(str(path))
    for _name, sql in fb.MIGRATIONS[:10]:
        con.execute(sql)
    con.execute("INSERT INTO fb_books VALUES ('fb-usd-100000', 'old', 'USD', 100000, "
                "TIMESTAMP '2026-10-18 12:00:00', '{}')")
    con.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_an_old_store_opens_and_records_its_migrations(path, read_only):
    """Low item 4: a store without fb_migrations opens (for the app read-only too),
    keeps its rows and records every migration name once."""
    _old_store(path)
    con = fb.connect(path, read_only=read_only)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM fb_migrations ORDER BY name")
                 .fetchall()]
        assert names == [name for name, _sql in fb.MIGRATIONS]
        assert con.execute("SELECT name FROM fb_books").fetchall() == [("old",)]
    finally:
        con.close()


def test_a_new_migration_runs_exactly_once_and_reaches_readers(path, monkeypatch):
    """Low item 4: an appended ALTER TABLE reaches an existing store once; replaying
    it on the next open would fail ("column already exists")."""
    _old_store(path)
    fb.connect(path).close()
    monkeypatch.setattr(fb, "MIGRATIONS", fb.MIGRATIONS + [
        ("999_test_column", "ALTER TABLE fb_runs ADD COLUMN extra INTEGER")])
    con = fb.connect(path, read_only=True)            # the app opens first
    cols = [r[0] for r in con.execute("DESCRIBE fb_runs").fetchall()]
    con.close()
    assert "extra" in cols
    fb.connect(path).close()                          # the tracker: not applied again
    fb.connect(path, read_only=True).close()
