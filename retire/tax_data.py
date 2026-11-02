"""Tax, benefit and premium tables.

Base year is 2026 (IRS Rev. Proc. 2025-32 / One Big Beautiful Bill Act values).
Dollar thresholds are indexed forward by the simulated inflation index unless
the statute fixes them (Social Security taxation and NIIT thresholds).
State tables are simplified approximations - verify against your state's
current instructions before relying on them.
"""

BASE_YEAR = 2026

# (upper bound of bracket, rate); last bound is None (infinity)
FED_BRACKETS = {
    "mfj": [(24_800, .10), (100_800, .12), (211_400, .22), (403_550, .24),
            (512_450, .32), (768_700, .35), (None, .37)],
    "single": [(12_400, .10), (50_400, .12), (105_700, .22), (201_775, .24),
               (256_225, .32), (640_600, .35), (None, .37)],
}
LTCG_BRACKETS = {
    "mfj": [(98_900, 0.0), (613_700, .15), (None, .20)],
    "single": [(49_450, 0.0), (545_500, .15), (None, .20)],
}
STD_DEDUCTION = {"mfj": 32_200, "single": 16_100}
ADDL_65_DEDUCTION = {"mfj": 1_650, "single": 2_050}       # per person 65+
# OBBBA "senior bonus" deduction: $6,000 per person 65+, tax years 2025-2028,
# phased out at 6% of MAGI above the threshold (not indexed).
SENIOR_BONUS = 6_000
SENIOR_BONUS_YEARS = (2025, 2028)
SENIOR_BONUS_PHASEOUT = {"mfj": 150_000, "single": 75_000}

# Social Security benefit taxation thresholds (fixed by statute, not indexed)
SS_TAX_THRESHOLDS = {"mfj": (32_000, 44_000), "single": (25_000, 34_000)}
NIIT_THRESHOLD = {"mfj": 250_000, "single": 200_000}       # not indexed
NIIT_RATE = 0.038

FICA_SS_RATE = 0.062
FICA_MED_RATE = 0.0145
SS_WAGE_BASE = 184_500
EARLY_WITHDRAWAL_PENALTY = 0.10

# --- Medicare / IRMAA (2026). MAGI from two years prior. -----------------
PART_B_BASE = 202.90        # monthly
# tier lower bounds of MAGI; tier 0 = no surcharge
IRMAA_TIERS = {
    "mfj": [0, 218_000, 274_000, 342_000, 410_000, 750_000],
    "single": [0, 109_000, 137_000, 171_000, 205_000, 500_000],
}
IRMAA_PART_B_TOTAL = [202.90, 284.10, 405.80, 527.50, 649.20, 689.90]   # monthly
IRMAA_PART_D_ADD = [0.0, 14.50, 37.50, 60.40, 83.30, 91.00]            # monthly

# --- ACA premium tax credit -----------------------------------------------
FPL_BASE = 15_650           # 2025 FPL (used for 2026 coverage), 1 person
FPL_PER_ADDL = 5_500
# Original ACA applicable percentage (post-2025, enhanced credits expired):
# list of (fpl_pct_low, fpl_pct_high, pct_low, pct_high); >400% FPL -> no credit
ACA_APPLICABLE_ORIGINAL = [
    (0, 133, .0210, .0210), (133, 150, .0314, .0419), (150, 200, .0419, .0660),
    (200, 250, .0660, .0844), (250, 300, .0844, .0996), (300, 400, .0996, .0996),
]
# ARPA/IRA "enhanced" schedule (no 400% cliff; 8.5% cap above 400%)
ACA_APPLICABLE_ENHANCED = [
    (0, 150, 0.0, 0.0), (150, 200, 0.0, .02), (200, 250, .02, .04),
    (250, 300, .04, .06), (300, 400, .06, .085), (400, 10_000, .085, .085),
]

# --- RMDs: IRS Uniform Lifetime Table (2022+) ------------------------------
UNIFORM_LIFETIME = {
    72: 27.4, 73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0,
    79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7, 84: 16.8, 85: 16.0,
    86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2, 91: 11.5, 92: 10.8,
    93: 10.1, 94: 9.5, 95: 8.9, 96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8, 100: 6.4,
    101: 6.0, 102: 5.6, 103: 5.2, 104: 4.9, 105: 4.6, 106: 4.3, 107: 4.1,
    108: 3.9, 109: 3.7, 110: 3.5, 111: 3.4, 112: 3.3, 113: 3.1, 114: 3.0,
    115: 2.9, 116: 2.8, 117: 2.7, 118: 2.5, 119: 2.3, 120: 2.0,
}

# IRS Single Life Expectancy table (2022+): SEPP payments and inherited-IRA RMDs.
SINGLE_LIFE = {
    30: 55.3, 31: 54.4, 32: 53.4, 33: 52.5, 34: 51.5, 35: 50.5, 36: 49.6,
    37: 48.6, 38: 47.7, 39: 46.7, 40: 45.7, 41: 44.8, 42: 43.8, 43: 42.9,
    44: 41.9, 45: 41.0, 46: 40.0, 47: 39.0, 48: 38.1, 49: 37.1, 50: 36.2,
    51: 35.3, 52: 34.3, 53: 33.4, 54: 32.5, 55: 31.6, 56: 30.6, 57: 29.8,
    58: 28.9, 59: 28.0, 60: 27.1, 61: 26.2, 62: 25.4, 63: 24.5, 64: 23.7,
    65: 22.9, 66: 22.0, 67: 21.2, 68: 20.4, 69: 19.6, 70: 18.8, 71: 18.0,
    72: 17.2, 73: 16.4, 74: 15.6, 75: 14.8, 76: 14.1, 77: 13.3, 78: 12.6,
    79: 11.9, 80: 11.2, 81: 10.5, 82: 9.9, 83: 9.3, 84: 8.7, 85: 8.1,
    86: 7.6, 87: 7.1, 88: 6.6, 89: 6.1, 90: 5.7, 91: 5.3, 92: 4.9,
    93: 4.6, 94: 4.3, 95: 4.0, 96: 3.7, 97: 3.4, 98: 3.2, 99: 3.0,
    100: 2.8,
}


