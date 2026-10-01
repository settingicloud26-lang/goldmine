from datetime import datetime, timezone
import io
import json
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Gold Intelligence Dashboard", page_icon="🪙", layout="wide"
)

st.title("🪙 XAUUSD Institutional Terminal")
st.caption(
    "Automated fundamental tracking & directional bias engine for prop execution"
)

# =====================================================================
# TIER 1: SLOW / MACRO ANCHORS (Cached for 12 Hours to Prevent Rate Blocks)
# =====================================================================


@st.cache_data(ttl=43200)
def fetch_macro_anchors():
    """Fetches COT positioning, CPI, US Debt, and 10Y Seasonality."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 1. COT Positioning via CFTC Socrata API
    net_spec_val = None
    try:
        url = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json?$limit=200&$order=report_date_as_yyyy_mm_dd%20DESC"
        cot_data = requests.get(url, timeout=10).json()
        for row in cot_data:
            m_name = str(row.get("market_and_exchange_names", "")).upper()
            if "GOLD" in m_name and "COMMODITY EXCHANGE" in m_name:
                nc_long = int(row.get("noncomm_positions_long_all", 0))
                nc_short = int(row.get("noncomm_positions_short_all", 0))
                net_spec_val = nc_long - nc_short
                break
    except Exception:
        net_spec_val = None

    # 2. CPI Inflation via BLS API
    cpi_num = None
    try:
        curr_y = datetime.now().year
        payload = json.dumps({
            "seriesid": ["CUSR0000SA0"],
            "startyear": str(curr_y - 2),
            "endyear": str(curr_y),
        })
        p = requests.post(
            "https://api.bls.gov/publicAPI/v2/timeseries/data/",
            data=payload,
            headers={"Content-type": "application/json"},
            timeout=8,
        )
        cpi_pts = p.json()["Results"]["series"][0]["data"]
        cpi_num = round(
            ((float(cpi_pts[0]["value"]) - float(cpi_pts[12]["value"]))
            / float(cpi_pts[12]["value"]))
            * 100,
            1,
        )
    except Exception:
        cpi_num = None

    # 3. US National Debt via Treasury API
    debt_val = None
    try:
        d_url = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny?sort=-record_date&page[size]=1"
        d_res = requests.get(d_url, timeout=8).json()
        debt_val = round(
            float(d_res["data"][0]["tot_pub_debt_out_amt"]) / 1e12, 2
        )
    except Exception:
        debt_val = None

    # 4. 10-Year Monthly Seasonality
    seasonality = pd.Series(dtype=float)
    try:
        gold_10y = yf.Ticker("GC=F").history(period="10y", interval="1mo")[
            "Close"
        ].dropna()
        monthly_returns = gold_10y.pct_change() * 100
        seasonality = monthly_returns.groupby(
            monthly_returns.index.strftime("%b")
        ).mean()
        months_order = [
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
        seasonality = seasonality.reindex(months_order).dropna()
    except Exception:
        pass

    return net_spec_val, cpi_num, debt_val, seasonality


# =====================================================================
# TIER 2: MEDIUM DATA (Cached for 15 Minutes)
# =====================================================================


@st.cache_data(ttl=900)
def fetch_intermediate_data():
    """Fetches 6-month historical chart data and nearest OPEX strike."""
    try:
        history_df = yf.download(
            "GC=F", period="6mo", interval="1d", progress=False
        )["Close"].dropna()
    except Exception:
        history_df = pd.Series(dtype=float)

    max_call_strike = "N/A"
    try:
        gld_opts = yf.Ticker("GLD")
        nearest_exp = gld_opts.options[0]
        calls = gld_opts.option_chain(nearest_exp).calls
        max_call_strike = calls.loc[calls["openInterest"].idxmax()]["strike"]
    except Exception:
        pass

    return history_df, max_call_strike


# =====================================================================
# TIER 3: FAST DATA & EXECUTION (Refreshes Every 60s without Rate Blocks)
# =====================================================================


@st.fragment(run_every=60)
def render_live_dashboard():
    # 1. Fetch Fast Quotes (Tiny 5-day window for instant retrieval)
    with st.spinner("Fetching active session data..."):
        try:
            fast_data = yf.download(
                ["GC=F", "SI=F", "DX-Y.NYB", "^TNX", "GLD"],
                period="5d",
                interval="1d",
                progress=False,
            )
            closes = fast_data["Close"]

            latest_gold = round(float(closes["GC=F"].dropna().iloc[-1]), 2)
            latest_silver = round(float(closes["SI=F"].dropna().iloc[-1]), 2)
            latest_dxy = round(float(closes["DX-Y.NYB"].dropna().iloc[-1]), 2)
            latest_yield = round(float(closes["^TNX"].dropna().iloc[-1]), 2)

            # Gold-Silver Ratio
            gsr = round(latest_gold / latest_silver, 2)

            # GLD Relative Volume
            vol_df = fast_data["Volume"]["GLD"].dropna()
            gld_rel_vol = round(float(vol_df.iloc[-1] / vol_df.mean()), 2)
        except Exception:
            latest_gold, latest_silver, latest_dxy, latest_yield, gsr, (
                gld_rel_vol
            ) = (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

        # 30-Day DXY Correlation (Calculated using 2-month history)
        try:
            corr_df = yf.download(
                ["GC=F", "DX-Y.NYB"], period="2mo", interval="1d", progress=False
            )["Close"]
            rets = corr_df.pct_change().dropna()
            latest_corr = round(
                float(
                    rets["GC=F"]
                    .rolling(30)
                    .corr(rets["DX-Y.NYB"])
                    .dropna()
                    .iloc[-1]
                ),
                3,
            )
        except Exception:
            latest_corr = -0.50

        # Load Cached Tiers
        net_spec_val, cpi_num, debt_val, seasonality = fetch_macro_anchors()
        history_df, max_call_strike = fetch_intermediate_data()

    # Determine Active Trading Session (UTC)
    now_utc = datetime.now(timezone.utc)
    utc_hour = now_utc.hour
    if 0 <= utc_hour < 7:
        active_session = "Asian / Tokyo Session 🌏"
    elif 7 <= utc_hour < 13:
        active_session = "London Morning Session 🇬🇧"
    elif 13 <= utc_hour < 17:
        active_session = "London / New York Overlap 🔥"
    elif 17 <= utc_hour < 21:
        active_session = "New York Afternoon Session 🇺🇸"
    else:
        active_session = "Session Close / Quiet Flow 🌙"

    # --- MACRO BIAS ALGORITHM ---
    bullish_points = 0
    total_criteria = 5

    if latest_corr < -0.30:
        bullish_points += 1
        corr_label = "Bullish (Inverse)"
    else:
        corr_label = "- Bearish (Decoupled)"

    if gsr > 75:
        bullish_points += 1
        gsr_label = "Bullish (Risk-Off)"
    else:
        gsr_label = "- Bearish (Risk-On)"

    if latest_yield and latest_yield < 4.5:
        bullish_points += 1
        yield_label = "Bullish (Low Yields)"
    else:
        yield_label = "- Bearish (Yield Pressure)"

    if cpi_num and cpi_num > 2.5:
        bullish_points += 1
        cpi_label = "Bullish (Inflation Bid)"
    else:
        cpi_label = "- Bearish (Disinflation)"

    if net_spec_val and net_spec_val > 0:
        bullish_points += 1
        cot_label = "Bullish (Net Long)"
    else:
        cot_label = "- Bearish (Net Short)"

    bearish_points = total_criteria - bullish_points

    # --- TOP HEADER BAR ---
    top_col1, top_col2 = st.columns([3, 1])
    with top_col1:
        st.markdown(
            f"**Session:** `{active_session}` | **Updated:** `{now_utc.strftime('%H:%M:%S')} UTC`"
        )
    with top_col2:
        if st.button("🔄 Force Refresh All Data"):
            st.cache_data.clear()
            st.rerun()

    # --- UI LAYOUT ---
    tab_summary, tab_charts, tab_playbook = st.tabs([
        "📊 Executive Summary",
        "📈 Deep Dive Charts",
        "🛡️ Prop Firm Playbook",
    ])

    with tab_summary:
        # GAUGE SPEEDOMETER
        c_left, c_gauge, c_right = st.columns([1, 2, 1])
        with c_gauge:
            fig_gauge = go.Figure(
                go.Indicator(
                    mode="gauge+number",
                    value=bullish_points,
                    domain={"x": [0, 1], "y": [0, 1]},
                    title={
                        "text": (
                            "Macro Bias Score (0=Bearish, 5=Bullish)"
                        ),
                        "font": {"size": 18},
                    },
                    gauge={
                        "axis": {
                            "range": [0, 5],
                            "tickwidth": 1,
                            "tickcolor": "white",
                        },
                        "bar": {
                            "color": "rgba(255, 255, 255, 0.7)",
                            "thickness": 0.25,
                        },
                        "bgcolor": "black",
                        "steps": [
                            {"range": [0, 2], "color": "#EF553B"},
                            {"range": [2, 3], "color": "#F6C85F"},
                            {"range": [3, 5], "color": "#00CC96"},
                        ],
                    },
                )
            )
            fig_gauge.update_layout(
                height=220,
                margin=dict(l=10, r=10, t=30, b=10),
                template="plotly_dark",
            )
            st.plotly_chart(fig_gauge, use_container_width=True)

        if bullish_points >= 4:
            st.success(
                f"🟢 **INSTITUTIONAL BIAS: STRONG BULLISH** ({bullish_points}/{total_criteria} macro factors favor longs)"
            )
        elif bullish_points == 3:
            st.info(
                f"🟡 **INSTITUTIONAL BIAS: NEUTRAL / RANGEBOUND** ({bullish_points}/{total_criteria} macro factors balanced)"
            )
        else:
            st.error(
                f"🔴 **INSTITUTIONAL BIAS: BEARISH / DEFENSIVE** ({bearish_points}/{total_criteria} macro factors favor shorts/pullbacks)"
            )

        st.markdown("---")

        # METRIC SCORECARD
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric(
            "Spot Gold",
            f"${latest_gold:,.2f}",
            delta="Live GC=F",
            delta_color="off",
            help="Active spot futures benchmark.",
        )
        c2.metric(
            "US Dollar (DXY)",
            f"{latest_dxy:,.2f}",
            delta="Tailwind" if latest_dxy < 103 else "Headwind",
            delta_color="off",
            help="Dollar strength index.",
        )
        c3.metric(
            "Gold-Silver Ratio",
            f"{gsr}",
            delta=gsr_label,
            help=">75 Safe Haven demand; <75 Industrial/Risk-On.",
        )
        c4.metric(
            "30D DXY Correlation",
            f"{latest_corr}",
            delta=corr_label,
            help="Expected normal range: -0.30 to -0.80.",
        )
        c5.metric(
            "Headline CPI",
            f"{cpi_num}%" if cpi_num else "N/A",
            delta=cpi_label,
            help="BLS YoY CPI print.",
        )

        c6, c7, c8, c9, c10 = st.columns(5)
        c6.metric(
            "GLD Rel. Volume",
            f"{gld_rel_vol}x",
            delta="Volume Active" if gld_rel_vol > 1.0 else "Quiet",
            delta_color="off",
            help="GLD volume relative to trailing average.",
        )
        c7.metric(
            "10Y Treasury Yield",
            f"{latest_yield}%" if latest_yield else "N/A",
            delta=yield_label,
            help="Benchmark yield (^TNX). Higher yield increases opportunity cost.",
        )
        c8.metric(
            "Nearest OPEX Magnet",
            f"${max_call_strike}",
            delta="Open Interest Peak",
            delta_color="off",
            help="GLD option strike with largest open interest concentration.",
        )
        c9.metric(
            "COT Net Positioning",
            f"{net_spec_val:,}" if net_spec_val else "N/A",
            delta=cot_label,
            help="CFTC speculative net long/short contract balance.",
        )
        c10.metric(
            "US National Debt",
            f"${debt_val}T" if debt_val else "N/A",
            delta="Sovereign Expansion",
            delta_color="off",
            help="Total outstanding public debt from US Treasury.",
        )

    with tab_charts:
        chart_col, season_col = st.columns([1, 1])
        with chart_col:
            st.subheader("Gold Price Action (6 Months)")
            if not history_df.empty:
                fig_price = go.Figure()
                fig_price.add_trace(
                    go.Scatter(
                        x=history_df.index,
                        y=history_df.values,
                        name="Gold",
                        line=dict(color="#FFD700", width=2),
                    )
                )
                fig_price.update_layout(
                    template="plotly_dark",
                    height=380,
                    margin=dict(l=0, r=0, t=30, b=0),
                )
                st.plotly_chart(fig_price, use_container_width=True)
            else:
                st.info("Chart data updating...")

        with season_col:
            st.subheader("10-Year Historical Seasonality (% Avg Return)")
            if not seasonality.empty:
                colors = [
                    "#00CC96" if v >= 0 else "#EF553B"
                    for v in seasonality.values
                ]
                fig_season = go.Figure(
                    go.Bar(
                        x=seasonality.index,
                        y=seasonality.values,
                        marker_color=colors,
                    )
                )
                fig_season.update_layout(
                    template="plotly_dark",
                    height=380,
                    margin=dict(l=0, r=0, t=30, b=0),
                    yaxis_title="% Return",
                )
                st.plotly_chart(fig_season, use_container_width=True)
            else:
                st.info("Seasonality data loading...")

    with tab_playbook:
        st.subheader("📋 Prop Firm Execution Protocol (XAUUSD)")
        p_col1, p_col2 = st.columns(2)
        with p_col1:
            st.markdown("""
            **Directional Bias Guidance:**
            * When Macro Score is **$\le$ 2/5 (Defensive)**:
              * Restrict trade plans to short execution at premium price zones.
              * Invalidate bullish Fair Value Gaps (FVG) or support bounces against higher yields.
              * Target intraday liquidity pools below key swing lows.
            * When Macro Score is **$\ge$ 4/5 (Bullish)**:
              * Look for discount entries inside institutional demand zones.
              * Target previous day highs and buy-side stops.
            """)
        with p_col2:
            st.markdown("""
            **Risk Rules for Evaluation Accounts:**
            * **Daily Drawdown Protection:** Cap total risk at maximum 1.5% balance exposure per day.
            * **High-Impact News Filter:** No new market orders 15 minutes before and after US CPI, NFP, or FOMC statements.
            * **Session Timing:** Prioritize entries between 07:30–10:30 GMT (London) and 13:00–16:00 GMT (NY Overlap).
            """)


render_live_dashboard()
