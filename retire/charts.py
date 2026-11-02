"""Plotly figures for results. All money is shown in thousands of dollars."""
import plotly.graph_objects as go

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT2 = "#52514e"
GRID = "#e6e5e0"
CRITICAL = "#e34948"
GOOD = "#1baf7a"
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def _layout(fig, title, ytitle="$ (thousands)", **kw):
    fig.update_layout(
        title=dict(text=title, x=0.01, font=dict(size=16, color=TEXT)),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family="Inter, Segoe UI, Helvetica, Arial, sans-serif", size=12, color=TEXT2),
        margin=dict(l=60, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, traceorder="normal"),
        **{"hovermode": "x unified", **kw},
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, tickcolor=GRID)
    fig.update_yaxes(title_text=ytitle, gridcolor=GRID, zerolinecolor=GRID, tickformat=",.0f")
    return fig


def _k(values, deflators=None):
    if deflators:
        return [v / d / 1000 for v, d in zip(values, deflators)]
    return [v / 1000 for v in values]


def balances(result, real=True):
    rows = result.rows
    years = [r["year"] for r in rows]
    defl = [r["end_cpi"] for r in rows] if real else None
    fig = go.Figure()
    parts = [("Taxable", "bal_taxable"), ("Traditional", "bal_trad"), ("Roth", "bal_roth"), ("Ladder", "bal_ladder")]
    for i, (label, key) in enumerate(parts):
        vals = _k([r[key] for r in rows], defl)
        if key == "bal_ladder" and not any(vals):
            continue
        fig.add_trace(go.Scatter(x=years, y=vals, name=label, stackgroup="one", mode="lines",
                                 line=dict(width=2, color=SERIES[i]),
                                 hovertemplate="%{y:,.0f}k"))
    _shortfall_marks(fig, rows)
    unit = "today's $" if real else "nominal $"
    return _layout(fig, f"{result.plan_name}: account balances ({unit})")


def _shortfall_marks(fig, rows):
    years = [r["year"] for r in rows if r["shortfall"] > 1]
    if years:
        fig.add_trace(go.Scatter(x=years, y=[0] * len(years), mode="markers", name="Shortfall",
                                 marker=dict(symbol="x", size=10, color=CRITICAL),
                                 hovertemplate="shortfall"))


def cashflow(result, real=True):
    rows = result.rows
    years = [r["year"] for r in rows]
    defl = [r["cpi"] for r in rows] if real else None
    fig = go.Figure()
    sources = [("Wages", "wages"), ("Social Security", "ss_total"), ("Other income", "other_income"),
               ("RMDs", "rmd"), ("Other withdrawals", None)]
    for i, (label, key) in enumerate(sources):
        vals = [r["withdrawals"] - r["rmd"] for r in rows] if key is None else [r[key] for r in rows]
        fig.add_trace(go.Bar(x=years, y=_k(vals, defl), name=label,
                             marker=dict(color=SERIES[i], line=dict(width=1, color=SURFACE)),
                             hovertemplate="%{y:,.0f}k"))
    required = [r["expenses"] + r["healthcare_auto"] + r["total_tax"] for r in rows]
    fig.add_trace(go.Scatter(x=years, y=_k(required, defl), name="Required: expenses + health + taxes",
                             mode="lines", line=dict(width=2, color=TEXT), hovertemplate="%{y:,.0f}k"))
    if any(r["discretionary"] for r in rows):
        total = [q + r["discretionary"] for q, r in zip(required, rows)]
        fig.add_trace(go.Scatter(x=years, y=_k(total, defl), name="+ Autopilot discretionary",
                                 mode="lines", line=dict(width=2, color=SERIES[6], dash="dash"),
                                 hovertemplate="%{y:,.0f}k"))
    fig.update_layout(barmode="stack", bargap=0.15)
    return _layout(fig, f"{result.plan_name}: cash flow ({'today' if real else 'nominal'} $)")


def taxes(result, real=True):
    rows = result.rows
    years = [r["year"] for r in rows]
    defl = [r["cpi"] for r in rows] if real else None
    fig = go.Figure()
    parts = [("Federal", "fed_tax"), ("State", "state_tax"), ("FICA", "fica"), ("NIIT", "niit"),
             ("10% penalty", "penalty"), ("IRMAA surcharge", "irmaa")]
    for i, (label, key) in enumerate(parts):
        vals = _k([r[key] for r in rows], defl)
        if not any(vals):
            continue
        fig.add_trace(go.Bar(x=years, y=vals, name=label, marker=dict(color=SERIES[i], line=dict(width=1, color=SURFACE)),
                             hovertemplate="%{y:,.1f}k"))
    conv = _k([r["conversion"] for r in rows], defl)
    if any(conv):
        fig.add_trace(go.Scatter(x=years, y=conv, name="Roth conversion", mode="lines+markers",
                                 line=dict(width=2, color=SERIES[6]), marker=dict(size=8),
                                 hovertemplate="%{y:,.0f}k"))
    fig.update_layout(barmode="stack", bargap=0.15)
    return _layout(fig, f"{result.plan_name}: taxes, penalties & conversions")