def single_life(age):
    if age < 30:
        return SINGLE_LIFE[30] + (30 - age) * 0.97
    return SINGLE_LIFE[min(age, 100)]


def rmd_age(birth_year):
    if birth_year <= 1950:
        return 72
    if birth_year <= 1959:
        return 73
    return 75


def _flat(rate):
    return [(None, rate)]


def _same(brk):
    return {"single": brk, "mfj": brk}


def _double(single):
    return [(None if b is None else b * 2, r) for b, r in single]


# State model fields:
#   brackets: {"single": [...], "mfj": [...]}
#   std: standard deduction/exemption {"single", "mfj"}
#   ss_exempt: Social Security not taxed
#   retirement_exempt: IRA/401k/pension distributions fully exempt
#   retirement_exclusion: [(min_age, amount_per_person)], highest age match wins
#   wages_only_pa: PA-style - retirement income and SS exempt
NO_TAX = {"brackets": _same(_flat(0.0)), "std": {"single": 0, "mfj": 0}}
_CA_S = [(10_756, .01), (25_499, .02), (40_245, .04), (55_866, .06),
         (70_606, .08), (360_659, .093), (432_787, .103), (721_314, .113),
         (1_000_000, .123), (None, .133)]
_NY_S = [(8_500, .04), (11_700, .045), (13_900, .0525), (80_650, .055),
         (215_400, .06), (1_077_550, .0685), (5_000_000, .0965),
         (25_000_000, .103), (None, .109)]
_NY_M = [(17_150, .04), (23_600, .045), (27_900, .0525), (161_550, .055),
         (323_200, .06), (2_155_350, .0685), (5_000_000, .0965),
         (25_000_000, .103), (None, .109)]
_NJ_S = [(20_000, .014), (35_000, .0175), (40_000, .035), (75_000, .05525),
         (500_000, .0637), (1_000_000, .0897), (None, .1075)]
_NJ_M = [(20_000, .014), (50_000, .0175), (70_000, .0245), (80_000, .035),
         (150_000, .05525), (500_000, .0637), (1_000_000, .0897), (None, .1075)]
_OR_S = [(4_400, .0475), (11_050, .0675), (125_000, .0875), (None, .099)]
_VA = [(3_000, .02), (5_000, .03), (17_000, .05), (None, .0575)]

STATES = {
    "None (no income tax)": NO_TAX,
    "California": {"brackets": {"single": _CA_S, "mfj": _double(_CA_S)},
                   "std": {"single": 5_540, "mfj": 11_080}, "ss_exempt": True},
    "New York": {"brackets": {"single": _NY_S, "mfj": _NY_M},
                 "std": {"single": 8_000, "mfj": 16_050}, "ss_exempt": True,
                 "retirement_exclusion": [(59.5, 20_000)]},
    "New Jersey": {"brackets": {"single": _NJ_S, "mfj": _NJ_M},
                   "std": {"single": 1_000, "mfj": 2_000}, "ss_exempt": True,
                   "retirement_exclusion": [(62, 50_000)]},
    "Oregon": {"brackets": {"single": _OR_S, "mfj": _double(_OR_S)},
               "std": {"single": 2_800, "mfj": 5_600}, "ss_exempt": True},
    "Virginia": {"brackets": _same(_VA), "std": {"single": 8_500, "mfj": 17_000},
                 "ss_exempt": True, "retirement_exclusion": [(65, 12_000)]},
    "Georgia": {"brackets": _same(_flat(.0519)), "std": {"single": 12_000, "mfj": 24_000},
                "ss_exempt": True, "retirement_exclusion": [(62, 35_000), (65, 65_000)]},
    "North Carolina": {"brackets": _same(_flat(.0399)),
                       "std": {"single": 12_750, "mfj": 25_500}, "ss_exempt": True},
    "Colorado": {"brackets": _same(_flat(.044)), "std": {"single": 16_100, "mfj": 32_200},
                 "ss_exempt": True, "retirement_exclusion": [(55, 20_000), (65, 24_000)]},
    "Arizona": {"brackets": _same(_flat(.025)), "std": {"single": 16_100, "mfj": 32_200},
                "ss_exempt": True},
    "Massachusetts": {"brackets": _same([(1_083_150, .05), (None, .09)]),
                      "std": {"single": 4_400, "mfj": 8_800}, "ss_exempt": True},
    "Illinois": {"brackets": _same(_flat(.0495)), "std": {"single": 2_850, "mfj": 5_700},
                 "ss_exempt": True, "retirement_exempt": True},
    "Pennsylvania": {"brackets": _same(_flat(.0307)), "std": {"single": 0, "mfj": 0},
                     "ss_exempt": True, "retirement_exempt": True},
    "Custom flat rate": {"brackets": _same(_flat(0.05)), "std": {"single": 0, "mfj": 0},
                         "ss_exempt": True, "custom": True},
}
