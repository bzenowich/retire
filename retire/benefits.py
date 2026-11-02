"""Social Security, Medicare/IRMAA and ACA premium calculations."""
from . import tax_data as td


# ---------------------------------------------------------------- Social Security
def full_retirement_age(birth_year):
    if birth_year <= 1937:
        return 65.0
    if birth_year <= 1942:
        return 65 + (birth_year - 1937) * 2 / 12
    if birth_year <= 1954:
        return 66.0
    if birth_year <= 1959:
        return 66 + (birth_year - 1954) * 2 / 12
    return 67.0


def own_benefit_factor(claim_age, fra):
    claim_age = min(max(claim_age, 62.0), 70.0)
    months = round((claim_age - fra) * 12)
    if months < 0:
        early = -months
        return 1 - min(early, 36) * 5 / 900 - max(0, early - 36) * 5 / 1200
    return 1 + months * (0.08 / 12)


def spousal_factor(claim_age, fra):
    months = round((min(claim_age, fra) - fra) * 12)
    early = max(0, -months)
    return 0.5 * (1 - min(early, 36) * 25 / 3600 - max(0, early - 36) * 5 / 1200)


def months_in_year_after(year, birth, age):
    """Months of `year` at or after the person reaches `age`."""
    total = birth.year * 12 + (birth.month - 1) + round(age * 12)
    start = year * 12
    return max(0, min(12, start + 12 - total))


def ss_benefits(year, people, alive, cola_index):
    """Annual nominal benefit per person index, including spousal and survivor.

    people: list of Person; alive: list of bool for this year; cola_index:
    inflation index applied to PIAs (PIAs are entered in today's dollars).
    """
    n = len(people)
    out = [0.0] * n
    fras = [full_retirement_age(p.birth.year) for p in people]
    own = [p.ss_pia * own_benefit_factor(p.ss_claim_age, fras[i]) for i, p in enumerate(people)]
    for i, p in enumerate(people):
        if not alive[i]:
            continue
        months = months_in_year_after(year, p.birth, p.ss_claim_age)
        monthly = own[i]
        if n == 2:
            j = 1 - i
            sp = people[j]
            if alive[j]:
                # spousal top-up once both have filed
                if months and months_in_year_after(year, sp.birth, sp.ss_claim_age):
                    top = max(0.0, spousal_factor(p.ss_claim_age, fras[i]) * 2 * sp.ss_pia - p.ss_pia)
                    monthly += top
            else:
                # survivor: larger of own or deceased's benefit (floor 82.5% of PIA)
                age_at_death = sp.life_expectancy
                dec_claim = min(sp.ss_claim_age, max(62.0, age_at_death))
                dec = sp.ss_pia * own_benefit_factor(dec_claim, fras[j])
                dec = max(dec, 0.825 * sp.ss_pia) if age_at_death >= 62 else 0.0
                # survivor benefits are available from 60; modeled unreduced
                if year - p.birth.year >= 60 and dec > (monthly if months else 0):
                    monthly, months = dec, 12
        out[i] = monthly * months * cola_index
    return out


# ---------------------------------------------------------------- Medicare
def irmaa_tier(magi, filing, index=1.0):
    tiers = td.IRMAA_TIERS[filing]
    tier = 0
    for k, lo in enumerate(tiers):
        # thresholds are CPI-indexed except the top tier
        thr = lo if k == len(tiers) - 1 else lo * index
        if magi > thr:
            tier = k
    return tier


def irmaa_threshold(tier, filing, index=1.0):
    """MAGI ceiling for staying within `tier` (inf for top tier)."""
    tiers = td.IRMAA_TIERS[filing]
    if tier + 1 >= len(tiers):
        return float("inf")
    k = tier + 1
    return tiers[k] if k == len(tiers) - 1 else tiers[k] * index


def medicare_premiums(tier, index, health_index, months, other_monthly):
    """Returns (base_premium, irmaa_surcharge) for one person for `months`."""
    base = td.PART_B_BASE * health_index
    b_total = td.IRMAA_PART_B_TOTAL[tier] * health_index
    d_add = td.IRMAA_PART_D_ADD[tier] * health_index
    base_cost = (base + other_monthly * health_index) * months
    surcharge = (b_total - base + d_add) * months
    return base_cost, surcharge


# ---------------------------------------------------------------- ACA
def fpl(household, index=1.0):
    return (td.FPL_BASE + td.FPL_PER_ADDL * max(0, household - 1)) * index


def applicable_pct(fpl_pct, enhanced=False):
    table = td.ACA_APPLICABLE_ENHANCED if enhanced else td.ACA_APPLICABLE_ORIGINAL
    if fpl_pct < table[0][0]:
        return table[0][2]
    for lo, hi, plo, phi in table:
        if fpl_pct < hi:
            return plo + (phi - plo) * (fpl_pct - lo) / (hi - lo)
    return None  # above the cliff: no subsidy


def aca_cost(magi, household, benchmark_annual, months_frac, index, enhanced):
    """Returns (net premium, subsidy, fpl_pct) for a benchmark silver plan."""
    if benchmark_annual <= 0 or months_frac <= 0:
        return 0.0, 0.0, 0.0
    f = fpl(household, index)
    pct = 100 * magi / f
    ap = applicable_pct(pct, enhanced)
    gross = benchmark_annual * months_frac
    if ap is None:
        return gross, 0.0, pct
    expected = ap * magi * months_frac
    subsidy = max(0.0, gross - expected)
    return gross - subsidy, subsidy, pct
