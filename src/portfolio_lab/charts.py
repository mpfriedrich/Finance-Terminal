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


# A change beyond this many percent gets the full colour. Hormuz at −96 % (2026) would
# otherwise stretch the scale until every ordinary ±10 % looked the same grey.
MAP_COLOUR_CAP = 0.5


def chokepoint_map(placed: pd.DataFrame, tankers: bool = False) -> go.Figure:
    """World map of the chokepoints: circle area for ships a day, colour for the change
    against a year earlier (red fewer, green more, grey no comparison).

    `placed` is shipping.on_map(dashboard). Drawn with plotly's built-in coastlines, so
    no map tiles are fetched from anyone's server.
    """
    count = placed["tankers_per_day" if tankers else "ships_per_day"].fillna(0.0)
    change = placed["tankers_vs_year_ago" if tankers else "vs_year_ago"]
    # Area, not radius, carries the number: the square root keeps 3 ships at Hormuz
    # visible next to 218 at Malacca. Kept small, because Suez, Bab el-Mandeb and Hormuz
    # (and the straits around Taiwan) sit close together; fixed name labels overlapped
    # there on a laptop-width pane, so the names are in the hover and the table below.
    size = 5 + 1.7 * count.clip(lower=0) ** 0.5
    what = "tankers" if tankers else "ships"
    hover = [
        f"<b>{n}</b><br>{c:,.0f} {what}/day (7-day avg)<br>"
        + ("no year-earlier value" if pd.isna(v) else f"{v:+.0%} vs a year ago")
        + f"<br>as of {d:%d.%m.%Y}"
        for n, c, v, d in zip(placed.chokepoint, count, change, placed.as_of)
    ]
    return _change_bubbles(placed.lat, placed.lon, size, change, hover)


def port_map(board: pd.DataFrame) -> go.Figure:
    """The watched ports (shipping.port_board): circle area for tonnes loaded plus
    unloaded a day, colour for the change against a year earlier."""
    kt = board.total_t.fillna(0.0).clip(lower=0) / 1e3
    # Singapore at ~2,200 kt a day against a near-empty port: the square root again.
    size = 5 + 0.7 * kt ** 0.5
    hover = [
        f"<b>{p}</b> ({c}) — {k}<br>mostly {m or '—'}<br>"
        f"out {_kt(e)} · in {_kt(i)} a day (7-day avg)<br>"
        + ("no year-earlier value" if pd.isna(v) else f"{v:+.0%} vs a year ago")
        + f"<br>as of {d:%d.%m.%Y}"
        for p, c, k, m, e, i, v, d in zip(board.port, board.country, board.known_for,
                                          board.mostly, board.export_t, board.import_t,
                                          board.total_vs_year_ago, board.as_of)
    ]
    return _change_bubbles(board.lat, board.lon, size, board.total_vs_year_ago, hover)


def _kt(tonnes) -> str:
    return "—" if tonnes is None or pd.isna(tonnes) else f"{tonnes / 1e3:,.0f} kt"


def _change_bubbles(lat, lon, size, change, hover) -> go.Figure:
    """Circles on the dark world, coloured by a change against a year earlier and capped
    at ±MAP_COLOUR_CAP; a missing comparison is grey."""
    colour = pd.Series(change, dtype=float).clip(-MAP_COLOUR_CAP, MAP_COLOUR_CAP)
    fig = go.Figure(go.Scattergeo(
        lat=lat, lon=lon, mode="markers", hovertext=hover, hoverinfo="text",
        marker=dict(
            size=size, sizemode="diameter", opacity=0.85,
            color=colour.fillna(0.0), cmin=-MAP_COLOUR_CAP, cmax=MAP_COLOUR_CAP,
            colorscale=[[0, ui.DOWN], [0.5, ui.MUTED], [1, ui.UP]],
            line=dict(width=0.6, color=ui.BG),
            colorbar=dict(
                title=dict(text="vs year ago", side="right"), thickness=8, len=0.6,
                tickvals=[-0.5, -0.25, 0, 0.25, 0.5],
                ticktext=["≤−50%", "−25%", "0", "+25%", "≥+50%"],
                tickfont=dict(color=ui.MUTED, size=9), outlinewidth=0),
        ),
    ))
    _world_geo(fig)
    return fig


def _world_geo(fig: go.Figure) -> None:
    """The dark world the traffic maps share: built-in coastlines, no tiles fetched."""
    fig.update_geos(
        projection_type="natural earth", showframe=False, bgcolor=ui.BG,
        showland=True, landcolor=ui.PANEL, showocean=True, oceancolor=ui.BG,
        showcoastlines=True, coastlinecolor=ui.RULE, showcountries=True,
        countrycolor=ui.RULE, showlakes=False, lataxis_range=[-58, 75],
    )
    fig.update_layout(
        height=400, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor=ui.BG,
        font=dict(family="IBM Plex Mono", color=ui.MUTED, size=10),
    )


def aircraft_map(cells: pd.DataFrame, taken_at, what: str = "aircraft",
                 colour: str = ui.CYAN) -> go.Figure:
    """Airborne aircraft per grid cell (aviation.grid): circle area for the count."""
    count = cells["aircraft"].astype(float)
    fig = go.Figure(go.Scattergeo(
        lat=cells.lat, lon=cells.lon, mode="markers",
        hovertext=[f"{int(n)} {what} in this cell<br>{taken_at:%d.%m.%Y %H:%M} UTC"
                   for n in count],
        hoverinfo="text",
        marker=dict(size=2 + 1.6 * count ** 0.5, sizemode="diameter", color=colour,
                    opacity=0.6, line=dict(width=0)),
    ))
    _world_geo(fig)
    return fig
