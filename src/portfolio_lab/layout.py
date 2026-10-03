"""Names of the app's tabs and of the SECURITY sections, in one place.

app.py builds its tabs from these and command.py routes function codes onto them, so a
renamed tab cannot silently break the command bar: tests/test_command.py checks every
routing target against these tuples.
"""

TAB_LABELS = ("SECURITY", "SCREEN & BACKTEST", "PORTFOLIOS", "MARKETS & MACRO", "WORLD & LOGISTICS", "EVENTS & NEWS")
SECURITY_SECTIONS = ("OVERVIEW", "FINANCIALS", "VALUATION", "OPTIONS", "EXPOSURE", "ANALYSTS", "OWNERSHIP", "NEWS & FILINGS")
