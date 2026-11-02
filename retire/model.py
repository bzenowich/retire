"""Plan data model and JSON persistence."""
import copy
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import date

ASSET_CLASSES = ["cash", "stocks", "bonds", "tips", "moneymarket", "treasuries"]
ACCOUNT_TYPES = ["taxable", "trad", "roth"]
FREQUENCIES = ["one-time", "weekly", "bi-weekly", "monthly", "yearly"]
INDEXING = ["inflation", "none", "healthcare", "college", "custom"]
INCOME_TAX_TYPES = ["wages", "ordinary", "pension", "ltcg", "tax-free"]
DEPOSIT_SOURCES = ["cash", "payroll", "employer"]
WITHDRAWAL_ORDERS = {
    "tax_smart": "Tax-smart: fill bracket from Trad, then Taxable, Roth last",
    "taxable_trad_roth": "Taxable → Trad → Roth (conventional)",
    "trad_taxable_roth": "Trad → Taxable → Roth",
    "taxable_roth_trad": "Taxable → Roth → Trad",
    "proportional": "Pro-rata across all accounts",
}
ALLOCATION_PRESETS = {
    "Custom (per account)": None,
    "60/40": (60, 40), "70/30": (70, 30), "80/20": (80, 20),
    "90/10": (90, 10), "100/0": (100, 0),
}
ROTH_MODES = {
    "none": "No conversions",
    "fixed": "Fixed amount per year (today's $)",
    "optimize": "Fill up to limits (bracket / IRMAA / LTCG / ACA)",
}
ROTH_PRIORITY = ["older first", "younger first", "larger balance first", "proportional"]


def parse_date(s, default=None):
    if not s:
        return default
    if isinstance(s, date):
        return s
    s = str(s).strip()
    parts = s.split("-")
    try:
        if len(parts) == 1:
            return date(int(parts[0]), 1, 1)
        if len(parts) == 2:
            return date(int(parts[0]), int(parts[1]), 1)
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return default


@dataclass
class Person:
    name: str = "Me"
    dob: str = "1966-06-15"
    retirement_date: str = "2031-01-01"
    life_expectancy: int = 95
    ss_pia: float = 3000.0          # monthly benefit at full retirement age, today's $
    ss_claim_age: float = 67.0

    @property
    def birth(self):
        return parse_date(self.dob, date(1970, 1, 1))

    @property
    def retire(self):
        return parse_date(self.retirement_date, date(2100, 1, 1))

    def age_at(self, d: date):
        b = self.birth
        return d.year - b.year + (d.month - b.month) / 12.0 + (d.day - b.day) / 365.25

    @property
    def death_year(self):
        return self.birth.year + int(self.life_expectancy)


@dataclass
class Account:
    name: str = "Brokerage"
    owner: str = "Me"
    type: str = "taxable"
    balance: float = 0.0
    cost_basis: float = 0.0       # taxable: cost basis; roth: contribution basis
    cash: float = 0.0
    stocks: float = 60.0
    bonds: float = 40.0
    tips: float = 0.0
    moneymarket: float = 0.0
    treasuries: float = 0.0

    def allocation(self):
        w = {k: max(0.0, float(getattr(self, k) or 0)) for k in ASSET_CLASSES}
        s = sum(w.values())
        if s <= 0:
            return {"cash": 1.0}
        return {k: v / s for k, v in w.items() if v > 0}


@dataclass
class CashFlow:
    kind: str = "expense"         # "income" | "expense"
    title: str = ""
    owner: str = ""               # person name; blank = household
    start: str = ""               # YYYY[-MM[-DD]]; blank = plan start
    end: str = ""                 # date, blank = plan end, "retirement" = owner's retirement, "death"
    amount: float = 0.0           # per occurrence, in today's dollars when indexed
    frequency: str = "monthly"
    indexing: str = "inflation"
    rate: float = 0.0             # % per year when indexing == "custom"
    tax_type: str = "ordinary"    # incomes only
    category: str = ""            # expenses: housing, college, healthcare, ...


