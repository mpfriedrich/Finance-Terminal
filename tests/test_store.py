"""Saved work must survive. These test the half of the database that cannot be rebuilt."""
import duckdb
import pytest

from portfolio_lab.screen.spec import Filter, PortfolioSpec, spec_from_json, spec_to_json
from portfolio_lab.data.schema import USER_MIGRATIONS, _apply
from portfolio_lab.store import Store


@pytest.fixture()
def store():
    c = duckdb.connect(":memory:")
    _apply(c, USER_MIGRATIONS)
    return Store(c)


def test_spec_survives_a_round_trip():
    spec = PortfolioSpec(size=7, filters=(Filter("price_to_book", "<=", 2.0),),
                         unsupported=("likeable CEO",))
    back = spec_from_json(spec_to_json(spec))
    assert back == spec
    assert back.fingerprint() == spec.fingerprint()


def test_portfolios_and_their_chat_persist(store):
    pid = store.create("Deep value")
    store.add_message(pid, "user", "price to book max 2")
    spec = PortfolioSpec(filters=(Filter("price_to_book", "<=", 2.0),))
    store.save_spec(pid, spec)

    assert store.get_spec(pid) == spec
    assert [m["text"] for m in store.messages(pid)] == ["price to book max 2"]


def test_two_portfolios_stay_separate(store):
    a, b = store.create("Value"), store.create("Growth")
    store.save_spec(a, PortfolioSpec(size=10))
    store.save_spec(b, PortfolioSpec(size=25))
    assert store.get_spec(a).size == 10
    assert store.get_spec(b).size == 25
    assert len(store.list_portfolios()) == 2


def test_duplicate_names_are_refused(store):
    store.create("Value")
    with pytest.raises(ValueError):
        store.create("value")


def test_deleting_a_portfolio_takes_its_chat_with_it(store):
    pid = store.create("Temp")
    store.add_message(pid, "user", "hello")
    store.delete(pid)
    assert store.messages(pid) == []
    assert len(store.list_portfolios()) == 0


def test_a_round_trip_does_not_change_the_fingerprint():
    """A list where a tuple belongs would give the same screen a different identity,
    silently resetting the how-many-times-tested counter."""
    spec = PortfolioSpec(size=5, tickers=("AAPL", "MSFT"),
                         filters=(Filter("market_cap", ">=", 1e9),))
    back = spec_from_json(spec_to_json(spec))
    assert back == spec
    assert back.fingerprint() == spec.fingerprint()
    assert isinstance(back.tickers, tuple)


def test_option_legs_persist_per_portfolio(store):
    a, b = store.create("Vol"), store.create("Directional")
    store.add_leg(a, "AAPL", "call", 340.0, "2026-12-18", 1, 12.5, iv=0.26)
    store.add_leg(a, "AAPL", "put", 320.0, "2026-12-18", -1, 6.25, iv=0.29)
    store.add_leg(b, "MSFT", "call", 500.0, "2027-01-15", 2, 20.0)

    assert len(store.legs(a)) == 2
    assert len(store.legs(b)) == 1
    assert store.legs(a).contract_size.tolist() == [100, 100]


def test_a_leg_can_be_removed_without_touching_the_others(store):
    pid = store.create("Spread")
    first = store.add_leg(pid, "AAPL", "call", 340.0, "2026-12-18", 1, 12.5)
    store.add_leg(pid, "AAPL", "call", 360.0, "2026-12-18", -1, 5.0)
    store.remove_leg(first)
    remaining = store.legs(pid)
    assert len(remaining) == 1
    assert remaining.iloc[0].strike == 360.0


def test_deleting_a_portfolio_takes_its_option_legs(store):
    pid = store.create("Temp")
    store.add_leg(pid, "AAPL", "call", 340.0, "2026-12-18", 1, 12.5)
    store.delete(pid)
    assert store.legs(pid).empty


