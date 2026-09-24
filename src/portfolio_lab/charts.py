"""Price charts shared by the tabs.

One layout: the close as a line on top, and — when the stored bars carry a high and a
low — the same days as candles in a panel underneath, on one shared time axis. Both are
visible at once; zooming or hovering one moves the other.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import ui


def price_figure(
    view: pd.DataFrame,
    ticker: str,
    bands: bool = False,
    candles: bool = True,
    trades: pd.DataFrame | None = None,
) -> go.Figure:
    """Line on top, candles below when `candles` is set.

    `view` needs date and close, plus open/high/low for candles and upper/middle/lower
    for bands. `trades`, if given, marks fills on the line: columns date, price, side
    ("buy" or "sell").
    """
    rows = 2 if candles else 1
    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.04,
        row_heights=[0.55, 0.45] if candles else [1.0],
    )
    if bands:
        fig.add_trace(go.Scatter(
            x=view["date"], y=view["upper"], name="upper",
            line=dict(color=ui.CYAN, width=1), hoverinfo="skip"), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=view["date"], y=view["lower"], name="lower",
            line=dict(color=ui.CYAN, width=1), fill="tonexty",
            fillcolor="rgba(77, 184, 255, 0.10)", hoverinfo="skip"), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=view["date"], y=view["middle"], name="20d avg",
            line=dict(color=ui.MUTED, width=1, dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=view["date"], y=view["close"], name=ticker,
        line=dict(color=ui.AMBER, width=1.4)), row=1, col=1)
    if trades is not None and not trades.empty:
        for side, symbol, color in (("buy", "triangle-up", ui.UP),
                                    ("sell", "triangle-down", ui.DOWN)):
            marks = trades[trades.side == side]
            if not marks.empty:
                fig.add_trace(go.Scatter(
                    x=marks["date"], y=marks["price"], mode="markers", name=side,
                    marker=dict(symbol=symbol, size=10, color=color)), row=1, col=1)
    if candles:
        fig.add_trace(go.Candlestick(
            x=view["date"], open=view["open"], high=view["high"],
            low=view["low"], close=view["close"], name=f"{ticker} OHLC",
            increasing=dict(line=dict(color=ui.UP), fillcolor=ui.UP),
            decreasing=dict(line=dict(color=ui.DOWN), fillcolor=ui.DOWN),
        ), row=2, col=1)

    fig.update_layout(
        height=480 if candles else 300, margin=dict(l=0, r=0, t=6, b=0),
        paper_bgcolor=ui.BG, plot_bgcolor=ui.BG, showlegend=False,
        font=dict(family="IBM Plex Mono", color=ui.MUTED, size=10),
        hovermode="x unified",
    )
    # Without the weekend break every candle body shrinks to make room for empty days;
    # the range slider under a candlestick would eat the lower panel.
    fig.update_xaxes(gridcolor=ui.RULE, color=ui.MUTED,
                     rangebreaks=[dict(bounds=["sat", "mon"])],
                     rangeslider=dict(visible=False))
    fig.update_yaxes(gridcolor=ui.RULE, color=ui.MUTED, side="right")
    return fig
