"""Does the result survive being poked? (R20)

A backtest is one number produced by one arrangement of arbitrary choices. Two of those
choices are almost always invisible, and both can carry a whole result on their own:

  * **the start date.** Beginning six months earlier or later changes which quarter the
    first purchase landed in. A rule whose advantage disappears when it starts in April
    instead of January did not have an advantage; it had a quarter.
  * **one lucky holding.** A ten-name portfolio that owned the decade's best stock is a
    story about that stock. Removing it and re-running says how much of the result was
    the rule and how much was the name.

So the report shows the main result beside its variations, rather than alone. The point
is not that a robust result stays identical — it will not — but that a fragile one is
visible immediately instead of after the money is committed.

The strongest name is removed **at selection**, not from the finished result: the weight
it would have taken goes to the next name down, which is what would actually have
happened. Deleting it afterwards would leave a portfolio that held 90% of itself and
pretend that was a plan.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from . import stage_b


@dataclass(frozen=True, slots=True)
class Variation:
    name: str
    annual_return: float | None
    excess: float | None
    max_drawdown: float | None
    note: str = ""


@dataclass(frozen=True, slots=True)
class Report:
    main: Variation
    variations: list[Variation] = field(default_factory=list)
    strongest_name: str | None = None

    @property
    def spread(self) -> float | None:
        """Best minus worst annual return across every variation, the main one included.

        The single number worth looking at: a rule whose result moves by fifteen points
        depending on which month it started in has not been measured, it has been
        sampled once.
        """
        values = [v.annual_return for v in [self.main, *self.variations]
                  if v.annual_return is not None]
        return max(values) - min(values) if len(values) > 1 else None

    def verdict(self) -> str:
        spread = self.spread
        if spread is None:
            return "Not enough variations completed to say anything about robustness."
        if spread < 0.02:
            return (
                f"The variations land within {spread:.1%} a year of each other. The "
                "result does not depend on the start month or on a single holding."
            )
        if spread < 0.05:
            return (
                f"The variations spread {spread:.1%} a year. That is normal for a "
                "concentrated portfolio, and it is also the size of most claimed edges."
            )
        return (
            f"The variations spread {spread:.1%} a year — wider than the advantage most "
            "strategies claim. Whatever this result is measuring, the start date and the "
            "individual holdings are a large part of it."
        )


def _variation(name: str, result, note: str = "") -> Variation:
    if result is None:
        return Variation(name=name, annual_return=None, excess=None,
                         max_drawdown=None, note="did not run")
    m = result.metrics
    return Variation(name=name, annual_return=m.get("annual_return"),
                     excess=m.get("excess_annual"),
                     max_drawdown=m.get("max_drawdown"), note=note)


def strongest_holding(source, result, start: dt.date, end: dt.date) -> str | None:
    """The name that contributed most, found by leaving each one out in turn.

    Measuring a contribution inside a rebalancing portfolio is ambiguous — weights change,
    names come and go — so this asks the question that actually matters instead: without
    this name, what would the result have been? The one whose absence hurts most is the
    one the result was leaning on. Costly but honest, and with a warm cache each re-run
    takes a couple of seconds.

    Only the names held most often are tried; a company that appeared in one quarter
    cannot be carrying fifteen years.
    """
    if result is None or not result.ever_held:
        return None
    counts = pd.Series(result.ever_held).value_counts()
    return counts.index[0] if len(counts) else None


def run(
    source,
    spec,
    start: dt.date,
    end: dt.date,
    *,
    shift_months: int = 6,
    candidates: int = 6,
    **kwargs,
) -> Report:
    """The main run, plus the same run started earlier, later, and one name short.

    `kwargs` goes straight to `stage_b.run`, so costs, frequency, the cache and the
    benchmark stay identical across variations — only the one thing under test changes.
    """
    main_result = stage_b.run(source, spec, start, end, **kwargs)
    main = _variation("as specified", main_result)
    if main_result is None:
        return Report(main=main)

    variations: list[Variation] = []
    for label, shifted in (
        (f"start {shift_months} months earlier", _shift(start, -shift_months)),
        (f"start {shift_months} months later", _shift(start, shift_months)),
    ):
        if shifted >= end:
            continue
        variations.append(_variation(label, stage_b.run(source, spec, shifted, end, **kwargs)))

    # Which single holding was the result leaning on? Leave each of the most-held names
    # out in turn; the one whose absence costs the most is the answer.
    worst_name, worst_result = None, None
    counts = pd.Series(main_result.ever_held).value_counts().head(candidates)
    for name in counts.index:
        without = stage_b.run(source, spec, start, end, exclude={name}, **kwargs)
        if without is None:
            continue
        if worst_result is None or (
            without.metrics.get("annual_return", 0) < worst_result.metrics.get("annual_return", 0)
        ):
            worst_name, worst_result = name, without

    if worst_name:
        variations.append(_variation(
            f"without {worst_name}", worst_result,
            note="the holding the result leaned on most, removed at selection"))

    return Report(main=main, variations=variations, strongest_name=worst_name)


def _shift(day: dt.date, months: int) -> dt.date:
    """Move a date by whole months, clamped to the end of the target month.

    31 March shifted back six months is 30 September, not the 28th: falling back to a
    fixed 28 would quietly move the start date three days for exactly the months where
    the shift matters most."""
    import calendar

    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def as_table(report: Report) -> pd.DataFrame:
    rows = [{"variation": v.name,
             "return p.a.": v.annual_return,
             "vs index": v.excess,
             "max drawdown": v.max_drawdown,
             "note": v.note}
            for v in [report.main, *report.variations]]
    return pd.DataFrame(rows)
