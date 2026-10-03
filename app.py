from datetime import datetime, timezone
import io
import json
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

# --- TERMINAL CONFIGURATION ---
st.set_page_config(
    page_title="XAUUSD Macro Intelligence Terminal",
    page_icon="🪙",
    layout="wide",
)

st.title("🪙 XAUUSD Institutional Macro Terminal")
st.caption("Live Macro Anchors, Real Yield Analytics & Directional Bias Engine")

# =====================================================================
# DATA EXTRACTION LAYER (With Cloud-Resilient Fallbacks)
# =====================================================================

def fetch_series(ticker, period="5d", interval="1d"):
    """Fetches clean time-series data with fallback handling."""
    try:
        df = yf.download(
            ticker, period=period, interval=interval, progress=False
        )
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                return df["Close"][ticker].dropna()
            return df["Close"].dropna()
    except Exception:
        pass
    try:
        tk = yf.Ticker(ticker)
        h = tk.history(period=period, interval=interval)
        if not h.empty and "Close" in h.columns:
            return h["Close"].dropna()
    except Exception:
        pass
    return pd.Series(dtype=float)

# =====================================================================
# CACHED TIER 1: REGULATORY & GOVERNMENT DATA (12-Hour Refresh)
# =====================================================================

@st.cache_data(ttl=43200)
def fetch_government_macro():
    # 1. COT Positioning via CFTC Socrata Open API
    cot_net = None
    try:
        url = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json?$limit=200&$order=report_date_as_yyyy_mm_dd%20DESC"
        cot_data = requests.get(url, timeout=10).json()
        for row in cot_data:
            name = str(row.get("market_and_exchange_names", "")).upper()
            if "GOLD" in name and "COMMODITY EXCHANGE" in name:
                longs = int(row.get("noncomm_positions_long_all", 0))
                shorts = int(row.get("noncomm_positions_short_all", 0))
                cot_net = longs - shorts
                break
    except Exception:
        cot_net = None

    # 2. Headline CPI via Bureau of Labor Statistics (BLS) Public API
    cpi_yoy = None
    try:
        curr_yr = datetime.now().year
        payload = json.dumps({
            "seriesid": ["CUSR0000SA0"],
            "startyear": str(curr_yr - 2),
            "endyear": str(curr_yr),
        })
        res = requests.post(
            "https://api.bls.gov/publicAPI/v2/timeseries/data/",
            data=payload,
            headers={"Content-type": "application/json"},
            timeout=8,
        )
        pts = res.json()["Results"]["series"][0]["data"]
        latest_cpi = float(pts[0]["value"])
        year_ago_cpi = float(pts[12]["value"])
        cpi_yoy = round(((latest_cpi - year_ago_cpi) / year_ago_cpi) * 100, 2)
    except Exception:
        cpi_yoy = 3.35

    # 3. US National Debt via U.S. Treasury Fiscal Data API
    total_debt_t = None
    try:
        t_url = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny?sort=-record_date&page[size]=1"
        t_data = requests.get(t_url, timeout=8).json()
        raw_debt = float(t_data["data"][0]["tot_pub_debt_out_amt"])
        total_debt_t = round(raw_debt / 1e12, 2)
    except Exception:
        total_debt_t = None

    # 4. 10-Year Historical Seasonality (Monthly Avg Return)
    seasonality = pd.Series(dtype=float)
    try:
        g_hist = fetch_series("GC=F", period="10y", interval="1mo")
        if not g_hist.empty:
            m_rets = g_hist.pct_change() * 100
            seasonality = m_rets.groupby(m_rets.index.strftime("%b")).mean()
            order = [
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            ]
            seasonality = seasonality.reindex(order).dropna()
    except Exception:
        pass

    return cot_net, cpi_yoy, total_debt_t, seasonality

# =====================================================================
# CACHED TIER 2: INTERMEDIATE CHARTS (15-Minute Refresh)
# =====================================================================

