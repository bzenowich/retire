"""Batch analyses: historical backtests, Monte Carlo, Roth explorer, SS optimizer."""
import copy
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

from . import markets
from .engine import plan_years, simulate

PERCENTILES = (10, 25, 50, 75, 90)


def _workers():
    return max(1, (os.cpu_count() or 2) - 1)


def _run_one(args):
    plan, seq = args
    r = simulate(plan, seq)
    return {
        "success": r.success,
        "first_shortfall": r.first_shortfall,
        "nw_real": r.series("net_worth_real"),
        "end_real": r.summary["end_net_worth_real"],
        "summary": r.summary,
    }


def _batch(plan, seqs, progress=None):
    jobs = [(plan, s) for s in seqs]
    if len(jobs) < 16:
        out = []
        for k, j in enumerate(jobs):
            out.append(_run_one(j))
            if progress:
                progress(k + 1, len(jobs))
        return out
    out = []
    # spawn: safe to start from a GUI process with live threads
    with ProcessPoolExecutor(max_workers=_workers(), mp_context=multiprocessing.get_context("spawn")) as ex:
        for k, res in enumerate(ex.map(_run_one, jobs, chunksize=8)):
            out.append(res)
            if progress:
                progress(k + 1, len(jobs))
    return out


def _percentile(values, p):
    v = sorted(values)
    if not v:
        return 0.0
    k = (len(v) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def _bands(paths):
    n = min(len(p) for p in paths)
    return {p: [_percentile([path[t] for path in paths], p) for t in range(n)] for p in PERCENTILES}


def historical(plan, wrap=None, progress=None):
    """Run the plan starting in every historical year (ficalc-style)."""
    years = plan_years(plan)
    wrap = plan.settings.historical_wrap if wrap is None else wrap
    starts = markets.historical_start_years(years, wrap)
    seqs = [markets.historical_sequence(y, years, wrap) for y in starts]
    runs = _batch(plan, seqs, progress)
    for y, r in zip(starts, runs):
        r["start_year"] = y
    return {
        "runs": runs,
        "success_rate": sum(r["success"] for r in runs) / len(runs) if runs else 0.0,
        "bands": _bands([r["nw_real"] for r in runs]) if runs else {},
        "years": [plan.settings.start_year + t for t in range(years)],
        "wrap": wrap,
    }


def monte_carlo(plan, n=500, block=5, seed=None, progress=None):
    years = plan_years(plan)
    seqs = markets.bootstrap_sequences(years, n, block, seed)
    runs = _batch(plan, seqs, progress)
    return {
        "runs": runs,
        "success_rate": sum(r["success"] for r in runs) / len(runs),
        "bands": _bands([r["nw_real"] for r in runs]),
        "years": [plan.settings.start_year + t for t in range(years)],
        "end_percentiles": {p: _percentile([r["end_real"] for r in runs], p) for p in PERCENTILES},
    }


def stress(plan, start_years):
    """Deterministic plan replayed through named historical periods."""
    years = plan_years(plan)
    out = {}
    for label, y in start_years.items():
        seq = markets.historical_sequence(y, years, wrap=True)
        out[label] = simulate(plan, seq)
    return out


# ------------------------------------------------------------ Roth explorer
def roth_variants(plan):
    base = plan.roth
    v = [("No conversions", dict(mode="none"))]
    for b in (12, 22, 24, 32):
        v.append((f"Fill {b}% bracket", dict(mode="optimize", max_bracket=b, irmaa_max_tier=-1,
                                             ltcg_zero=False, aca_max_fpl=0)))
    for tier in (0, 1, 2):
        v.append((f"24% bracket, IRMAA tier ≤{tier}", dict(mode="optimize", max_bracket=24,
                                                           irmaa_max_tier=tier, ltcg_zero=False,
                                                           aca_max_fpl=0)))
    v.append(("22% + IRMAA 0 + ACA 400% FPL", dict(mode="optimize", max_bracket=22, irmaa_max_tier=0,
                                                  ltcg_zero=False, aca_max_fpl=400)))
    v.append(("0% LTCG bracket", dict(mode="optimize", max_bracket=0, irmaa_max_tier=-1,
                                      ltcg_zero=True, aca_max_fpl=0)))
    v.append(("Current plan setting", {k: getattr(base, k) for k in vars(base)}))
    return v


def roth_explorer(plan, priority=None, progress=None):
    out = []
    variants = roth_variants(plan)
    for k, (label, cfg) in enumerate(variants):
        p = copy.deepcopy(plan)
        for key, val in cfg.items():
            setattr(p.roth, key, val)
        if priority:
            p.roth.priority = priority
        r = simulate(p)
        out.append({"label": label, "result": r, "summary": r.summary,
                    "conversions": r.series("conversion")})
        if progress:
            progress(k + 1, len(variants))
    best = max(out, key=lambda x: (x["summary"]["success"], x["summary"]["lifetime_value_real"]))
    return {"variants": out, "best": best["label"], "years": out[0]["result"].series("year")}


# ------------------------------------------------------------ SS optimizer
def ss_optimizer(plan, ages=range(62, 71), metric="lifetime_value_real", progress=None):
    """Grid search claiming ages for up to two people with the full plan."""
    people = plan.people[:2]
    grid = []
    ages = list(ages)
    combos = [(a,) for a in ages] if len(people) == 1 else [(a, b) for a in ages for b in ages]
    for k, combo in enumerate(combos):
        p = copy.deepcopy(plan)
        for person, age in zip(p.people, combo):
            person.ss_claim_age = float(age)
        r = simulate(p)
        grid.append({"ages": combo, "value": r.summary[metric], "summary": r.summary})
        if progress:
            progress(k + 1, len(combos))
    best = max(grid, key=lambda g: (g["summary"]["success"], g["value"]))
    return {"grid": grid, "ages": ages, "best": best, "people": [p.name for p in people], "metric": metric}
