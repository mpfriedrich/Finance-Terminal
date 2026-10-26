"""Industry classification from the SEC's own filing metadata.

Replaces guessing at company names. Name patterns caught abrdn's metal trusts because
they say "ETF" on the tin, but they cannot tell Sprott Physical Gold Trust from Camden
Property Trust, and they never catch a blank-cheque shell called "Iron Horse Acquisition
Corp". The SIC code the filer registers with the SEC does both.

The four codes excluded here are not a matter of taste — they describe entities that have
no operating business, so a valuation ratio computed for them means something different
from the same ratio on a company that sells things:

  6189  asset-backed securities        a securitisation vehicle, not a company
  6221  commodity contracts broking    the metal trusts land here
  6726  investment offices             closed-end funds, unit trusts, ETFs
  6770  blank checks                   SPACs: a pile of cash looking for a target

REITs (6798) are deliberately KEPT. They are operating businesses with real assets,
tenants and earnings, and a book-value screen is a legitimate way to look at them.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pandas as pd

NON_OPERATING_SIC = {
    "6189": "asset-backed securities",
    "6221": "commodity contracts",
    "6726": "investment offices, funds and ETFs",
    "6770": "blank cheque (SPAC)",
}


def iter_submissions(zip_path: Path, only: set[int] | None = None) -> Iterator[dict]:
    """Yield filer metadata documents.

    The archive holds 985,667 filers — every entity that has ever filed with EDGAR —
    while a US-listed equity universe is a few thousand of them. `only` restricts the
    read to the CIKs actually needed; a zip allows random access per member, so the rest
    are never decompressed. Skipping them is the difference between seconds and an hour.

    Paginated continuation files (CIK…-submissions-001.json) carry filing history only
    and no `sic`, so they are excluded by name rather than parsed and discarded.
    """
    with zipfile.ZipFile(zip_path) as zf:
        if only is not None:
            names = [f"CIK{cik:010d}.json" for cik in sorted(only)]
            available = set(zf.namelist())
            names = [n for n in names if n in available]
        else:
            names = sorted(
                n for n in zf.namelist()
                if n.startswith("CIK") and n.endswith(".json") and "-submissions-" not in n
            )
        for name in names:
            try:
                with zf.open(name) as fh:
                    yield json.load(fh)
            except (json.JSONDecodeError, KeyError, zipfile.BadZipFile):
                continue


def load_sic(
    zip_path: Path,
    con: duckdb.DuckDBPyConnection,
    batch: int = 50_000,
    universe_only: bool = True,
) -> dict:
    """Write SIC code and description onto `securities`."""
    only = None
    if universe_only:
        only = {r[0] for r in con.execute("SELECT DISTINCT cik FROM securities").fetchall()}
    con.execute("CREATE OR REPLACE TABLE sic_map (cik BIGINT PRIMARY KEY, sic VARCHAR, sic_desc VARCHAR)")
    buffer: list[tuple] = []
    counts = {"filers": 0, "with_sic": 0}

    def flush() -> None:
        if not buffer:
            return
        frame = pd.DataFrame(buffer, columns=["cik", "sic", "sic_desc"])
        frame = frame.drop_duplicates(subset=["cik"], keep="first")
        con.register("_sic", frame)
        con.execute("INSERT OR REPLACE INTO sic_map SELECT cik, sic, sic_desc FROM _sic")
        con.unregister("_sic")
        buffer.clear()

    for doc in iter_submissions(zip_path, only=only):
        cik = doc.get("cik")
        if cik is None:
            continue
        counts["filers"] += 1
        sic = (doc.get("sic") or "").strip()
        if sic:
            counts["with_sic"] += 1
        buffer.append((int(cik), sic or None, doc.get("sicDescription") or None))
        if len(buffer) >= batch:
            flush()
    flush()

    con.execute(
        """
        UPDATE securities AS s
        SET sic = m.sic, sector = m.sic_desc
        FROM sic_map AS m
        WHERE m.cik = s.cik
        """
    )
    return counts


def non_operating(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Which securities the classification says are not operating companies."""
    codes = list(NON_OPERATING_SIC)
    return con.execute(
        "SELECT sic, sector, COUNT(*) AS n FROM securities "
        "WHERE sic IN (SELECT UNNEST(?)) GROUP BY 1, 2 ORDER BY n DESC",
        [codes],
    ).fetch_df()


def drop_non_operating(con: duckdb.DuckDBPyConnection) -> int:
    codes = list(NON_OPERATING_SIC)
    before = con.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
    con.execute("DELETE FROM securities WHERE sic IN (SELECT UNNEST(?))", [codes])
    after = con.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
    return before - after
