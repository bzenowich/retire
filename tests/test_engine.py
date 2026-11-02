import pytest

from retire import analysis, benefits as bn, markets, tax as tx
from retire.engine import plan_years, simulate
from retire.model import Account, CashFlow, Ladder, Person, Plan, Settings, example_plan, load_workspace, save_workspace


def test_federal_brackets_mfj_2026():
    r = tx.compute(tx.TaxInput(year=2026, filing="mfj", index=1.0, wages=100_000 + 32_200))
    assert r.taxable_income == pytest.approx(100_000)
    assert r.federal == pytest.approx(2_480 + 0.12 * (100_000 - 24_800))


def test_ltcg_stacks_on_ordinary():
    r = tx.compute(tx.TaxInput(year=2026, filing="mfj", index=1.0, wages=32_200 + 90_000, qualified=20_000))
    # 8,900 of gains fit in the 0% bracket, the rest at 15%
    ordinary_tax = tx.bracket_tax(90_000, tx.td.FED_BRACKETS["mfj"])
    assert r.federal == pytest.approx(ordinary_tax + 0.15 * (20_000 - 8_900))


def test_social_security_taxation_caps_at_85pct():
    assert tx.taxable_social_security(40_000, 0, "mfj") == 0
    assert tx.taxable_social_security(40_000, 500_000, "mfj") == pytest.approx(34_000)


def test_ss_claiming_factors():
    assert bn.own_benefit_factor(62, 67) == pytest.approx(0.70)
    assert bn.own_benefit_factor(70, 67) == pytest.approx(1.24)
    assert bn.spousal_factor(62, 67) == pytest.approx(0.325)


def test_aca_subsidy_and_cliff():
    net, sub, pct = bn.aca_cost(60_000, 2, 24_000, 1.0, 1.0, False)
    assert 0 < sub < 24_000 and pct == pytest.approx(100 * 60_000 / 21_150)
    net, sub, _ = bn.aca_cost(90_000, 2, 24_000, 1.0, 1.0, False)   # > 400% FPL
    assert sub == 0 and net == 24_000
    _, sub_e, _ = bn.aca_cost(90_000, 2, 24_000, 1.0, 1.0, True)
    assert sub_e > 0


def test_irmaa_tiers():
    assert bn.irmaa_tier(200_000, "mfj") == 0
    assert bn.irmaa_tier(250_000, "mfj") == 1
    assert bn.irmaa_tier(1_000_000, "single") == 5


def simple_plan(**kw):
    s = Settings(start_year=2026, inflation=2.5, ret_stocks=6, ret_bonds=4)
    p = Plan(settings=s)
    p.people = [Person("Pat", "1961-01-01", "2026-01-01", 90, 2500, 67)]
    p.accounts = [Account("IRA", "Pat", "trad", 800_000, 0, stocks=60, bonds=40),
                  Account("Brokerage", "Pat", "taxable", 200_000, 150_000, stocks=60, bonds=40)]
    p.cashflows = [CashFlow("expense", "Living", "", "", "", 4_000, "monthly")]
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_simple_plan_succeeds_and_rmds_start():
    r = simulate(simple_plan())
    assert r.success
    rmd_years = [row["year"] for row in r.rows if row["rmd"] > 0]
    assert rmd_years[0] == 1961 + 75 - 0 or rmd_years[0] == 2036  # born 1961 -> RMD age 75
    assert r.rows[-1]["year"] == 1961 + 90


def test_overspending_fails():
    p = simple_plan()
    p.cashflows[0].amount = 20_000
    r = simulate(p)
    assert not r.success and r.first_shortfall is not None


def test_early_withdrawal_penalty():
    p = simple_plan()
    p.people[0].dob = "1975-01-01"
    p.accounts[1].balance = 0
    r = simulate(p)
    assert r.rows[0]["penalty"] > 0


def test_frequencies_and_one_time():
    p = simple_plan()
    p.cashflows = [CashFlow("expense", "Car", "", "2027-06", "", 30_000, "one-time", "none"),
                   CashFlow("expense", "Weekly", "", "", "", 100, "weekly", "none")]
    r = simulate(p)
    assert r.rows[1]["expenses"] == pytest.approx(30_000 + 5_200, rel=0.01)
    assert r.rows[0]["expenses"] == pytest.approx(5_200, rel=0.01)


def test_roth_conversion_respects_bracket():
    p = simple_plan()
    p.roth.mode, p.roth.max_bracket, p.roth.irmaa_max_tier = "optimize", 12, -1
    r = simulate(p)
    row = r.rows[0]
    top = tx.bracket_top(0.12, "single", row["cpi"])
    assert row["conversion"] > 0
    assert row["ordinary_taxable"] <= top + 200   # LTCG stacks above the bracket


def test_ladder_maturities_fund_spending():
    p = simple_plan()
    p.ladders = [Ladder("TIPS", "IRA", "TIPS", "fixed", 5, "2027", "", 30_000, 2.0)]
    r = simulate(p)
    assert [row["year"] for row in r.rows if row["ladder_maturities"] > 0][:5] == list(range(2027, 2032))
    assert r.rows[1]["bal_ladder"] > 0


def test_historical_and_mc_run():
    p = simple_plan()
    h = analysis.historical(p)
    assert len(h["runs"]) == len(markets.historical_start_years(plan_years(p)))
    mc = analysis.monte_carlo(p, n=20, seed=3)
    assert 0 <= mc["success_rate"] <= 1


def test_duplicate_and_roundtrip(tmp_path):
    p = example_plan()
    q = p.duplicate("What if")
    q.settings.inflation = 4
    assert p.settings.inflation != 4
    path = tmp_path / "w.json"
    save_workspace(path, [p, q])
    loaded = load_workspace(path)
    assert [x.name for x in loaded] == [p.name, "What if"]
    assert simulate(loaded[1]).rows


def test_ss_optimizer_couple():
    p = example_plan()
    res = analysis.ss_optimizer(p, ages=[62, 67, 70])
    assert len(res["grid"]) == 9 and res["best"]["ages"]


def test_rmd_uses_prior_year_end_and_aggregates_iras():
    p = simple_plan()
    p.people[0].dob = "1946-01-01"           # age 80 in 2026, RMD age 72
    p.people[0].life_expectancy = 95
    p.accounts = [Account("IRA A", "Pat", "trad", 600_000, 0, stocks=60, bonds=40),
                  Account("IRA B", "Pat", "trad", 400_000, 0, stocks=60, bonds=40),
                  Account("Brokerage", "Pat", "taxable", 500_000, 400_000, stocks=60, bonds=40)]
    from retire.model import Deposit
    p.deposits = [Deposit("Rollover", "IRA A", "2026", "2027", 100_000, "one-time", "none", 0, "cash")]
    p.settings.yale_enabled = False
    r = simulate(p)
    row = r.rows[0]
    assert row["rmd_required"] == pytest.approx(1_000_000 / 20.2)   # deposit not counted
    assert row["rmd"] == pytest.approx(row["rmd_required"])
    assert row["wd_IRA A"] > 0 and row["wd_IRA B"] > 0


def test_yale_rule_first_year_and_smoothing():
    p = simple_plan()
    p.settings.inflation = 3.0
    p.cashflows = []
    r = simulate(p)
    first, second = r.rows[0], r.rows[1]
    assert first["rule_withdrawal"] == pytest.approx(0.05 * 1_000_000)
    nw = first["net_worth"]
    assert second["rule_withdrawal"] == pytest.approx(0.3 * 0.05 * nw + 0.7 * first["rule_withdrawal"] * 1.03)
