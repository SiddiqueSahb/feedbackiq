"""
Shared styling for the FeedbackIQ frontend.

Raw CSS instead of st.columns: the pinned Streamlit version doesn't reflow on
narrow screens, so KPI rows use CSS grid (auto-fit/minmax) instead. Streamlit
widgets stay as widgets — only the presentational cards are hand-built.
"""

import streamlit as st

NAVY = "#1F3864"

CSS = """
<style>
:root {
  --fiq-navy:   #1F3864;
  --fiq-navy-2: #2E5496;
  --fiq-ink:    #1A1C1E;
  --fiq-muted:  #5F6570;
  --fiq-line:   #E4E7EC;
  --fiq-soft:   #F6F8FC;
  --fiq-pos:    #2ECC71;
  --fiq-neg:    #E74C3C;
  --fiq-neu:    #F39C12;
}

/* Give the app room to breathe, and let it use the width it has. */
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px; }

/* ---------- hero ---------- */
.fiq-hero {
  background: linear-gradient(135deg, var(--fiq-navy) 0%, var(--fiq-navy-2) 100%);
  color: #fff; border-radius: 16px;
  padding: 30px 34px; margin-bottom: 22px;
}
.fiq-hero h1 { font-size: 2.3rem; font-weight: 700; margin: 0 0 8px 0; color: #fff; letter-spacing: -0.5px; }
.fiq-hero p  { font-size: 1.05rem; line-height: 1.6; margin: 0 0 14px 0; color: rgba(255,255,255,0.93); max-width: 76ch; }
.fiq-hero .fiq-meta { font-size: 0.87rem; color: rgba(255,255,255,0.75); margin: 0; }

/* ---------- responsive grids ---------- */
.fiq-grid { display: grid; gap: 14px; margin-bottom: 8px; }
.fiq-kpis  { grid-template-columns: repeat(auto-fit, minmax(155px, 1fr)); }
.fiq-cards { grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }

/* ---------- cards ---------- */
.fiq-card {
  background: #fff; border: 1px solid var(--fiq-line);
  border-radius: 12px; padding: 16px 18px;
}
.fiq-kpi { text-align: center; }
.fiq-kpi .v { font-size: 1.6rem; font-weight: 700; color: var(--fiq-navy); line-height: 1.2; }
.fiq-kpi .l { font-size: 0.8rem; color: var(--fiq-muted); text-transform: uppercase; letter-spacing: 0.4px; margin-top: 4px; }

/* ---------- research question cards ---------- */
.fiq-rq {
  background: #fff; border: 1px solid var(--fiq-line);
  border-left: 4px solid var(--fiq-navy);
  border-radius: 12px; padding: 18px 20px; margin-bottom: 14px;
}
.fiq-rq .tag {
  display: inline-block; background: var(--fiq-soft); color: var(--fiq-navy);
  font-size: 0.74rem; font-weight: 700; letter-spacing: 0.6px;
  padding: 3px 10px; border-radius: 999px; margin-bottom: 10px;
}
.fiq-rq .q { font-size: 1.0rem; font-weight: 600; color: var(--fiq-ink); line-height: 1.5; margin-bottom: 10px; }
.fiq-rq .a { font-size: 0.94rem; color: #3C4043; line-height: 1.65; margin: 0; }
.fiq-rq .a b { color: var(--fiq-navy); }

/* ---------- stage cards ---------- */
.fiq-stage .n {
  font-size: 0.74rem; font-weight: 700; color: var(--fiq-navy);
  letter-spacing: 0.6px; text-transform: uppercase; margin-bottom: 6px;
}
.fiq-stage .t { font-size: 1.05rem; font-weight: 700; color: var(--fiq-ink); margin-bottom: 8px; }
.fiq-stage .d { font-size: 0.9rem; color: #3C4043; line-height: 1.6; margin-bottom: 10px; }
.fiq-stage .p { font-size: 0.82rem; color: var(--fiq-muted); font-style: italic; margin: 0; }

/* ---------- section headings ---------- */
.fiq-h { font-size: 1.35rem; font-weight: 700; color: var(--fiq-ink); margin: 26px 0 4px 0; }
.fiq-sub { font-size: 0.92rem; color: var(--fiq-muted); margin: 0 0 16px 0; }

/* ---------- sidebar and page menu ---------- */
/* Streamlit builds the page menu from the filenames in pages/. Left alone it
   renders as tight, small-caps links with no breathing room and no clear
   indication of which page you are on. */
section[data-testid="stSidebar"] { background: var(--fiq-soft); border-right: 1px solid var(--fiq-line); }
section[data-testid="stSidebar"] > div { padding-top: 1.1rem; }

/* Streamlit caps the nav container's height and scrolls inside it. Taller
   items therefore mean FEWER visible pages, not a taller menu — with six
   pages and roomier rows the last two dropped below the fold, where the
   internal scrollbar is easy to miss entirely. Release the cap so the menu
   grows to fit however many pages exist. */
div[data-testid="stSidebarNav"] {
  padding-top: 0.25rem;
  max-height: none !important;
}
div[data-testid="stSidebarNav"] > ul,
div[data-testid="stSidebarNav"] ul {
  padding-left: 0.35rem;
  max-height: none !important;
  overflow: visible !important;
}

div[data-testid="stSidebarNav"] li a,
section[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] {
  display: flex; align-items: center;
  border-radius: 8px;
  padding: 6px 12px; margin: 1px 0;
  font-size: 0.94rem; font-weight: 500;
  color: var(--fiq-ink);
  transition: background 120ms ease, color 120ms ease;
}
div[data-testid="stSidebarNav"] li a:hover,
section[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"]:hover {
  background: rgba(31,56,100,0.08); color: var(--fiq-navy);
}
/* Current page */
div[data-testid="stSidebarNav"] li a[aria-current="page"] {
  background: rgba(31,56,100,0.12);
  color: var(--fiq-navy); font-weight: 600;
}
div[data-testid="stSidebarNav"] li a span { font-size: 0.95rem; }

/* Sidebar headings and captions, so filters don't read louder than the nav */
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
  font-size: 1rem; font-weight: 700; color: var(--fiq-navy);
  letter-spacing: 0.2px; margin-top: 0.9rem; margin-bottom: 0.4rem;
}
section[data-testid="stSidebar"] .stCaption,
section[data-testid="stSidebar"] small { color: var(--fiq-muted); }

/* In-page navigation links (the Explore row) sit level and fill their cell */
a[data-testid="stPageLink-NavLink"] {
  border: 1px solid var(--fiq-line); border-radius: 8px;
  padding: 10px 14px; background: #fff;
  justify-content: flex-start;
}
a[data-testid="stPageLink-NavLink"]:hover { border-color: var(--fiq-navy); background: var(--fiq-soft); }

/* ---------- Streamlit widget polish ---------- */
div[data-testid="stMetric"] { background:#fff; border:1px solid var(--fiq-line); border-radius:10px; padding:12px 14px; }
div[data-testid="stMetricValue"] { font-size: 1.45rem; color: var(--fiq-navy); }
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] { border-radius: 8px 8px 0 0; padding: 8px 16px; }
.stButton button, .stDownloadButton button { border-radius: 8px; font-weight: 500; }

/* ---------- narrow screens ---------- */
@media (max-width: 720px) {
  .block-container { padding-left: 1rem; padding-right: 1rem; }
  .fiq-hero { padding: 22px 20px; border-radius: 12px; }
  .fiq-hero h1 { font-size: 1.75rem; }
  .fiq-hero p  { font-size: 0.97rem; }
  .fiq-grid { gap: 10px; }
  .fiq-kpi .v { font-size: 1.35rem; }
}
</style>
"""


def inject() -> None:
    """Apply the stylesheet. Safe to call on every page; Streamlit dedupes."""
    st.markdown(CSS, unsafe_allow_html=True)


def hero(title: str, lead: str, meta: str = "") -> None:
    st.markdown(
        f"""<div class="fiq-hero">
              <h1>{title}</h1>
              <p>{lead}</p>
              {f'<p class="fiq-meta">{meta}</p>' if meta else ''}
            </div>""",
        unsafe_allow_html=True,
    )


def section(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div class="fiq-h">{title}</div>'
        + (f'<div class="fiq-sub">{subtitle}</div>' if subtitle else ""),
        unsafe_allow_html=True,
    )


def kpi_grid(items: list[tuple[str, str]]) -> None:
    """items: [(label, value), ...] — reflows from one row to one column."""
    cells = "".join(
        f'<div class="fiq-card fiq-kpi"><div class="v">{value}</div>'
        f'<div class="l">{label}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div class="fiq-grid fiq-kpis">{cells}</div>', unsafe_allow_html=True)
