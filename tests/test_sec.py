"""Reading the SEC company-facts documents.

Until 24.09.2026 only amounts in USD under US-GAAP tags were read: every filer that
reports in another currency, or under IFRS, had no fundamentals at all. These tests pin
the old behaviour for domestic filers and the new one for foreign ones.
"""
from portfolio_lab.data import sec


def fact(val, end, filed, form="10-K", start=None, accn="a1"):
    e = {"val": val, "end": end, "filed": filed, "form": form, "accn": accn}
    if start:
        e["start"] = start
    return e


def doc(facts, cik=1):
    return {"cik": cik, "facts": facts}


def rows_by_metric(rows):
    out = {}
    for r in rows:
        out.setdefault(r[1], []).append(r)
    return out


def test_a_domestic_filer_reads_as_before_and_is_marked_usd():
    rows = sec.extract_rows(doc({"us-gaap": {
        "StockholdersEquity": {"units": {"USD": [fact(900.0, "2023-12-31", "2024-02-15")]}},
    }, "dei": {
        "EntityCommonStockSharesOutstanding": {"units": {"shares": [
            fact(100.0, "2024-02-01", "2024-02-15")]}},
    }}))
    by = rows_by_metric(rows)
    assert by["book_value"][0][2] == 900.0 and by["book_value"][0][9] == "USD"
    assert by["shares_outstanding"][0][9] is None, "a share count has no currency"
    assert by["book_value"][0][10] == "10-K"


def test_an_ifrs_filer_in_euros_now_has_fundamentals():
    rows = sec.extract_rows(doc({"ifrs-full": {
        "EquityAttributableToOwnersOfParent": {"units": {"EUR": [
            fact(5_000.0, "2023-12-31", "2024-03-01", form="20-F")]}},
        "Revenue": {"units": {"EUR": [
            fact(2_000.0, "2023-12-31", "2024-03-01", form="20-F", start="2023-01-01")]}},
    }}))
    by = rows_by_metric(rows)
    assert by["book_value"][0][2] == 5_000.0
    assert by["book_value"][0][9] == "EUR" and by["book_value"][0][10] == "20-F"
    assert by["revenue"][0][9] == "EUR"


def test_a_us_gaap_filer_reporting_in_yen_is_no_longer_dropped():
    rows = sec.extract_rows(doc({"us-gaap": {
        "StockholdersEquity": {"units": {"JPY": [
            fact(9e12, "2024-03-31", "2024-06-20", form="20-F")]}},
    }}))
    assert rows and rows[0][9] == "JPY"


def test_a_convenience_translation_does_not_sit_beside_the_real_figure():
    """Some filers tag a figure in their currency AND a USD translation of it. One
    currency per filer — the one most of its figures use — or the same period would
    carry two values in two units."""
    rows = sec.extract_rows(doc({"ifrs-full": {
        "Equity": {"units": {
            "EUR": [fact(100.0, "2022-12-31", "2023-03-01", form="20-F"),
                    fact(110.0, "2023-12-31", "2024-03-01", form="20-F")],
            "USD": [fact(121.0, "2023-12-31", "2024-03-01", form="20-F")],
        }},
    }}))
    assert {r[9] for r in rows} == {"EUR"}
    assert sorted(r[2] for r in rows) == [100.0, 110.0]


def test_per_share_units_are_not_mistaken_for_money():
    rows = sec.extract_rows(doc({"us-gaap": {
        "StockholdersEquity": {"units": {"USD/shares": [
            fact(9.0, "2023-12-31", "2024-02-15")]}},
    }}))
    assert rows == []


def test_the_earliest_filing_of_a_value_is_kept():
    rows = sec.extract_rows(doc({"us-gaap": {
        "StockholdersEquity": {"units": {"USD": [
            fact(900.0, "2023-12-31", "2025-02-15", form="10-K", accn="late"),
            fact(900.0, "2023-12-31", "2024-02-15", form="10-K", accn="early"),
        ]}},
    }}))
    assert len(rows) == 1 and rows[0][6] == "early"


def test_the_reporting_currency_is_the_one_most_figures_use():
    facts = {"ifrs-full": {
        "Assets": {"units": {"GBP": [fact(1, "2023-12-31", "2024-03-01")] * 5,
                             "USD": [fact(1, "2023-12-31", "2024-03-01")]}},
    }}
    assert sec.reporting_currency(facts) == "GBP"