@dataclass
class Deposit:
    title: str = "401(k)"
    account: str = ""
    start: str = ""
    end: str = "retirement"
    amount: float = 0.0
    frequency: str = "yearly"
    indexing: str = "inflation"
    rate: float = 0.0
    source: str = "payroll"       # cash: from spending money; payroll: from wages (pre-tax if trad); employer


@dataclass
class WithdrawalRule:
    title: str = "Scheduled withdrawal"
    account: str = ""
    start: str = ""
    end: str = ""
    mode: str = "fixed"           # fixed (today's $ per year) | percent (of balance)
    amount: float = 0.0


@dataclass
class Ladder:
    name: str = "TIPS ladder"
    account: str = ""
    kind: str = "TIPS"            # TIPS (real yield, CPI-indexed) | CD (nominal yield)
    mode: str = "fixed"           # fixed n-year tent | rolling n-year
    years: int = 5
    start: str = ""
    end: str = ""                 # rolling: last maturity year
    annual_amount: float = 40_000.0   # today's $ per rung
    yield_pct: float = 2.0


@dataclass
class RothStrategy:
    mode: str = "none"
    fixed_amount: float = 50_000.0
    start_year: int = 0           # 0 = plan start
    end_year: int = 0             # 0 = year before first RMD
    max_bracket: float = 22.0     # fill ordinary income to the top of this % bracket (0 = no limit)
    irmaa_max_tier: int = 0       # keep MAGI within this IRMAA tier (-1 = ignore)
    ltcg_zero: bool = False       # keep taxable income inside the 0% LTCG bracket
    aca_max_fpl: float = 0.0      # keep MAGI under this % of FPL while on ACA (0 = ignore)
    priority: str = "older first"


@dataclass
class Settings:
    start_year: int = date.today().year
    state: str = "None (no income tax)"
    state_rate: float = 5.0
    inflation: float = 2.5
    healthcare_inflation: float = 5.0
    college_inflation: float = 4.0
    ret_cash: float = 0.5
    ret_stocks: float = 7.0
    ret_bonds: float = 4.5
    ret_tips: float = 4.0
    ret_moneymarket: float = 3.0
    ret_treasuries: float = 4.0
    dividend_yield: float = 1.5
    allocation_preset: str = "Custom (per account)"
    withdrawal_order: str = "tax_smart"
    tax_smart_bracket: float = 12.0
    surplus_account: str = ""
    dependents: int = 0
    aca_enabled: bool = True
    aca_benchmark: float = 11_000.0   # annual benchmark silver premium per adult, today's $
    aca_enhanced: bool = False
    medicare_other: float = 250.0     # monthly per person: Part D base + Medigap, today's $
    prior_magi: float = 0.0           # MAGI for the two years before start (IRMAA lookback)
    heir_tax_rate: float = 24.0       # used to value Trad balances after tax
    historical_wrap: bool = False
    autopilot_enabled: bool = True    # Hebeler Autopilot II spending in retirement
    autopilot_pct: float = 4.0        # first-year withdrawal, % of portfolio
    autopilot_real_return: float = 3.0   # real return used in the PMT term
    optimize_basis: str = "historical"   # historical | montecarlo


@dataclass
class Plan:
    name: str = "Base plan"
    people: list = field(default_factory=list)
    accounts: list = field(default_factory=list)
    cashflows: list = field(default_factory=list)
    deposits: list = field(default_factory=list)
    withdrawals: list = field(default_factory=list)
    ladders: list = field(default_factory=list)
    roth: RothStrategy = field(default_factory=RothStrategy)
    settings: Settings = field(default_factory=Settings)

    def duplicate(self, name=None):
        p = copy.deepcopy(self)
        p.name = name or f"{self.name} (copy)"
        return p

    def person(self, name):
        for p in self.people:
            if p.name == name:
                return p
        return self.people[0] if self.people else None

    def account(self, name):
        for a in self.accounts:
            if a.name == name:
                return a
        return None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        def build(klass, data):
            names = {f.name for f in fields(klass)}
            return klass(**{k: v for k, v in (data or {}).items() if k in names})
        return cls(
            name=d.get("name", "Plan"),
            people=[build(Person, x) for x in d.get("people", [])],
            accounts=[build(Account, x) for x in d.get("accounts", [])],
            cashflows=[build(CashFlow, x) for x in d.get("cashflows", [])],
            deposits=[build(Deposit, x) for x in d.get("deposits", [])],
            withdrawals=[build(WithdrawalRule, x) for x in d.get("withdrawals", [])],
            ladders=[build(Ladder, x) for x in d.get("ladders", [])],
            roth=build(RothStrategy, d.get("roth")),
            settings=build(Settings, d.get("settings")),
        )


