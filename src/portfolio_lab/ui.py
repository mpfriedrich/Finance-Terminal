"""Terminal skin.

The brief is a Bloomberg terminal, so the choices follow that instrument rather than
current web taste:

* True black, not a tinted near-black. A trading terminal is read for hours in a dim
  room; #000 with amber is the combination that display was built around.
* One monospace family throughout. Fixed width is not nostalgia here — it is what makes a
  column of prices scan vertically without the eye re-finding the decimal point each row.
* Density over breathing room. A terminal's value is how much you can see at once, so
  padding is cut to what keeps rows distinguishable and no further.
* Colour carries meaning only: amber labels the structure, white is data, green and red
  are direction. Nothing is coloured for decoration.
"""

from __future__ import annotations

import html

import pandas as pd

BG = "#000000"
PANEL = "#080808"
AMBER = "#FF9E1B"
AMBER_DIM = "#9A6110"
DATA = "#E8E8E8"
MUTED = "#8A8A8A"
CYAN = "#4DB8FF"
UP = "#26D07C"
DOWN = "#FF4B4B"
RULE = "#242424"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap');

html, body, [class*="st-"], .stMarkdown, input, textarea, button {{
    font-family: 'IBM Plex Mono', ui-monospace, Menlo, monospace !important;
}}
/* ...but NOT the icons. Streamlit draws chevrons and avatars as ligatures in the
   Material Symbols font, so forcing a monospace face on them leaves the raw ligature
   name on screen: "arrow_right", "face", "smart_toy". Hand those elements back. */
[data-testid="stIconMaterial"], .material-icons, .material-symbols-rounded,
span[translate="no"] {{
    font-family: 'Material Symbols Rounded', 'Material Icons' !important;
}}
.stApp, [data-testid="stHeader"] {{ background: {BG}; }}
[data-testid="stSidebar"] {{
    background: {PANEL};
    border-right: 1px solid {RULE};
}}
.block-container {{ padding-top: 0.8rem; max-width: 1600px; }}

h1, h2, h3, h4 {{
    color: {AMBER} !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em;
    font-size: 0.82rem !important;
    text-transform: uppercase;
    margin: 0.9rem 0 0.35rem 0 !important;
    border-bottom: 1px solid {RULE};
    padding-bottom: 0.25rem;
}}
p, li, span, label, .stMarkdown {{ color: {DATA}; font-size: 0.80rem; }}
[data-testid="stCaptionContainer"] p {{ color: {MUTED}; font-size: 0.72rem; }}
hr {{ border-color: {RULE} !important; }}

/* Header strip — the terminal's identity line */
.tl-bar {{
    display: flex; justify-content: space-between; align-items: baseline;
    border-top: 2px solid {AMBER}; border-bottom: 1px solid {RULE};
    padding: 0.35rem 0.1rem; margin-bottom: 0.6rem;
}}
.tl-bar .name {{ color: {AMBER}; font-weight: 600; letter-spacing: 0.16em; font-size: 0.86rem; }}
.tl-bar .meta {{ color: {MUTED}; font-size: 0.70rem; }}
.tl-bar .meta b {{ color: {DATA}; font-weight: 500; }}

/* Metric strip */
.tl-stats {{ display: flex; gap: 1.6rem; flex-wrap: wrap; margin: 0.2rem 0 0.6rem 0; }}
.tl-stat .k {{ color: {MUTED}; font-size: 0.66rem; letter-spacing: 0.10em; }}
.tl-stat .v {{ color: {DATA}; font-size: 1.15rem; font-weight: 500; line-height: 1.15; }}
.tl-stat .v.up {{ color: {UP}; }}
.tl-stat .v.down {{ color: {DOWN}; }}
.tl-stat .v.amber {{ color: {AMBER}; }}

/* Data grid */
/* The grid is wide by design — a terminal shows everything at once. Let it scroll
   sideways inside its own box instead of spilling past the edge of the page. */
