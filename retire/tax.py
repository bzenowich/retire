"""Federal and state income tax projection."""
from dataclasses import dataclass

from . import tax_data as td


def bracket_tax(income, brackets, index=1.0):
    """Tax on `income` through progressive `brackets` scaled by `index`."""
    tax, lower = 0.0, 0.0
    for upper, rate in brackets:
        top = float("inf") if upper is None else upper * index
        if income <= lower:
            break
        tax += (min(income, top) - lower) * rate
        lower = top
    return tax


def marginal_rate(income, brackets, index=1.0):
    for upper, rate in brackets:
        if upper is None or income < upper * index:
            return rate
    return brackets[-1][1]


def bracket_top(rate, filing, index=1.0):
    """Top of the ordinary bracket with this rate (taxable-income dollars)."""
    for upper, r in td.FED_BRACKETS[filing]:
        if abs(r - rate) < 1e-9:
            return float("inf") if upper is None else upper * index
    raise ValueError(f"no {rate:.0%} bracket")


def taxable_social_security(ss, other_income, filing):
    """IRS worksheet: up to 85% of benefits are taxable."""
    if ss <= 0:
        return 0.0
    base1, base2 = td.SS_TAX_THRESHOLDS[filing]
    prov = other_income + 0.5 * ss
    if prov <= base1:
        return 0.0
    if prov <= base2:
        return min(0.5 * ss, 0.5 * (prov - base1))
    return min(0.85 * ss, 0.85 * (prov - base2) + min(0.5 * ss, 0.5 * (base2 - base1)))


@dataclass
class TaxInput:
    year: int
    filing: str                 # "mfj" | "single"
    index: float                # bracket inflation index relative to tax base year
    wages: float = 0.0          # W-2 wages after pre-tax deferrals
    ordinary: float = 0.0       # pensions, interest, trad withdrawals, conversions
    qualified: float = 0.0      # qualified dividends + long-term gains
    ss: float = 0.0             # gross Social Security benefits
    retirement_income: float = 0.0   # subset of ordinary from IRAs/pensions (for state)
    treasury_interest: float = 0.0   # subset of ordinary exempt from state tax
    n65: int = 0                # filers aged 65+
    ages: tuple = ()            # filer ages (state exclusions)
    state: str = "None (no income tax)"
    state_rate: float = 0.05    # used for "Custom flat rate"
    penalty_base: float = 0.0   # early-withdrawal amounts subject to 10%


@dataclass
class TaxResult:
    agi: float
    taxable_income: float
    taxable_ss: float
    federal: float
    niit: float
    state: float
    penalty: float
    marginal: float
    magi_irmaa: float
    magi_aca: float

    @property
    def total(self):
        return self.federal + self.niit + self.state + self.penalty


def compute(t: TaxInput) -> TaxResult:
    f = t.filing
    other = t.wages + t.ordinary + t.qualified
    tss = taxable_social_security(t.ss, other, f)
    agi = other + tss
    ded = td.STD_DEDUCTION[f] * t.index + td.ADDL_65_DEDUCTION[f] * t.index * t.n65
    if t.n65 and td.SENIOR_BONUS_YEARS[0] <= t.year <= td.SENIOR_BONUS_YEARS[1]:
        bonus = td.SENIOR_BONUS - 0.06 * max(0.0, agi - td.SENIOR_BONUS_PHASEOUT[f])
        ded += max(0.0, bonus) * t.n65
    taxable = max(0.0, agi - ded)
    qual = min(t.qualified, taxable)
    ord_taxable = taxable - qual
    fed = bracket_tax(ord_taxable, td.FED_BRACKETS[f], t.index)
    # LTCG/qualified dividends stack on top of ordinary income
    lower = ord_taxable
    for upper, rate in td.LTCG_BRACKETS[f]:
        top = float("inf") if upper is None else upper * t.index
        if lower >= top:
            continue
        portion = min(lower + qual, top) - lower
        if portion <= 0:
            break
        fed += portion * rate
        lower += portion
        qual -= portion
        if qual <= 0:
            break
    nii = t.qualified + max(0.0, t.ordinary - t.retirement_income)
    niit = td.NIIT_RATE * max(0.0, min(nii, agi - td.NIIT_THRESHOLD[f]))
    st = state_tax(t, agi, tss)
    return TaxResult(
        agi=agi, taxable_income=taxable, taxable_ss=tss, federal=fed, niit=niit,
        state=st, penalty=td.EARLY_WITHDRAWAL_PENALTY * t.penalty_base,
        marginal=marginal_rate(ord_taxable, td.FED_BRACKETS[f], t.index),
        magi_irmaa=agi, magi_aca=agi + (t.ss - tss),
    )


def state_tax(t: TaxInput, agi, taxable_ss):
    s = td.STATES.get(t.state, td.NO_TAX)
    f = t.filing
    income = agi - t.treasury_interest
    if s.get("ss_exempt", True):
        income -= taxable_ss
    if s.get("retirement_exempt"):
        income -= t.retirement_income
    elif s.get("retirement_exclusion") and t.retirement_income > 0:
        excl = 0.0
        for age in t.ages:
            best = 0.0
            for min_age, amt in s["retirement_exclusion"]:
                if age >= min_age:
                    best = max(best, amt)
            excl += best
        income -= min(excl, t.retirement_income)
    income -= s["std"][f] * t.index
    if income <= 0:
        return 0.0
    if s.get("custom"):
        return income * t.state_rate
    return bracket_tax(income, s["brackets"][f], t.index)


def fica(wages, index=1.0):
    return td.FICA_SS_RATE * min(wages, td.SS_WAGE_BASE * index) + td.FICA_MED_RATE * wages
