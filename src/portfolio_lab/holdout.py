"""The withheld period: data the search is not allowed to see (R21).

Every backtest in this app is run by someone who already knows what the last fifteen
years did. That is not a character flaw, it is unavoidable — and it is why a good-looking
curve proves so little. Filters get tried, one of them looks best, and the number that
comes out is the maximum of many attempts rather than an estimate of anything.

The defence is boring and effective: hold back the most recent two years, run every
experiment on the rest, and only at the end — once, deliberately — look at what was held
back. If the result survives, that is evidence. If it collapses, the search found a shape
in the noise, and it cost nothing to learn.

Three properties make it work, and all three are easy to lose:

  * **Withheld by default.** An opt-in reserve is not a reserve; it is a setting nobody
    changes when the curve already looks good.
  * **The unlock is recorded.** `backtest_runs.holdout_unlocked` carries it, so a later
    reader can tell which numbers came from the protected period.
  * **It is spent when it is used.** Once the withheld years have been seen, tuning
    against them makes them part of the search like everything else. The app cannot stop
    that, but it can count it and say so, which is what `burn_count` is for.

The window is defined against the LATEST DATA DATE, not against today: a store that has
not been refreshed for a month would otherwise silently shrink its own reserve.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Window:
    """The period a backtest may use, and what was held back from it."""

    start: dt.date
    end: dt.date
    withheld_from: dt.date | None      # None when nothing is held back
    withheld_to: dt.date | None
    unlocked: bool

    @property
    def withheld_days(self) -> int:
        if not self.withheld_from or not self.withheld_to:
            return 0
        return (self.withheld_to - self.withheld_from).days

    def describe(self) -> str:
        if self.unlocked:
            return (
                f"Withheld period UNLOCKED: this run includes {self.withheld_from} to "
                f"{self.withheld_to}, which was deliberately kept out of every earlier "
                "experiment. It is now part of the searched data like everything else — "
                "a second look at it proves less than this one did."
            )
        if not self.withheld_from:
            return (
                "No period is being withheld, so nothing here is out of sample. Every "
                "figure was produced on data the search could see."
            )
        return (
            f"Withheld: {self.withheld_from} to {self.withheld_to} is NOT in this result. "
            "Tune the rule on what you see, then unlock that period once. If the result "
            "holds up there, it is worth something; if it falls apart, the search found "
            "a shape in the noise — and finding that out costs nothing."
        )


def window(
    latest_data: dt.date,
    start: dt.date,
    years: int = 2,
    unlocked: bool = False,
) -> Window:
    """The usable window, with the last `years` held back unless explicitly unlocked.

    Measured from the newest data rather than from today, so a store that has not been
    refreshed does not quietly hand back some of its own reserve.
    """
    if years <= 0 or unlocked:
        return Window(start=start, end=latest_data,
                      withheld_from=(_shift(latest_data, years) if years > 0 else None),
                      withheld_to=(latest_data if years > 0 else None),
                      unlocked=bool(years > 0 and unlocked))

    cut = _shift(latest_data, years)
    if cut <= start:
        # The reserve would swallow the whole window. Refusing is better than returning
        # an empty backtest that looks like a result of zero.
        raise ValueError(
            f"holding back {years} years leaves nothing to test: the window would end "
            f"{cut}, at or before its start {start}. Start earlier, or unlock."
        )
    return Window(start=start, end=cut, withheld_from=cut, withheld_to=latest_data,
                  unlocked=False)


def _shift(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:                     # 29 February
        return day.replace(year=day.year - years, day=28)


def burn_count(store, spec_fingerprint: str, data_fingerprint: str) -> int:
    """How often the withheld period has already been looked at for this exact rule.

    Once is evidence. Twice is the beginning of a second search, this time against the
    data that was supposed to settle the first one.
    """
    row = store.con.execute(
        "SELECT COUNT(*) FROM backtest_runs WHERE spec_fingerprint = ? "
        "AND data_fingerprint = ? AND holdout_unlocked",
        [spec_fingerprint, data_fingerprint],
    ).fetchone()
    return int(row[0]) if row else 0


def burn_warning(times: int) -> str:
    """What to say once the reserve has been spent."""
    if times <= 0:
        return ""
    if times == 1:
        return (
            "The withheld period has been opened once for this rule. That reading was "
            "the out-of-sample one; everything after it is in sample."
        )
    return (
        f"The withheld period has now been opened {times}× for this rule. It is no longer "
        "a reserve — it has become part of the search, and a result that looks good here "
        "says no more than one that looked good before it was unlocked."
    )