.tl-scroll {{ overflow-x: auto; width: 100%; padding-bottom: 0.2rem; }}
.tl-scroll::-webkit-scrollbar {{ height: 6px; }}
.tl-scroll::-webkit-scrollbar-thumb {{ background: {RULE}; }}
table.tl {{ border-collapse: collapse; font-size: 0.74rem; min-width: 100%; }}
table.tl th {{
    color: {AMBER}; font-weight: 500; text-align: left; letter-spacing: 0.06em;
    border-bottom: 1px solid {AMBER_DIM}; padding: 0.22rem 0.5rem 0.22rem 0;
    white-space: nowrap;
}}
table.tl td {{
    color: {DATA}; padding: 0.18rem 0.5rem 0.18rem 0;
    border-bottom: 1px solid {RULE}; white-space: nowrap;
}}
table.tl td.num, table.tl th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
table.tl td.tick {{ color: {AMBER}; font-weight: 600; }}
table.tl td.up {{ color: {UP}; }}
table.tl td.down {{ color: {DOWN}; }}
table.tl td.flag {{ color: {DOWN}; }}
table.tl tr:hover td {{ background: #0E0E0E; }}

/* Streamlit chrome, quietened */
.stButton button, .stFormSubmitButton button {{
    background: transparent; color: {AMBER}; border: 1px solid {AMBER_DIM};
    border-radius: 0; font-size: 0.74rem; letter-spacing: 0.06em;
}}
.stButton button:hover, .stFormSubmitButton button:hover {{
    border-color: {AMBER}; color: {BG}; background: {AMBER};
}}
.stTextInput input, .stChatInput textarea, .stChatInput div {{
    background: {PANEL} !important; color: {DATA} !important;
    border-radius: 0 !important; border-color: {RULE} !important;
    font-size: 0.78rem !important;
}}
[data-testid="stChatMessage"] {{
    background: transparent; border-left: 2px solid {RULE};
    padding: 0.1rem 0 0.1rem 0.6rem; gap: 0.5rem;
}}
[data-testid="stChatMessage"] p {{ margin-bottom: 0.2rem; }}
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {{
    background: transparent !important; color: {AMBER};
    width: 1.1rem; min-width: 1.1rem; height: 1.1rem; font-size: 0.8rem;
}}
/* Conversation, rendered as terminal lines rather than chat bubbles. Streamlit's chat
   component brings avatars, rounded cards and generous padding — three things a terminal
   does not have. A prompt character and a hanging indent say the same thing in less
   space, and sidestep the avatar/icon machinery entirely. */
.tl-line {{ display: flex; gap: 0.55rem; padding: 0.12rem 0; font-size: 0.78rem; }}
.tl-line .p {{ color: {AMBER}; font-weight: 600; flex: 0 0 auto; }}
.tl-line.reply .p {{ color: {AMBER_DIM}; }}
.tl-line .t {{ color: {DATA}; white-space: pre-wrap; }}
.tl-line.reply .t {{ color: {MUTED}; }}
.tl-log {{ border-left: 1px solid {RULE}; padding-left: 0.7rem; margin-bottom: 0.5rem; }}

/* Research: a command line, the way a terminal takes a ticker */
.tl-cmd {{ display: flex; align-items: baseline; gap: 0.5rem; margin-bottom: 0.3rem; }}
.tl-cmd .p {{ color: {AMBER}; font-weight: 600; }}

/* Key/value block for a company profile */
.tl-kv {{ display: grid; grid-template-columns: max-content 1fr; gap: 0.1rem 1rem;
         font-size: 0.76rem; margin-bottom: 0.4rem; }}
.tl-kv .k {{ color: {MUTED}; }}
.tl-kv .v {{ color: {DATA}; }}
.tl-kv .v.big {{ color: {AMBER}; font-size: 0.95rem; font-weight: 600; }}

/* Tabs, flattened into terminal function keys */
[data-testid="stTabs"] button {{
    border-radius: 0 !important; font-size: 0.74rem !important;
    letter-spacing: 0.10em; color: {MUTED} !important;
}}
[data-testid="stTabs"] button[aria-selected="true"] {{ color: {AMBER} !important; }}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {{ background: {AMBER} !important; }}
[data-testid="stTabs"] [data-baseweb="tab-border"] {{ background: {RULE} !important; }}

/* Expander summary: keep the label and its chevron on one line */
[data-testid="stExpander"] summary {{ font-size: 0.74rem; padding: 0.3rem 0.5rem; }}
[data-testid="stExpander"] summary p {{ margin: 0; }}
[data-testid="stNotification"], .stAlert {{ border-radius: 0; font-size: 0.74rem; }}
[data-testid="stExpander"] details {{ border: 1px solid {RULE}; border-radius: 0; background: {PANEL}; }}
[data-testid="stMetricValue"] {{ font-size: 1.1rem; color: {DATA}; }}
[data-testid="stMetricLabel"] p {{ color: {MUTED}; font-size: 0.66rem; letter-spacing: 0.08em; }}

/* Phone. A terminal's density does not survive a 390px screen unaltered: the grid
   keeps its columns but scrolls, type steps down one notch rather than shrinking to
   illegibility, and the header wraps instead of squeezing the data out of view. */
@media (max-width: 640px) {{
    .block-container {{ padding: 0.5rem 0.6rem 4rem 0.6rem !important; }}
    .tl-bar {{ flex-direction: column; gap: 0.15rem; }}
    .tl-bar .meta {{ font-size: 0.66rem; line-height: 1.5; }}
    .tl-stats {{ gap: 0.9rem 1.1rem; }}
    .tl-stat .v {{ font-size: 1rem; }}
    .tl-stat .k {{ font-size: 0.62rem; }}
    table.tl {{ font-size: 0.70rem; }}
    table.tl th, table.tl td {{ padding-right: 0.65rem; }}
    h1, h2, h3, h4 {{ font-size: 0.78rem !important; margin-top: 0.7rem !important; }}
    p, li, span, label, .stMarkdown {{ font-size: 0.78rem; }}
    /* Streamlit stacks columns on narrow screens already; stop them adding a gap
       big enough to push the table below the fold. */
    [data-testid="stHorizontalBlock"] {{ gap: 0.4rem !important; }}
}}
</style>
"""


def bar(session: str, meta: dict[str, str]) -> str:
    cells = " ".join(f"{k} <b>{html.escape(str(v))}</b>" for k, v in meta.items())
    return (
        f'<div class="tl-bar"><span class="name">{html.escape(session)}</span>'
        f'<span class="meta">{cells}</span></div>'
    )


def stats(items: list[tuple[str, str, str]]) -> str:
    """items: (label, value, tone) where tone is '', 'up', 'down' or 'amber'."""
    cells = "".join(
        f'<div class="tl-stat"><div class="k">{html.escape(k)}</div>'
        f'<div class="v {tone}">{html.escape(v)}</div></div>'
        for k, v, tone in items
    )
    return f'<div class="tl-stats">{cells}</div>'


def kv(pairs: list[tuple[str, str, bool]]) -> str:
    """Key/value block. pairs: (label, value, emphasise)."""
    cells = "".join(
        f'<div class="k">{html.escape(k)}</div>'
        f'<div class="v{" big" if big else ""}">{html.escape(str(v))}</div>'
        for k, v, big in pairs
    )
    return f'<div class="tl-kv">{cells}</div>'


def log(entries: list[tuple[str, str]]) -> str:
    """The conversation as a terminal log. entries: (role, text)."""
    if not entries:
        return ""
    lines = []
    for role, text in entries:
        reply = role != "user"
        prompt = "·" if reply else ">"
        body = html.escape(text).replace("\n", "<br>")
        lines.append(
            f'<div class="tl-line{" reply" if reply else ""}">'
            f'<span class="p">{prompt}</span><span class="t">{body}</span></div>'
        )
    return f'<div class="tl-log">{"".join(lines)}</div>'


def grid(
    frame: pd.DataFrame,
    numeric: set[str] = frozenset(),
    signed: set[str] = frozenset(),
    ticker_col: str = "ticker",
) -> str:
    """Render a dense terminal grid.

    Numbers are right-aligned with tabular figures so decimal points line up down the
    column — the reason a terminal uses fixed width in the first place. Signed columns
    take their colour from their sign, which is the one place colour is allowed to mean
    something on its own.
    """
    if frame.empty:
        return f'<p style="color:{MUTED}">No rows.</p>'

    head = "".join(
        f'<th class="{"num" if c in numeric or c in signed else ""}">'
        f'{html.escape(c.replace("_", " "))}</th>'
        for c in frame.columns
    )
    rows = []
    for row in frame.itertuples(index=False):
        cells = []
        for col, value in zip(frame.columns, row):
            klass = []
            if col in numeric or col in signed:
                klass.append("num")
            if col == ticker_col:
                klass.append("tick")
            text = "—" if pd.isna(value) else str(value)
            if col in signed and isinstance(text, str):
                if text.startswith("+"):
                    klass.append("up")
                elif text.startswith("-") or text.startswith("−"):
                    klass.append("down")
            cells.append(f'<td class="{" ".join(klass)}">{html.escape(text)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div class="tl-scroll"><table class="tl">'
        f'<thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody>'
        "</table></div>"
    )


def num(value, digits: int = 2) -> str:
    return "—" if pd.isna(value) else f"{value:,.{digits}f}"


def money(value) -> str:
    if pd.isna(value):
        return "—"
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= size:
            return f"{value / size:,.2f}{unit}"
    return f"{value:,.0f}"


def pct(value, digits: int = 2) -> str:
    return "—" if pd.isna(value) else f"{value:+.{digits}%}"
