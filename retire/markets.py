"""Market return sequences: fixed assumptions, historical windows, Monte Carlo."""
import csv
import os
import random
from functools import lru_cache

DATA = os.path.join(os.path.dirname(__file__), "data", "historical_returns.csv")

# Named stress periods (start year of a historical sequence)
STRESS_PERIODS = {
    "1929 Great Depression": 1929,
    "1937 Double-dip recession": 1937,
    "1946 Post-war inflation": 1946,
    "1966 Stagflation era": 1966,
    "1973 Oil shock / bear market": 1973,
    "2000 Dot-com crash": 2000,
    "2007 Global financial crisis": 2007,
    "2022 Stock + bond drawdown": 2022,
}


@lru_cache(maxsize=1)
def historical():
    """List of dicts: year, and decimal returns per asset class plus inflation."""
    rows = []
    with open(DATA) as f:
        reader = csv.DictReader(line for line in f if not line.startswith("#"))
        for r in reader:
            sp, tb, bd, cpi = (float(r[k]) / 100 for k in ("sp500", "tbill", "tbond", "cpi"))
            rows.append({
                "year": int(r["year"]),
                "stocks": sp,
                "treasuries": bd,
                # intermediate investment-grade aggregate proxy
                "bonds": 0.5 * bd + 0.5 * tb + 0.008,
                "moneymarket": tb,
                "cash": max(0.0, tb - 0.01),
                # TIPS proxy: realized inflation plus a 1% real yield
                "tips": cpi + 0.01,
                "inflation": cpi,
            })
    return rows


def fixed_sequence(settings, years):
    s = settings
    r = {"cash": s.ret_cash, "stocks": s.ret_stocks, "bonds": s.ret_bonds, "tips": s.ret_tips,
         "moneymarket": s.ret_moneymarket, "treasuries": s.ret_treasuries}
    row = {k: v / 100 for k, v in r.items()}
    row["inflation"] = s.inflation / 100
    return [dict(row) for _ in range(years)]


def historical_sequence(start_year, years, wrap=False):
    data = historical()
    first = data[0]["year"]
    i0 = start_year - first
    if i0 < 0 or i0 >= len(data):
        raise ValueError(f"no data for {start_year}")
    if not wrap and i0 + years > len(data):
        return None
    return [data[(i0 + k) % len(data)] for k in range(years)]


def historical_start_years(years, wrap=False):
    data = historical()
    if wrap:
        return [r["year"] for r in data]
    return [r["year"] for r in data[: max(0, len(data) - years + 1)]]


def bootstrap_sequences(years, n, block=5, seed=None):
    """Block bootstrap of joint historical years (keeps cross-asset correlation
    and some serial correlation such as inflation persistence)."""
    data = historical()
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        seq = []
        while len(seq) < years:
            i = rng.randrange(len(data))
            for k in range(block):
                seq.append(data[(i + k) % len(data)])
        out.append(seq[:years])
    return out