def healthcare(result, real=True):
    rows = result.rows
    years = [r["year"] for r in rows]
    defl = [r["cpi"] for r in rows] if real else None
    fig = go.Figure()
    parts = [("ACA net premium", "aca_premium"), ("ACA subsidy received", "aca_subsidy"),
             ("Medicare premiums", "medicare"), ("IRMAA surcharge", "irmaa")]
    for i, (label, key) in enumerate(parts):
        fig.add_trace(go.Bar(x=years, y=_k([r[key] for r in rows], defl), name=label,
                             marker=dict(color=SERIES[i], line=dict(width=1, color=SURFACE)),
                             hovertemplate="%{y:,.1f}k"))
    fig.update_layout(barmode="group", bargap=0.15)
    return _layout(fig, f"{result.plan_name}: healthcare premiums")


def fan(mc, title):
    years = mc["years"][: len(mc["bands"][50])]
    b = {p: [v / 1000 for v in vals] for p, vals in mc["bands"].items()}
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=years, y=b[90], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=years, y=b[10], fill="tonexty", fillcolor="rgba(42,120,214,0.15)",
                             line=dict(width=0), name="10th–90th pct", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=years, y=b[75], line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=years, y=b[25], fill="tonexty", fillcolor="rgba(42,120,214,0.30)",
                             line=dict(width=0), name="25th–75th pct", hoverinfo="skip"))
    for p, dash in ((90, "dot"), (75, "dash"), (50, "solid"), (25, "dash"), (10, "dot")):
        fig.add_trace(go.Scatter(x=years, y=b[p], name=f"{p}th", mode="lines", showlegend=p == 50,
                                 line=dict(width=2 if p == 50 else 1, color=SERIES[0], dash=dash),
                                 hovertemplate=f"{p}th: %{{y:,.0f}}k<extra></extra>"))
    sr = mc["success_rate"] * 100
    return _layout(fig, f"{title} — success rate {sr:.1f}% (net worth, today's $)")


def historical_bars(hist, title):
    runs = hist["runs"]
    x = [r["start_year"] for r in runs]
    y = [r["end_real"] / 1000 for r in runs]
    colors = [SERIES[0] if r["success"] else CRITICAL for r in runs]
    text = ["" if r["success"] else f"fails {r['first_shortfall']}" for r in runs]
    fig = go.Figure(go.Bar(x=x, y=y, marker=dict(color=colors, line=dict(width=1, color=SURFACE)),
                           customdata=text, name="Ending net worth",
                           hovertemplate="start %{x}: %{y:,.0f}k %{customdata}<extra></extra>"))
    sr = hist["success_rate"] * 100
    fig = _layout(fig, f"{title} — {sr:.1f}% of {len(runs)} historical start years succeed "
                       f"(red = ran out of money)", hovermode="closest")
    fig.update_xaxes(title_text="First year of historical sequence")
    return fig


def spaghetti(hist, title, highlight=None):
    fig = go.Figure()
    years = hist["years"]
    for r in hist["runs"]:
        hl = highlight and r["start_year"] in highlight
        color = CRITICAL if not r["success"] else ("#9ec5f4" if not hl else SERIES[0])
        fig.add_trace(go.Scatter(x=years[: len(r["nw_real"])], y=[v / 1000 for v in r["nw_real"]],
                                 mode="lines", line=dict(width=2 if hl else 1, color=color),
                                 name=str(r["start_year"]), showlegend=bool(hl),
                                 hovertemplate=f"start {r['start_year']}: %{{y:,.0f}}k<extra></extra>"))
    return _layout(fig, f"{title} — every historical path (today's $)", hovermode="closest")


def stress_lines(results, title):
    fig = go.Figure()
    for i, (label, r) in enumerate(results.items()):
        fig.add_trace(go.Scatter(x=r.series("year"), y=[v / 1000 for v in r.series("net_worth_real")],
                                 mode="lines", name=label + ("" if r.success else " ✗"),
                                 line=dict(width=2, color=SERIES[i % len(SERIES)]),
                                 hovertemplate="%{y:,.0f}k"))
    return _layout(fig, f"{title} — stress periods (net worth, today's $; ✗ = ran out)")


