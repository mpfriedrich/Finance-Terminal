"""Price charts shared by the tabs.

One layout, the one a trading terminal uses: price on top — a line, or candles when the
bars carry a high and a low — and volume underneath as bars with its moving average, on
one shared time axis. Zooming or hovering one panel moves the other.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import ui

VOLUME_AVERAGE_DAYS = 15


def price_figure(
    view: pd.DataFrame,
    ticker: str,
    bands: bool = False,
    candles: bool = False,
    trades: pd.DataFrame | None = None,
    volume_average_days: int = VOLUME_AVERAGE_DAYS,
) -> go.Figure:
    """Price on top (line, or candles with `candles`), volume below when `view` has it.

    `view` needs date and close; open/high/low for candles; upper/middle/lower for
    bands; volume for the lower panel. `trades`, if given, marks fills on the price:
    columns date, price, side ("buy" or "sell").
    """
    has_volume = "volume" in view.columns and view["volume"].fillna(0).gt(0).any()
    rows = 2 if has_volume else 1
    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.72, 0.28] if has_volume else [1.0],
    )

    # --- top: price ----------------------------------------------------------------
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
    if candles:
        fig.add_trace(go.Candlestick(
            x=view["date"], open=view["open"], high=view["high"],
            low=view["low"], close=view["close"], name=ticker,
            increasing=dict(line=dict(color=ui.UP), fillcolor=ui.UP),
            decreasing=dict(line=dict(color=ui.DOWN), fillcolor=ui.DOWN),
        ), row=1, col=1)
    else:
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

    # --- bottom: volume ------------------------------------------------------------
    if has_volume:
        # Coloured by the day's direction, close against the previous close — the
        # same reading as the candle colours above it.
        up = view["close"].diff().fillna(0) >= 0
        fig.add_trace(go.Bar(
            x=view["date"], y=view["volume"], name="volume",
            marker=dict(color=[ui.UP if u else ui.DOWN for u in up], opacity=0.55,
                        line=dict(width=0))), row=2, col=1)
        avg = view["volume"].rolling(volume_average_days,
                                     min_periods=volume_average_days).mean()
        fig.add_trace(go.Scatter(
            x=view["date"], y=avg, name=f"{volume_average_days}d avg volume",
            line=dict(color=ui.CYAN, width=1.2)), row=2, col=1)

    fig.update_layout(
        height=460 if has_volume else 320, margin=dict(l=0, r=0, t=6, b=0),
        paper_bgcolor=ui.BG, plot_bgcolor=ui.BG, showlegend=False, bargap=0.15,
        font=dict(family="IBM Plex Mono", color=ui.MUTED, size=10),
        hovermode="x unified",
    )
    # Without the weekend break every bar and candle shrinks to make room for empty
    # days; the range slider plotly puts under a candlestick would eat the volume panel.
    fig.update_xaxes(gridcolor=ui.RULE, color=ui.MUTED,
                     rangebreaks=[dict(bounds=["sat", "mon"])],
                     rangeslider=dict(visible=False))
    fig.update_yaxes(gridcolor=ui.RULE, color=ui.MUTED, side="right")
    return fig
