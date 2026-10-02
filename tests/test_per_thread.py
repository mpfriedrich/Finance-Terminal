"""One DuckDB connection, many Streamlit threads: each thread must get its own cursor."""
import threading

import duckdb

from portfolio_lab.data.sources import per_thread


class Holder:
    def __init__(self, con):
        self.con = con
        self.name = "market"


def _in_thread(fn):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()))
    t.start()
    t.join()
    return out[0]


def test_each_thread_gets_its_own_cursor_and_keeps_it():
    holder = Holder(duckdb.connect(":memory:"))
    mine = per_thread(holder)
    assert mine.con is not holder.con
    assert per_thread(holder) is mine                    # reused within a run
    other = _in_thread(lambda: per_thread(holder))
    assert other.con is not mine.con
    assert mine.name == "market"                         # everything else is shared


def test_a_new_holder_gets_a_new_cursor():
    # after a rebuilt store is reopened, the old cursor must not be served
    a, b = Holder(duckdb.connect(":memory:")), Holder(duckdb.connect(":memory:"))
    assert per_thread(a).con is not per_thread(b).con


def test_concurrent_queries_do_not_cross():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE a AS SELECT range AS metric FROM range(2000)")
    con.execute("CREATE TABLE b AS SELECT range AS other FROM range(2000)")
    holder, failures = Holder(con), []

    def work():
        src = per_thread(holder)
        for i in range(300):
            try:
                if i % 2:
                    df = src.con.execute("SELECT metric FROM a").fetch_df()
                    assert [r.metric for r in df.head(2).itertuples()] == [0, 1]
                else:
                    assert src.con.execute("SELECT count(*) FROM b").fetchone()[0] == 2000
            except Exception as exc:                                  # noqa: BLE001
                failures.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert failures == []