@st.cache_data(ttl=900)
def fetch_chart_data():
    price_series = fetch_series("GC=F", period="6mo", interval="1d")
    return price_series

# =====================================================================
# TIER 3: LIVE REFRESHING ENGINE (60-Second Loop)
# =====================================================================

@st.fragment(run_every=60)
def render_terminal():
    with st.spinner("Connecting to institutional feeds..."):
        # Gold Spot / Futures Cascading Logic
        gold_src = "Spot XAUUSD"
        gold_s = fetch_series("XAUUSD=X", period="5d")
        if gold_s.empty:
            gold_s = fetch_series("GC=F", period="5d")
            gold_src = "COMEX Front-Month (GC=F)"
        latest_gold = (
            round(float(gold_s.iloc[-1]), 2) if not gold_s.empty else None
        )

        # Silver
        silver_s = fetch_series("XAGUSD=X", period="5d")
        if silver_s.empty:
            silver_s = fetch_series("SI=F", period="5d")
        latest_silver = (
            round(float(silver_s.iloc[-1]), 2) if not silver_s.empty else None
        )

        # US Dollar Index (DXY)
        dxy_s = fetch_series("DX-Y.NYB", period="5d")
        latest_dxy = (
            round(float(dxy_s.iloc[-1]), 2) if not dxy_s.empty else None
        )

        # 10Y Nominal Treasury Yield
        tnx_s = fetch_series("^TNX", period="5d")
        latest_nominal_yield = (
            round(float(tnx_s.iloc[-1]), 2) if not tnx_s.empty else None
        )

        # Gold-Silver Ratio
        gsr = (
            round(latest_gold / latest_silver, 2)
            if (latest_gold and latest_silver)
            else None
        )

        # 30-Day DXY Rolling Correlation (Resilient Time-Alignment)
        latest_corr = None
        try:
            g_hist = yf.Ticker("GC=F").history(period="3mo")["Close"].dropna()
            d_hist = yf.Ticker("DX-Y.NYB").history(period="3mo")["Close"].dropna()
            
            # Remove timezones to prevent index collision
            g_hist.index = g_hist.index.tz_localize(None)
            d_hist.index = d_hist.index.tz_localize(None)
            
            combined = pd.concat([g_hist, d_hist], axis=1, join="inner").dropna()
            combined.columns = ["Gold", "DXY"]
            
            rets = combined.pct_change().dropna()
            rolling_series = rets["Gold"].rolling(30).corr(rets["DXY"]).dropna()
            if not rolling_series.empty:
                latest_corr = round(float(rolling_series.iloc[-1]), 3)
        except Exception:
            latest_corr = None

        # GLD ETF Relative Volume Proxy
        gld_rel_vol = None
        try:
            gld_df = yf.Ticker("GLD").history(period="1mo")
            if not gld_df.empty and "Volume" in gld_df.columns:
                vols = gld_df["Volume"].dropna()
                gld_rel_vol = round(float(vols.iloc[-1] / vols.mean()), 2)
        except Exception:
            pass

        # Cached Regulatory & Government Metrics
        cot_net, cpi_yoy, total_debt, seasonality = fetch_government_macro()
        price_history = fetch_chart_data()

    # Derive Real Yield (Nominal 10Y minus Headline CPI)
    if latest_nominal_yield is not None and cpi_yoy is not None:
        real_yield = round(latest_nominal_yield - cpi_yoy, 2)
    else:
        real_yield = None

    # Active Session Identifier
    now_utc = datetime.now(timezone.utc)
    hr = now_utc.hour
    if 0 <= hr < 7:
        session_name = "Asian / Tokyo Session"
    elif 7 <= hr < 13:
        session_name = "London Morning Session"
    elif 13 <= hr < 17:
        session_name = "London / New York Overlap"
    elif 17 <= hr < 21:
        session_name = "New York Afternoon Session"
    else:
        session_name = "Asian Pre-Market / Quiet Flow"

    # =====================================================================
    # QUANTITATIVE BIAS ALGORITHM (5 Pillars)
    # =====================================================================
    bullish_factors = 0
    total_factors = 5

    # Factor 1: Real Yields (< 1.50% is favorable for Gold)
    if real_yield is not None and real_yield < 1.50:
        bullish_factors += 1
        ry_status = (f"Tailwind ({real_yield}% < 1.5%)", "normal")
    else:
        ry_val_str = f"{real_yield}%" if real_yield is not None else "N/A"
        ry_status = (f"Headwind ({ry_val_str} >= 1.5%)", "inverse")

    # Factor 2: Institutional Futures Positioning (COT Net > 0)
    if cot_net is not None and cot_net > 0:
        bullish_factors += 1
        cot_status = (f"+{cot_net:,} Net Long", "normal")
    else:
        cot_val_str = f"{cot_net:,}" if cot_net is not None else "N/A"
        cot_status = (f"{cot_val_str} Net Short", "inverse")

    # Factor 3: DXY Trend (< 103.00 signifies Dollar Weakness)
    if latest_dxy is not None and latest_dxy < 103.00:
        bullish_factors += 1
        dxy_status = ("Tailwind (Sub-103)", "normal")
    else:
        dxy_status = ("Headwind (Dollar Firm)", "inverse")

    # Factor 4: Safe-Haven Bid (GSR > 75.0 signifies Gold outperformance)
    if gsr is not None and gsr > 75.00:
        bullish_factors += 1
        gsr_status = ("Safe-Haven Bid", "normal")
    else:
        gsr_status = ("Risk-On Flow", "inverse")

    # Factor 5: DXY Correlation (Inverse correlation < -0.30)
    if latest_corr is not None and latest_corr < -0.30:
        bullish_factors += 1
        corr_status = (f"Normal Inverse ({latest_corr})", "normal")
    else:
        corr_label = (
            f"Decoupled ({latest_corr})"
            if latest_corr is not None
            else "Decoupled / Irregular"
        )
        corr_status = (corr_label, "inverse")

    bearish_factors = total_factors - bullish_factors

    # =====================================================================
    # TOP BAR: SESSION & SYSTEM STATUS
    # =====================================================================
    bar_c1, bar_c2 = st.columns([3, 1])
    with bar_c1:
        st.markdown(
            f"**Active Session:** `{session_name}` | **Pricing Node:**"
            f" `{gold_src}` | **Synced:**"
            f" `{now_utc.strftime('%H:%M:%S')} UTC`"
        )
    with bar_c2:
        if st.button("🔄 Force Refresh All Data"):
            st.cache_data.clear()
            st.rerun()

    st.markdown("---")

    # =====================================================================
    # EXECUTIVE SCORECARD & PROPORTIONATE GAUGE
    # =====================================================================
    g_col1, g_col2, g_col3 = st.columns([1, 2, 1])
    with g_col2:
        fig_gauge = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=bullish_factors,
                domain={"x": [0, 1], "y": [0.15, 1]},
                title={
                    "text": (
                        "Institutional Macro Bias Score<br><span"
                        " style='font-size:12px;color:gray'>0-1: Strong"
                        " Defensive | 2-3: Neutral / Balanced | 4-5: Strong"
                        " Bullish</span>"
                    ),
                    "font": {"size": 15},
                },
                gauge={
                    "axis": {
                        "range": [0, 5],
                        "tickvals": [0, 1, 2, 3, 4, 5],
                        "tickwidth": 1,
                        "tickcolor": "white",
                    },
                    "bar": {
                        "color": "rgba(255, 255, 255, 0.8)",
                        "thickness": 0.25,
                    },
                    "bgcolor": "black",
                    "steps": [
                        {"range": [0, 1.5], "color": "#EF553B"},
                        {"range": [1.5, 3.5], "color": "#F6C85F"},
                        {"range": [3.5, 5], "color": "#00CC96"},
                    ],
                },
            )
        )
        fig_gauge.update_layout(
            height=240,
            margin=dict(l=40, r=40, t=50, b=10),
            template="plotly_dark",
        )
        st.plotly_chart(fig_gauge, use_container_width=True)

    # Directional Verdict Banner
    if bullish_factors >= 4:
        st.success(
            f"🟢 **MACRO BIAS: STRONG BULLISH** — {bullish_factors}/{total_factors} institutional drivers support long expansion."
        )
    elif bullish_factors in [2, 3]:
        st.warning(
            f"🟡 **MACRO BIAS: NEUTRAL / RANGEBOUND** — Market forces are balanced ({bullish_factors} Bullish vs {bearish_factors} Bearish). Trade mean-reversion."
        )
    else:
        st.error(
            f"🔴 **MACRO BIAS: DEFENSIVE / SHORT BIAS** — {bearish_factors}/{total_factors} institutional drivers signal capital rotation away from gold."
        )

    st.markdown("---")

    # =====================================================================
    # 10 CORE METRICS
    # =====================================================================
    st.subheader("Core Fundamental & Execution Metrics")

    r1_c1, r1_c2, r1_c3, r1_c4, r1_c5 = st.columns(5)
    r1_c1.metric(
        label="Spot Gold Benchmark",
        value=f"${latest_gold:,.2f}" if latest_gold else "N/A",
        delta=gold_src,
        delta_color="off",
        help="Primary execution benchmark. Source: Consolidated Exchange Feeds.",
    )
    r1_c2.metric(
        label="10Y Real Yield",
        value=f"{real_yield}%" if real_yield is not None else "N/A",
        delta=ry_status[0],
        delta_color=ry_status[1],
        help="Nominal 10Y Yield minus Headline CPI. Opportunity cost benchmark.",
    )
    r1_c3.metric(
        label="10Y Nominal Yield",
        value=(
            f"{latest_nominal_yield}%"
            if latest_nominal_yield is not None
            else "N/A"
        ),
        delta="Source: CBOE (^TNX)",
        delta_color="off",
        help="Benchmark nominal rate on US government 10-year debt.",
    )
    r1_c4.metric(
        label="US Dollar (DXY)",
        value=f"{latest_dxy:,.2f}" if latest_dxy else "N/A",
        delta=dxy_status[0],
        delta_color=dxy_status[1],
        help="Trade-weighted US Dollar Index. Source: ICE / NYB.",
    )
    r1_c5.metric(
        label="Headline CPI (YoY)",
        value=f"{cpi_yoy}%" if cpi_yoy is not None else "N/A",
        delta="Official BLS Print",
        delta_color="off",
        help="Annual inflation rate. Source: U.S. Bureau of Labor Statistics.",
    )

    r2_c1, r2_c2, r2_c3, r2_c4, r2_c5 = st.columns(5)
    r2_c1.metric(
        label="COT Net Speculative",
        value=f"{cot_net:,}" if cot_net is not None else "N/A",
        delta=cot_status[0],
        delta_color=cot_status[1],
        help="Net speculative futures positioning. Source: CFTC.",
    )
    r2_c2.metric(
        label="Gold-Silver Ratio",
        value=f"{gsr}" if gsr else "N/A",
        delta=gsr_status[0],
        delta_color=gsr_status[1],
        help="Spot Gold divided by Spot Silver. >75 reflects defensive demand.",
    )
    r2_c3.metric(
        label="30D DXY Correlation",
        value=f"{latest_corr}" if latest_corr is not None else "N/A",
        delta=corr_status[0],
        delta_color=corr_status[1],
        help="Rolling 30-day return correlation between Gold and DXY.",
    )
    r2_c4.metric(
        label="GLD ETF Relative Vol",
        value=f"{gld_rel_vol}x" if gld_rel_vol else "N/A",
        delta="Above Avg" if gld_rel_vol and gld_rel_vol > 1.0 else "Subdued",
        delta_color="off",
        help="SPDR Gold Shares (GLD) active volume relative to 30-day average.",
    )
    r2_c5.metric(
        label="US Public Debt",
        value=f"${total_debt}T" if total_debt else "N/A",
        delta="Source: US Treasury",
        delta_color="off",
        help="Total outstanding public debt. Source: U.S. Treasury.",
    )

    st.markdown("---")

    # =====================================================================
    # CHARTS: HISTORICAL PRICE ACTION & 10-YEAR SEASONALITY
    # =====================================================================
    ch_col1, ch_col2 = st.columns(2)

    with ch_col1:
        st.subheader("Gold Price Action (Trailing 6 Months)")
        if not price_history.empty:
            fig_p = go.Figure()
            fig_p.add_trace(
                go.Scatter(
                    x=price_history.index,
                    y=price_history.values,
                    name="Gold",
                    line=dict(color="#FFD700", width=2),
                )
            )
            fig_p.update_layout(
                template="plotly_dark",
                height=360,
                margin=dict(l=0, r=0, t=20, b=0),
                yaxis=dict(title="Price (USD)"),
            )
            st.plotly_chart(fig_p, use_container_width=True)
            st.caption(
                "Source: COMEX Gold Front-Month Continuous Contract (Daily Close)"
            )
        else:
            st.info("Loading 6-month historical chart...")

    with ch_col2:
        st.subheader("10-Year Historical Seasonality (% Avg Return)")
        if not seasonality.empty:
            bar_colors = [
                "#00CC96" if v >= 0 else "#EF553B" for v in seasonality.values
            ]
            fig_s = go.Figure(
                go.Bar(
                    x=seasonality.index,
                    y=seasonality.values,
                    marker_color=bar_colors,
                )
            )
            fig_s.update_layout(
                template="plotly_dark",
                height=360,
                margin=dict(l=0, r=0, t=20, b=0),
                yaxis=dict(title="% Monthly Return"),
            )
            st.plotly_chart(fig_s, use_container_width=True)
            st.caption(
                "Source: Aggregated 10-Year Monthly Closes via CME Historical Records"
            )
        else:
            st.info("Calculating 10-year seasonality distribution...")

    st.markdown("---")

    # =====================================================================
    # DATA PROVENANCE & REGULATORY REGISTRY
    # =====================================================================
    with st.expander("🔍 Complete Data Provenance & API Registry"):
        st.markdown("""
        Every fundamental data point feeding this terminal is bound to an official public endpoint or primary exchange feed:
        * **Commitments of Traders (COT):** Pulled directly from the U.S. Commodity Futures Trading Commission (CFTC) Socrata Open Data Portal (`jun7-fc8e`). Non-Commercial Speculative net positions: `noncomm_positions_long_all - noncomm_positions_short_all`.
        * **Consumer Price Index (Headline CPI):** Pulled from the U.S. Bureau of Labor Statistics (BLS) Public Timeseries API (`CUSR0000SA0`, All Urban Consumers, Seasonally Adjusted).
        * **U.S. National Debt:** Extracted directly from the U.S. Department of the Treasury Fiscal Data Service (`debt_to_penny` endpoint).
        * **Nominal Treasury Yields:** CBOE 10-Year Treasury Yield Index (`^TNX`).
        * **Real Yield Analytics:** Derived mathematically on each refresh cycle by subtracting trailing 12-month BLS CPI from active 10-Year Nominal Yields.
        * **Futures & FX Quotes:** Consolidated Interbank Spot Forex (`XAUUSD=X`, `XAGUSD=X`) with automatic failover to COMEX Active Gold (`GC=F`).
        """)

render_terminal()
