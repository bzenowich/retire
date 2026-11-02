"""Search withdrawal and Roth conversion strategies for the best success rate.

Each candidate strategy is run through every historical start year (or a fixed
Monte Carlo sample). Candidates are ranked by success rate, then by median
"lifetime value" = discretionary (Autopilot) spending + ending after-tax wealth,
both in today's dollars. The search is coordinate descent over the strategy
dimensions, which needs far fewer runs than the full cross product.
"""
import copy
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

from . import markets
from .engine import plan_years, simulate

ORDERS = [
    ("tax_smart", 12.0), ("tax_smart", 22.0), ("taxable_trad_roth", None),
    ("trad_taxable_roth", None), ("taxable_roth_trad", None), ("proportional", None),
]
ORDER_LABELS = {
    "tax_smart": "Tax-smart (fill {b:.0f}% from Trad, Taxable, Roth last)",
    "taxable_trad_roth": "Taxable → Trad → Roth",
    "trad_taxable_roth": "Trad → Taxable → Roth",
    "taxable_roth_trad": "Taxable → Roth → Trad",
    "proportional": "Pro-rata",
}


def _roth(mode="optimize", bracket=0, irmaa=-1, ltcg=False, aca=0):
    return {"mode": mode, "max_bracket": float(bracket), "irmaa_max_tier": irmaa,
            "ltcg_zero": ltcg, "aca_max_fpl": float(aca)}


def roth_candidates(plan):
    c = [_roth("none")]
    for b in (12, 22, 24, 32):
        c.append(_roth(bracket=b))
    for b in (22, 24):
        for tier in (0, 1):
            c.append(_roth(bracket=b, irmaa=tier))
    c.append(_roth(ltcg=True))
    if _has_aca_years(plan):
        for b in (12, 22, 24):
            c.append(_roth(bracket=b, irmaa=0, aca=400))
        c.append(_roth(ltcg=True, aca=400))
    return c


def _has_aca_years(plan):
    if not plan.settings.aca_enabled:
        return False
    return any(p.retire.year < p.birth.year + 65 for p in plan.people)


def roth_label(cfg):
    if cfg["mode"] == "none":
        return "No conversions"
    parts = []
    if cfg["max_bracket"]:
        parts.append(f"fill {cfg['max_bracket']:.0f}% bracket")
    if cfg["ltcg_zero"]:
        parts.append("stay in 0% LTCG")
    if cfg["irmaa_max_tier"] is not None and cfg["irmaa_max_tier"] >= 0:
        parts.append(f"IRMAA tier ≤{cfg['irmaa_max_tier']}")
    if cfg["aca_max_fpl"]:
        parts.append(f"≤{cfg['aca_max_fpl']:.0f}% FPL on ACA")
    return "Convert: " + ", ".join(parts)


def order_label(order):
    key, b = order
    return ORDER_LABELS[key].format(b=b or 0)


def strategy_label(cfg):
    return (f"{order_label(cfg['order'])}; surplus → {cfg['surplus'] or 'first taxable'}; "
            f"{roth_label(cfg['roth'])}" + (f"; {cfg['priority']}" if cfg.get("priority") else ""))


def apply(plan, cfg):
    """Write a strategy into `plan` (in place)."""
    s = plan.settings
    s.withdrawal_order, b = cfg["order"]
    if b is not None:
        s.tax_smart_bracket = b
    s.surplus_account = cfg["surplus"]
    for k, v in cfg["roth"].items():
        setattr(plan.roth, k, v)
    if cfg.get("priority"):
        plan.roth.priority = cfg["priority"]
    return plan


def current(plan):
    s, r = plan.settings, plan.roth
    return {
        "order": (s.withdrawal_order, s.tax_smart_bracket if s.withdrawal_order == "tax_smart" else None),
        "surplus": s.surplus_account,
        "roth": {"mode": r.mode if r.mode != "fixed" else "none", "max_bracket": r.max_bracket,
                 "irmaa_max_tier": r.irmaa_max_tier, "ltcg_zero": r.ltcg_zero, "aca_max_fpl": r.aca_max_fpl},
        "priority": r.priority if _couple_with_trad(plan) else None,
    }


def _couple_with_trad(plan):
    owners = {a.owner for a in plan.accounts if a.type == "trad"}
    return len(plan.people) == 2 and len(owners) >= 2


def _summary_only(args):
    plan, seq = args
    s = simulate(plan, seq).summary
    return s["success"], s["lifetime_value_real"]


def _median(v):
    v = sorted(v)
    n = len(v)
    return 0.0 if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def sequences(plan, basis, n_mc=200):
    years = plan_years(plan)
    if basis == "montecarlo":
        return markets.bootstrap_sequences(years, n_mc, 5, seed=42)
    wrap = plan.settings.historical_wrap
    return [markets.historical_sequence(y, years, wrap) for y in markets.historical_start_years(years, wrap)]


def _key(cfg):
    return (cfg["order"], cfg["surplus"], tuple(sorted(cfg["roth"].items())), cfg.get("priority"))


def optimize(plan, basis="historical", progress=None, passes=2):
    """Returns {"best": cfg, "best_score": ..., "evaluated": [rows], "baseline": row}."""
    from .model import ROTH_PRIORITY

    seqs = sequences(plan, basis)
    taxable = [a.name for a in plan.accounts if a.type == "taxable"] or [""]
    dims = {
        "order": ORDERS,
        "surplus": taxable,
        "roth": roth_candidates(plan),
    }
    if _couple_with_trad(plan):
        dims["priority"] = ROTH_PRIORITY
    cur = current(plan)
    if cur["surplus"] not in taxable:
        cur["surplus"] = taxable[0]
    if cur["order"] not in ORDERS:
        cur["order"] = ORDERS[0]

    cache, evaluated = {}, []
    est_total = 1 + passes * sum(len(v) for v in dims.values() if len(v) > 1)
    done = [0]
    workers = max(1, (os.cpu_count() or 2) - 1)
    ctx = multiprocessing.get_context("spawn")

    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
        def score(cfg):
            k = _key(cfg)
            if k in cache:
                return cache[k]
            p = apply(copy.deepcopy(plan), cfg)
            res = list(ex.map(_summary_only, [(p, s) for s in seqs], chunksize=4))
            rate = sum(ok for ok, _ in res) / len(res)
            value = _median([v for _, v in res])
            row = {"cfg": copy.deepcopy(cfg), "label": strategy_label(cfg), "success_rate": rate,
                   "median_value": value, "runs": len(res)}
            cache[k] = row
            evaluated.append(row)
            done[0] += 1
            if progress:
                progress(min(done[0], est_total - 1), est_total)
            return row

        rank = lambda r: (round(r["success_rate"], 6), r["median_value"])
        baseline = score(cur)
        best = baseline
        for _ in range(passes):
            changed = False
            for dim, options in dims.items():
                if len(options) < 2:
                    continue
                for opt in options:
                    cand = dict(best["cfg"], **{dim: opt})
                    r = score(cand)
                    if rank(r) > rank(best):
                        best, changed = r, True
            if not changed:
                break
    if progress:
        progress(1, 1)
    evaluated.sort(key=lambda r: (-r["success_rate"], -r["median_value"]))
    return {"best": best["cfg"], "best_row": best, "baseline": baseline, "evaluated": evaluated,
            "basis": basis, "runs_per_strategy": len(seqs)}