def test_the_watchlist_deduplicates_and_keeps_order(store):
    """Re-adding a name updates its note without moving it. A watchlist that reshuffles
    itself every time you annotate something stops being a list you can scan."""
    store.watch("aapl", "expensive")
    store.watch("MSFT")
    store.watch("AAPL", "still expensive")   # same name again
    wl = store.watchlist()
    assert wl.ticker.tolist() == ["AAPL", "MSFT"]
    assert wl.set_index("ticker").loc["AAPL", "note"] == "still expensive"


def test_everything_followed_merges_watchlist_holdings_and_picks(store):
    store.watch("NVDA")
    pid = store.create("Value")
    store.save_spec(pid, PortfolioSpec(tickers=("KO", "NVDA")))
    followed = store.everything_followed()
    assert followed[0] == "NVDA"          # watchlist first
    assert set(followed) == {"NVDA", "KO"}   # and no duplicate


def test_unwatch_removes_only_that_name(store):
    store.watch("AAPL")
    store.watch("MSFT")
    store.unwatch("AAPL")
    assert store.watchlist().ticker.tolist() == ["MSFT"]


def test_a_read_only_reader_cannot_write(tmp_path):
    """Regression: the scheduled briefing opened the portfolio store read-write and so
    failed whenever the app was left running. A job that only reads must say so.

    The cross-process case (app running, job starting) cannot be exercised in one
    process — DuckDB refuses two different configurations on one file inside a single
    process, which is precisely why separate processes are the working arrangement. What
    is checked here is the other half: that read-only really is read-only."""
    import duckdb as ddb

    from portfolio_lab.data.schema import open_user

    path = tmp_path / "user.duckdb"
    writer = open_user(path)
    Store(writer).create("Held open")
    writer.close()

    reader = open_user(path, read_only=True)
    assert Store(reader).list_portfolios().name.tolist() == ["Held open"]
    with pytest.raises(ddb.Error):
        reader.execute("DELETE FROM portfolios")


def test_opening_a_missing_store_read_only_explains_itself(tmp_path):
    from portfolio_lab.data.schema import open_user

    with pytest.raises(RuntimeError, match="Open the app once"):
        open_user(tmp_path / "never_created.duckdb", read_only=True)


# --- the strategy lab lives beside the screen, not inside it -------------------------

def test_the_two_chat_threads_do_not_bleed_into_each_other(store):
    """The screen and the rule are different conversations about the same portfolio.
    Read interleaved, both become nonsense."""

    pid = store.create("Deep value")
    store.add_message(pid, "user", "cheap small caps")
    store.add_message(pid, "user", "50/200 crossover", kind="lab")

    assert [m["text"] for m in store.messages(pid)] == ["cheap small caps"]
    assert [m["text"] for m in store.messages(pid, kind="lab")] == ["50/200 crossover"]


def test_a_portfolio_without_a_rule_starts_from_the_baseline(store):
    from portfolio_lab.lab.spec import StrategySpec

    pid = store.create("Deep value")
    assert store.get_strategy(pid) == StrategySpec()


def test_a_rule_survives_a_round_trip_with_its_fingerprint(store):
    from portfolio_lab.lab.spec import StrategySpec

    pid = store.create("Deep value")
    rule = StrategySpec(signal="ma_cross", fast_days=20, slow_days=100,
                        stop="trailing_stop", stop_pct=0.15)
    store.save_strategy(pid, rule)
    back = store.get_strategy(pid)
    assert back == rule
    assert back.fingerprint() == rule.fingerprint()


def test_saving_a_rule_replaces_the_previous_one(store):
    from portfolio_lab.lab.spec import StrategySpec

    pid = store.create("Deep value")
    store.save_strategy(pid, StrategySpec(signal="momentum"))
    store.save_strategy(pid, StrategySpec(signal="mean_reversion"))
    assert store.get_strategy(pid).signal == "mean_reversion"
    assert store.con.execute(
        "SELECT COUNT(*) FROM strategies WHERE portfolio_id = ?", [pid]).fetchone()[0] == 1