def roth_bars(rx):
    v = rx["variants"]
    labels = [x["label"] for x in v]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=labels, x=[x["summary"]["lifetime_value_real"] / 1000 for x in v],
                         orientation="h", name="Spending + ending after-tax wealth",
                         marker=dict(color=[SERIES[1] if x["label"] == rx["best"] else SERIES[0] for x in v]),
                         hovertemplate="%{x:,.0f}k<extra></extra>"))
    fig = _layout(fig, f"Roth conversion explorer — best: {rx['best']} (orange)", hovermode="closest")
    fig.update_yaxes(title_text="", autorange="reversed")
    fig.update_xaxes(title_text="Discretionary spending + ending after-tax wealth, today's $ (k)", showgrid=True, gridcolor=GRID)
    fig.update_layout(margin=dict(l=230))
    return fig


def roth_schedule(rx, labels=None):
    fig = go.Figure()
    years = rx["years"]
    chosen = [x for x in rx["variants"] if (labels is None and x["label"] in (rx["best"], "Current plan setting"))
              or (labels and x["label"] in labels)]
    for i, x in enumerate(chosen):
        rows = x["result"].rows
        fig.add_trace(go.Bar(x=years, y=[r["conversion"] / r["cpi"] / 1000 for r in rows], name=x["label"],
                             marker=dict(color=SERIES[i % len(SERIES)]), hovertemplate="%{y:,.0f}k"))
    fig.update_layout(barmode="group")
    return _layout(fig, "Annual Roth conversions (today's $)")


def ss_heatmap(opt):
    ages, people = opt["ages"], opt["people"]
    if len(people) == 1:
        fig = go.Figure(go.Bar(x=ages, y=[g["value"] / 1000 for g in opt["grid"]],
                               marker=dict(color=[SERIES[1] if g is opt["best"] else SERIES[0] for g in opt["grid"]]),
                               hovertemplate="claim %{x}: %{y:,.0f}k<extra></extra>"))
        fig = _layout(fig, f"Social Security claiming age — best {opt['best']['ages'][0]}", hovermode="closest")
        fig.update_xaxes(title_text=f"{people[0]} claiming age")
        return fig
    z = [[None] * len(ages) for _ in ages]
    for g in opt["grid"]:
        a, b = g["ages"]
        z[ages.index(b)][ages.index(a)] = g["value"] / 1000
    fig = go.Figure(go.Heatmap(x=ages, y=ages, z=z, colorscale=[[i / 6, c] for i, c in enumerate(SEQ_BLUE)],
                               colorbar=dict(title="$k"), xgap=2, ygap=2,
                               hovertemplate=f"{people[0]} %{{x}}, {people[1]} %{{y}}: %{{z:,.0f}}k<extra></extra>"))
    ba, bb = opt["best"]["ages"]
    fig.add_trace(go.Scatter(x=[ba], y=[bb], mode="markers+text", text=["best"], textposition="top center",
                             marker=dict(size=14, color=SERIES[1], line=dict(width=2, color=SURFACE)),
                             showlegend=False, hoverinfo="skip"))
    fig = _layout(fig, f"Spending + ending after-tax wealth (today's $) — best: "
                       f"{people[0]} {ba}, {people[1]} {bb}", ytitle=f"{people[1]} claiming age",
                  hovermode="closest")
    fig.update_xaxes(title_text=f"{people[0]} claiming age", dtick=1)
    fig.update_yaxes(dtick=1, tickformat="d", gridcolor=SURFACE)
    return fig


METRICS = {
    "Net worth (today's $)": "net_worth_real",
    "After-tax net worth (today's $)": "after_tax_worth_real",
    "Net worth (nominal)": "net_worth",
    "Total taxes": "total_tax",
    "Spending withdrawals": "withdrawals",
    "Roth conversions": "conversion",
    "MAGI": "magi",
}


def overlay(results, metric_label):
    key = METRICS[metric_label]
    fig = go.Figure()
    for i, r in enumerate(results):
        fig.add_trace(go.Scatter(x=r.series("year"), y=[v / 1000 for v in r.series(key)], mode="lines",
                                 name=r.plan_name + ("" if r.success else " ✗"),
                                 line=dict(width=2, color=SERIES[i % len(SERIES)]),
                                 hovertemplate="%{y:,.0f}k"))
    return _layout(fig, f"Plan comparison — {metric_label}")


def to_html(fig, inline_js=True):
    return fig.to_html(include_plotlyjs=True if inline_js else "cdn", full_html=True,
                       config={"displaylogo": False, "responsive": True})
