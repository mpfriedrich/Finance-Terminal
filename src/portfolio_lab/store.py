"""Portfolios, chat history and backtest runs.

Lives in its own database file. Market data can be deleted and rebuilt at any time; this
cannot — it is the only thing here that represents the user's own work.
"""

from __future__ import annotations

import datetime as dt
import uuid

import duckdb
import pandas as pd

from .screen.spec import PortfolioSpec, spec_from_json, spec_to_json


class Store:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con

    def list_portfolios(self) -> pd.DataFrame:
        return self.con.execute(
            "SELECT id, name, created_at FROM portfolios ORDER BY created_at"
        ).fetch_df()

    def create(self, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("A portfolio needs a name.")
        taken = self.con.execute(
            "SELECT 1 FROM portfolios WHERE lower(name) = lower(?)", [name]
        ).fetchone()
        if taken:
            raise ValueError(f"You already have a portfolio called {name!r}.")
        pid = uuid.uuid4().hex[:12]
        self.con.execute(
            "INSERT INTO portfolios (id, name, created_at, spec_json) VALUES (?,?,?,?)",
            [pid, name, dt.datetime.now(), spec_to_json(PortfolioSpec())],
        )
        return pid

    def rename(self, pid: str, name: str) -> None:
        self.con.execute("UPDATE portfolios SET name = ? WHERE id = ?", [name.strip(), pid])

    def delete(self, pid: str) -> None:
        self.con.execute("DELETE FROM messages WHERE portfolio_id = ?", [pid])
        self.con.execute("DELETE FROM backtest_runs WHERE portfolio_id = ?", [pid])
        self.con.execute("DELETE FROM option_legs WHERE portfolio_id = ?", [pid])
        self.con.execute("DELETE FROM portfolios WHERE id = ?", [pid])

    def get_spec(self, pid: str) -> PortfolioSpec:
        row = self.con.execute("SELECT spec_json FROM portfolios WHERE id = ?", [pid]).fetchone()
        return spec_from_json(row[0]) if row else PortfolioSpec()

    def save_spec(self, pid: str, spec: PortfolioSpec) -> None:
        self.con.execute(
            "UPDATE portfolios SET spec_json = ? WHERE id = ?", [spec_to_json(spec), pid]
        )

    def add_message(self, pid: str, role: str, text: str,
                    spec: PortfolioSpec | None = None, kind: str = "screen") -> None:
        self.con.execute(
            "INSERT INTO messages (id, portfolio_id, role, text, spec_json, kind, "
            "created_at) VALUES (?,?,?,?,?,?,?)",
            [uuid.uuid4().hex[:12], pid, role, text,
             spec_to_json(spec) if spec else None, kind, dt.datetime.now()],
        )

    def messages(self, pid: str, kind: str = "screen") -> list[dict]:
        """One thread per conversation. The screen and the strategy lab are different
        conversations about the same portfolio, and reading them interleaved would make
        both of them nonsense."""
        rows = self.con.execute(
            "SELECT role, text, created_at FROM messages WHERE portfolio_id = ? "
            "AND COALESCE(kind, 'screen') = ? ORDER BY created_at",
            [pid, kind],
        ).fetchall()
        return [{"role": r[0], "text": r[1], "at": r[2]} for r in rows]

    # ---- the strategy lab --------------------------------------------------------
    def get_strategy(self, pid: str):
        from .lab.spec import StrategySpec, spec_from_json as strategy_from_json

        row = self.con.execute(
            "SELECT spec_json FROM strategies WHERE portfolio_id = ?", [pid]).fetchone()
        return strategy_from_json(row[0]) if row else StrategySpec()

    def save_strategy(self, pid: str, spec) -> None:
        from .lab.spec import spec_to_json as strategy_to_json

        self.con.execute("DELETE FROM strategies WHERE portfolio_id = ?", [pid])
        self.con.execute(
            "INSERT INTO strategies (portfolio_id, spec_json, updated_at) VALUES (?,?,?)",
            [pid, strategy_to_json(spec), dt.datetime.now()],
        )

    def backtest_count(self, spec_fingerprint: str, data_fingerprint: str) -> int:
        """How often this exact screen has been tested against this exact data build.

        The overfitting counter (PLAN.md R21): searching long enough always turns up a
        pretty curve, so the number of attempts belongs next to the result.
        """
        row = self.con.execute(
            "SELECT COUNT(*) FROM backtest_runs WHERE spec_fingerprint = ? "
            "AND data_fingerprint = ?",
            [spec_fingerprint, data_fingerprint],
        ).fetchone()
        return int(row[0]) if row else 0

    def record_run(
        self,
        pid: str,
        kind: str,
        spec_fingerprint: str,
        data_fingerprint: str,
        start: dt.date | None,
        end: dt.date | None,
        params: dict,
        metrics: dict,
        holdout_unlocked: bool = False,
    ) -> str:
        """Write one backtest result down, stamped with the data build it came from.

        The stamp is the point (R22). Free price sources restate adjusted closes without
        telling anyone, so the same screen over the same dates can produce a different
        curve next month. A run that cannot name the build it read is not reproducible,
        and an irreproducible backtest is an anecdote.
        """
        import json

        run_id = uuid.uuid4().hex[:12]
        self.con.execute(
            "INSERT INTO backtest_runs (id, portfolio_id, kind, spec_fingerprint, "
            "data_fingerprint, start_date, end_date, holdout_unlocked, params_json, "
            "metrics_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, pid, kind, spec_fingerprint, data_fingerprint, start, end,
             holdout_unlocked, json.dumps(params, default=str),
             json.dumps(metrics, default=str), dt.datetime.now()],
        )
        return run_id

    def runs(self, pid: str, limit: int = 20) -> pd.DataFrame:
        return self.con.execute(
            "SELECT id, kind, spec_fingerprint, data_fingerprint, start_date, end_date, "
            "holdout_unlocked, metrics_json, created_at FROM backtest_runs "
            "WHERE portfolio_id = ? ORDER BY created_at DESC LIMIT ?",
            [pid, limit],
        ).fetch_df()

    def other_data_builds(self, spec_fingerprint: str, data_fingerprint: str) -> int:
        """How many earlier runs of this same screen read a DIFFERENT data build.

        Non-zero means the curve on screen is not comparable with the one remembered from
        last time, however identical the screen was (R22).
        """
        row = self.con.execute(
            "SELECT COUNT(*) FROM backtest_runs WHERE spec_fingerprint = ? "
            "AND data_fingerprint <> ?",
            [spec_fingerprint, data_fingerprint],
        ).fetchone()
        return int(row[0]) if row else 0

    # ---- live tracking -----------------------------------------------------------
    def start_tracking(
        self,
        pid: str,
        picks: pd.DataFrame,
        entry_date: dt.date,
        spec_json: str,
        thesis: str = "",
        benchmark: str = "^SP500TR",
        cost_of: callable | None = None,
    ) -> int:
        """Freeze today's selection as a paper portfolio.

        Entry prices and weights are recorded now, before the outcome is known. That is
        the whole point: nothing about this result can be revised later to look better.
        """
        self.stop_tracking(pid)
        rows = []
        for r in picks.itertuples():
            cost = float(cost_of(getattr(r, "market_cap", float("nan")))) if cost_of else 0.0
            rows.append((pid, r.ticker, getattr(r, "name", None), float(r.weight),
                         float(r.price), entry_date, cost))
        self.con.executemany(
            "INSERT INTO tracked (portfolio_id, ticker, name, weight, entry_price, "
            "entry_date, entry_cost) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        self.con.execute(
            "INSERT INTO tracking (portfolio_id, started_on, thesis, benchmark, spec_json, "
            "created_at) VALUES (?,?,?,?,?,?)",
            [pid, entry_date, thesis, benchmark, spec_json, dt.datetime.now()],
        )
        return len(rows)

    def stop_tracking(self, pid: str) -> None:
        self.con.execute("DELETE FROM tracked WHERE portfolio_id = ?", [pid])
        self.con.execute("DELETE FROM tracking WHERE portfolio_id = ?", [pid])

    def tracking_info(self, pid: str) -> dict | None:
        row = self.con.execute(
            "SELECT started_on, thesis, benchmark, spec_json FROM tracking WHERE portfolio_id = ?",
            [pid],
        ).fetchone()
        if not row:
            return None
        return {"started_on": row[0], "thesis": row[1], "benchmark": row[2], "spec_json": row[3]}

    def holdings(self, pid: str) -> pd.DataFrame:
        return self.con.execute(
            "SELECT ticker, name, weight, entry_price, entry_date, entry_cost "
            "FROM tracked WHERE portfolio_id = ? ORDER BY weight DESC, ticker",
            [pid],
        ).fetch_df()

    # ---- option positions --------------------------------------------------------
    def add_leg(self, pid: str, underlying: str, kind: str, strike: float, expiry: str,
                quantity: float, entry_price: float, iv: float | None = None,
                contract_size: int = 100) -> str:
        leg_id = uuid.uuid4().hex[:12]
        self.con.execute(
            "INSERT INTO option_legs (id, portfolio_id, underlying, kind, strike, expiry, "
            "quantity, entry_price, iv, contract_size, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [leg_id, pid, underlying.upper(), kind, float(strike), expiry,
             float(quantity), float(entry_price), iv, int(contract_size),
             dt.datetime.now()],
        )
        return leg_id

    def legs(self, pid: str) -> pd.DataFrame:
        return self.con.execute(
            "SELECT id, underlying, kind, strike, expiry, quantity, entry_price, iv, "
            "contract_size FROM option_legs WHERE portfolio_id = ? "
            "ORDER BY expiry, strike, kind",
            [pid],
        ).fetch_df()

    def remove_leg(self, leg_id: str) -> None:
        self.con.execute("DELETE FROM option_legs WHERE id = ?", [leg_id])

    def clear_legs(self, pid: str) -> None:
        self.con.execute("DELETE FROM option_legs WHERE portfolio_id = ?", [pid])

    # ---- watchlist ---------------------------------------------------------------
    def watchlist(self) -> pd.DataFrame:
        return self.con.execute(
            "SELECT ticker, note, added_at FROM watchlist ORDER BY added_at"
        ).fetch_df()

    def watch(self, ticker: str, note: str = "") -> None:
        # ON CONFLICT rather than INSERT OR REPLACE: replacing deletes the row and
        # re-inserts it, which resets added_at and quietly moves the name to the bottom
        # of the list. Adding a note to something you have watched for months should not
        # reorder your watchlist.
        self.con.execute(
            "INSERT INTO watchlist (ticker, note, added_at) VALUES (?,?,?) "
            "ON CONFLICT (ticker) DO UPDATE SET note = excluded.note",
            [ticker.strip().upper(), note.strip() or None, dt.datetime.now()],
        )

    def unwatch(self, ticker: str) -> None:
        self.con.execute("DELETE FROM watchlist WHERE ticker = ?", [ticker.strip().upper()])

    def everything_followed(self) -> list[str]:
        """Watchlist, tracked holdings and hand-picked names, in that order, deduplicated.

        One list for anything that should show up in a daily briefing — a name you are
        tracking is followed whether or not you also put it on the watchlist.
        """
        seen: list[str] = []
        for t in self.watchlist().ticker.tolist():
            if t not in seen:
                seen.append(t)
        for row in self.list_portfolios().itertuples():
            for t in self.holdings(row.id).ticker.tolist():
                if t not in seen:
                    seen.append(t)
            for t in self.get_spec(row.id).tickers:
                if t not in seen:
                    seen.append(t)
        return seen