def save_workspace(path, plans):
    with open(path, "w") as f:
        json.dump({"version": 1, "plans": [p.to_dict() for p in plans]}, f, indent=2)


def load_workspace(path):
    with open(path) as f:
        d = json.load(f)
    if "plans" not in d:          # a single plan file
        return [Plan.from_dict(d)]
    return [Plan.from_dict(x) for x in d["plans"]]


def example_plan():
    """A married couple, early retirement, college, ACA bridge to Medicare."""
    s = Settings(start_year=date.today().year, state="North Carolina")
    p = Plan(name="Base plan", settings=s)
    p.people = [
        Person("Alex", "1968-03-10", "2029-01-01", 92, 3400, 67),
        Person("Sam", "1970-09-22", "2029-01-01", 95, 2100, 67),
    ]
    p.accounts = [
        Account("Joint brokerage", "Alex", "taxable", 450_000, 300_000, cash=5, stocks=70, bonds=25),
        Account("Alex 401k", "Alex", "trad", 900_000, 0, stocks=60, bonds=40),
        Account("Sam IRA", "Sam", "trad", 350_000, 0, stocks=60, bonds=30, tips=10),
        Account("Alex Roth", "Alex", "roth", 120_000, 60_000, stocks=100, bonds=0),
        Account("Sam Roth", "Sam", "roth", 80_000, 50_000, stocks=100, bonds=0),
    ]
    p.cashflows = [
        CashFlow("income", "Alex salary", "Alex", "", "retirement", 175_000, "yearly", "inflation", 0, "wages"),
        CashFlow("income", "Sam salary", "Sam", "", "retirement", 85_000, "yearly", "inflation", 0, "wages"),
        CashFlow("expense", "Living expenses", "", "", "", 6_500, "monthly", "inflation", 0, "", "living"),
        CashFlow("expense", "Mortgage P&I", "", "", "2034-06", 2_400, "monthly", "none", 0, "", "housing"),
        CashFlow("expense", "Property tax & insurance", "", "", "", 9_000, "yearly", "inflation", 0, "", "housing"),
        CashFlow("expense", "College tuition", "", "2027-08", "2031-05", 32_000, "yearly", "college", 0, "", "college"),
        CashFlow("expense", "Employer health premium", "", "", "2029-01", 450, "monthly", "healthcare", 0, "", "healthcare"),
        CashFlow("expense", "Out-of-pocket medical", "", "", "", 4_000, "yearly", "healthcare", 0, "", "healthcare"),
        CashFlow("expense", "Travel (go-go years)", "", "2029", "2040", 15_000, "yearly", "inflation", 0, "", "travel"),
        CashFlow("expense", "Replace car", "", "2032-05", "", 40_000, "one-time", "inflation", 0, "", "auto"),
    ]
    p.deposits = [
        Deposit("Alex 401k deferral", "Alex 401k", "", "retirement", 24_500, "yearly", "none", 0, "payroll"),
        Deposit("Alex 401k match", "Alex 401k", "", "retirement", 8_000, "yearly", "none", 0, "employer"),
        Deposit("Sam Roth IRA", "Sam Roth", "", "retirement", 7_500, "yearly", "none", 0, "cash"),
    ]
    p.ladders = [Ladder("TIPS bridge", "Alex 401k", "TIPS", "fixed", 5, "2029", "", 30_000, 1.8)]
    p.roth = RothStrategy(mode="optimize", max_bracket=22, irmaa_max_tier=0, aca_max_fpl=400)
    s.surplus_account = "Joint brokerage"
    return p
