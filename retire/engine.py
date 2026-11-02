"""Year-by-year retirement simulation.

Conventions
-----------
* Amounts entered in the plan are in start-year ("today's") dollars and are
  grown by their indexing rule (CPI, healthcare, college, custom or none).
* Each simulated year: flows (income, deposits, withdrawals, conversions,
  ladder purchases) happen at the start of the year, then accounts grow at the
  year's market returns.
* Taxes are solved simultaneously with withdrawals: withdrawing to pay a tax
  creates more tax, so the cash need is iterated to a fixed point.
* End dates are exclusive (first day the item is no longer active).
"""
from dataclasses import dataclass, field
from datetime import date

from . import benefits as bn
from . import markets
from . import tax as tx
from . import tax_data as td
from .model import ALLOCATION_PRESETS, parse_date

PER_YEAR = {"weekly": 52, "bi-weekly": 26, "monthly": 12, "yearly": 1}


@dataclass
class Result:
    plan_name: str
    rows: list
    success: bool
    first_shortfall: int = None
    summary: dict = field(default_factory=dict)

    def series(self, key):
        return [r[key] for r in self.rows]


class _Acct:
    __slots__ = ("name", "owner", "type", "bal", "basis", "alloc", "rungs", "synthetic")

    def __init__(self, name, owner, typ, bal, basis, alloc, synthetic=False):
        self.name, self.owner, self.type = name, owner, typ
        self.bal, self.basis, self.alloc = float(bal), float(basis), alloc
        self.rungs = []          # [maturity year, market value, kind, yield]
        self.synthetic = synthetic

    @property
    def total(self):
        return self.bal + sum(r[1] for r in self.rungs)


def _active_fraction(year, start, end):
    """Fraction of calendar `year` inside [start, end)."""
    y0, y1 = date(year, 1, 1), date(year + 1, 1, 1)
    a, b = max(start, y0), min(end, y1)
    if b <= a:
        return 0.0
    return (b - a).days / (y1 - y0).days


class Simulator:
    def __init__(self, plan, sequence=None):
        self.plan = plan
        s = self.s = plan.settings
        self.people = plan.people
        if not self.people:
            raise ValueError("plan needs at least one investor")
        self.y0 = int(s.start_year)
        self.y1 = max(p.death_year for p in self.people)
        self.n = self.y1 - self.y0 + 1
        self.seq = sequence or markets.fixed_sequence(s, self.n)
        if len(self.seq) < self.n:
            raise ValueError("market sequence shorter than plan")
        self.base_tax_index = (1 + s.inflation / 100) ** (self.y0 - td.BASE_YEAR)
        self._build_accounts()

    # ------------------------------------------------------------ setup
    def _build_accounts(self):
        preset = ALLOCATION_PRESETS.get(self.s.allocation_preset)
        self.accts = []
        for a in self.plan.accounts:
            alloc = {"stocks": preset[0] / 100, "bonds": preset[1] / 100} if preset else a.allocation()
            basis = a.cost_basis if a.type in ("taxable", "roth") else 0.0
            self.accts.append(_Acct(a.name, a.owner, a.type, a.balance, min(basis, a.balance) if a.type == "taxable" else basis, alloc))
        # make sure every person with a Trad account has somewhere to convert to
        for p in self.people:
            trads = [a for a in self.accts if a.type == "trad" and a.owner == p.name]
            if trads and not any(a.type == "roth" and a.owner == p.name for a in self.accts):
                self.accts.append(_Acct(f"{p.name} Roth (conversions)", p.name, "roth", 0, 0,
                                        dict(trads[0].alloc), synthetic=True))
        if not any(a.type == "taxable" for a in self.accts):
            self.accts.append(_Acct("Cash (surplus)", self.people[0].name, "taxable", 0, 0,
                                    {"moneymarket": 1.0}, synthetic=True))
        self.by_name = {a.name: i for i, a in enumerate(self.accts)}
        self._names = {p.name: i for i, p in enumerate(self.people)}

    def _end_date(self, text, owner):
        t = (text or "").strip().lower()
        p = self.plan.person(owner) if owner else None
        if t == "retirement":
            return (p or self.people[0]).retire
        if t == "death":
            return date((p or self.people[0]).death_year + 1, 1, 1)
        return parse_date(text, date(self.y1 + 1, 1, 1))

    def _growth(self, indexing, rate, t):
        s = self.s
        if indexing == "none":
            return 1.0
        if indexing == "custom":
            return (1 + rate / 100) ** t
        cpi = self.cpi[t]
        if indexing == "healthcare":
            return cpi * ((1 + s.healthcare_inflation / 100) / (1 + s.inflation / 100)) ** t
        if indexing == "college":
            return cpi * ((1 + s.college_inflation / 100) / (1 + s.inflation / 100)) ** t
        return cpi

    def _flow_amount(self, item, year, t, owner):
        start = parse_date(item.start, date(self.y0, 1, 1))
        end = self._end_date(item.end, owner)
        if item.frequency == "one-time":
            if start.year != year:
                return 0.0
            mult = 1.0
        else:
            mult = PER_YEAR.get(item.frequency, 1) * _active_fraction(year, start, end)
        if mult <= 0:
            return 0.0
        return float(item.amount) * mult * self._growth(item.indexing, item.rate, t)

    # ------------------------------------------------------------ helpers
    def _owner_index(self, name, alive):
        idx = next((i for i, p in enumerate(self.people) if p.name == name), 0)
        if alive[idx]:
            return idx
        for i, a in enumerate(alive):
            if a:
                return i
        return idx

    # ------------------------------------------------------------ main loop
    def run(self):
        s, people, accts = self.s, self.people, self.accts
        n = self.n
        self.cpi = [1.0] * (n + 1)
        for t in range(n):
            self.cpi[t + 1] = self.cpi[t] * (1 + self.seq[t]["inflation"])
        rows, magi_hist = [], []
        success, first_short = True, None
        roth_cfg = self.plan.roth
        # the spending rule starts in the first full year everyone is retired
        ap_start = max(self.y0, max(p.retire.year + (0 if (p.retire.month, p.retire.day) == (1, 1) else 1)
                                    for p in people))
        ap_prev = None

        for t in range(n):
            y = self.y0 + t
            mk = self.seq[t]
            cpi = self.cpi[t]
            tix = self.base_tax_index * cpi
            hix = self._growth("healthcare", 0, t)
            alive = [y <= p.death_year for p in people]
            if not any(alive):
                break
            filing = "mfj" if sum(alive) >= 2 else "single"
            ages = [y - p.birth.year for p in people]
            mid = date(y, 7, 1)
            early = [p.age_at(mid) < 59.5 for p in people]
            acct_owner = [self._owner_index(a.owner, alive) for a in accts]
            acct_early = [early[o] for o in acct_owner]
            row = {"year": y, "cpi": cpi}
            start_totals = [a.total for a in accts]     # Dec 31 of the prior year
            start_total = sum(start_totals)
            for i, p in enumerate(people):
                row[f"age_{p.name}"] = ages[i] if alive[i] else None

            # ---- ladders: maturities and purchases
            ladder_cash = [0.0] * len(accts)
            ladder_buy = 0.0
            for i, a in enumerate(accts):
                matured = [r for r in a.rungs if r[0] <= y]
                if matured:
                    a.rungs = [r for r in a.rungs if r[0] > y]
                    v = sum(r[1] for r in matured)
                    a.bal += v
                    ladder_cash[i] += v
            for L in self.plan.ladders:
                ai = self.by_name.get(L.account)
                if ai is None:
                    continue
                a = accts[ai]
                ls = parse_date(L.start, date(self.y0, 1, 1)).year
                le = parse_date(L.end, date(self.y1, 1, 1)).year if L.end else self.y1
                nyrs = max(1, int(L.years))
                if y == ls:
                    targets = list(range(y, y + nyrs))
                elif L.mode == "rolling" and ls < y:
                    targets = [y + nyrs - 1]
                else:
                    targets = []
                yl = L.yield_pct / 100
                for m in targets:
                    if m > le or m > self.y1 or any(r[0] == m and r[2] == L.kind for r in a.rungs):
                        continue
                    k = m - y
                    if L.kind == "TIPS":     # real face value; principal accrues with CPI
                        cost = L.annual_amount * cpi / (1 + yl) ** k
                    else:                    # CD: nominal face sized with assumed inflation
                        cost = L.annual_amount * cpi * ((1 + s.inflation / 100) / (1 + yl)) ** k
                    cost = min(a.bal, cost)
                    if cost <= 0:
                        continue
                    ladder_buy += cost
                    if k == 0:
                        ladder_cash[ai] += cost      # stays as cash, spent this year
                    else:
                        a.bal -= cost
                        a.rungs.append([m, cost, L.kind, yl])
            row["ladder_purchases"] = ladder_buy
            row["ladder_maturities"] = sum(ladder_cash)

            # ---- income
            wages = [0.0] * len(people)
            ordinary = qualified = tax_free = pension = 0.0
            for cf in self.plan.cashflows:
                if cf.kind != "income":
                    continue
                oi = next((i for i, p in enumerate(people) if p.name == cf.owner), None)
                if oi is not None and not alive[oi]:
                    continue
                amt = self._flow_amount(cf, y, t, cf.owner)
                if amt == 0:
                    continue
                tt = cf.tax_type
                if tt == "wages":
                    wages[oi if oi is not None else 0] += amt
                elif tt == "pension":
                    pension += amt
                elif tt == "ltcg":
                    qualified += amt
                elif tt == "tax-free":
                    tax_free += amt
                else:
                    ordinary += amt
            gross_wages = sum(wages)
            fica = sum(tx.fica(w, tix) for w in wages)

            # ---- Social Security
            ss = bn.ss_benefits(y, people, alive, cpi)
            ss_total = sum(ss)

            # ---- expenses
            expenses, by_cat = 0.0, {}
            for cf in self.plan.cashflows:
                if cf.kind != "expense":
                    continue
                oi = next((i for i, p in enumerate(people) if p.name == cf.owner), None)
                if oi is not None and not alive[oi]:
                    continue
                amt = self._flow_amount(cf, y, t, cf.owner)
                if amt:
                    expenses += amt
                    cat = cf.category or "other"
                    by_cat[cat] = by_cat.get(cat, 0.0) + amt

            # ---- deposits
            pretax_deferral = 0.0
            deposit_cash = 0.0
            deposits_total = 0.0
            for d in self.plan.deposits:
                ai = self.by_name.get(d.account)
                if ai is None:
                    continue
                a = accts[ai]
                oi = acct_owner[ai]
                if a.owner in self._names and not alive[self._names[a.owner]]:
                    continue
                amt = self._flow_amount(d, y, t, a.owner)
                if amt <= 0:
                    continue
                if d.source == "payroll":
                    amt = min(amt, wages[oi])
                    if a.type == "trad":
                        pretax_deferral += amt
                        wages[oi] -= amt
                    deposit_cash += amt
                elif d.source == "cash":
                    deposit_cash += amt
                a.bal += amt
                if a.type in ("taxable", "roth"):
                    a.basis += amt
                deposits_total += amt
            row["deposits"] = deposits_total

            # ---- taxable-account investment income (reinvested)
            int_income = treas_income = div_income = 0.0
            for a in accts:
                if a.type != "taxable" or a.bal <= 0:
                    continue
                for k, w in a.alloc.items():
                    if k == "stocks":
                        div_income += a.bal * w * s.dividend_yield / 100
                    else:
                        r = max(0.0, mk[k]) * a.bal * w
                        int_income += r
                        if k == "treasuries":
                            treas_income += r

            # ---- RMDs and scheduled withdrawals (forced, before spending)
            forced_cash = 0.0
            rmd_total = 0.0
            f_ord = f_ret = f_ltcg = f_pen = 0.0
            withdrawn = [0.0] * len(accts)
            # RMD per owner = sum of their Trad balances on Dec 31 of the prior year
            # / Uniform Lifetime factor; like IRAs it may come from any of them
            # (pro-rata here, selling ladder rungs if the cash part runs short).
            by_owner = {}
            for i, a in enumerate(accts):
                if a.type == "trad":
                    by_owner.setdefault(acct_owner[i], []).append(i)
            rmd_req = 0.0
            for o, idxs in by_owner.items():
                if not alive[o] or ages[o] < td.rmd_age(people[o].birth.year):
                    continue
                prior = sum(start_totals[i] for i in idxs)
                if prior <= 0:
                    continue
                req = prior / td.UNIFORM_LIFETIME[min(ages[o], 120)]
                rmd_req += req
                avail = sum(accts[i].bal for i in idxs)
                for i in idxs:
                    share = req * accts[i].bal / avail if avail > 0 else 0.0
                    amt = min(share, accts[i].bal)
                    accts[i].bal -= amt
                    withdrawn[i] += amt
                    rmd_total += amt
                left = req - sum(withdrawn[i] for i in idxs)
                for i in idxs:                     # sell rungs, earliest maturity first
                    for rung in sorted(accts[i].rungs, key=lambda r: r[0]):
                        if left <= 1e-6:
                            break
                        amt = min(left, rung[1])
                        rung[1] -= amt
                        left -= amt
                        withdrawn[i] += amt
                        rmd_total += amt
                    accts[i].rungs = [r for r in accts[i].rungs if r[1] > 1e-6]
            forced_cash += rmd_total
            f_ord += rmd_total
            f_ret += rmd_total
            for w in self.plan.withdrawals:
                ai = self.by_name.get(w.account)
                if ai is None:
                    continue
                a = accts[ai]
                ws = parse_date(w.start, date(self.y0, 1, 1)).year
                we = self._end_date(w.end, a.owner).year
                if not ws <= y < we:
                    continue
                amt = a.bal * w.amount / 100 if w.mode == "percent" else w.amount * cpi
                amt = max(0.0, min(amt, a.bal))
                if amt <= 0:
                    continue
                o, l, pe, rt = self._take(a, amt, acct_early[ai])
                f_ord += o
                f_ltcg += l
                f_pen += pe
                f_ret += rt
                withdrawn[ai] += amt
                forced_cash += amt
            row["rmd"] = rmd_total
            row["rmd_required"] = rmd_req

            # ---- Medicare (IRMAA uses MAGI from two years earlier)
            if t >= 2:
                look = magi_hist[t - 2]
            elif s.prior_magi > 0:
                look = s.prior_magi * cpi
            else:
                look = 0.0
            look_filing = filing
            tier = bn.irmaa_tier(look, look_filing, tix)
            medicare = irmaa = 0.0
            aca_months = []
            for i, p in enumerate(people):
                if not alive[i]:
                    aca_months.append(0.0)
                    continue
                m65 = bn.months_in_year_after(y, p.birth, 65)
                if m65:
                    base, sur = bn.medicare_premiums(tier, tix, hix, m65, s.medicare_other)
                    medicare += base
                    irmaa += sur
                ret_frac = _active_fraction(y, p.retire, date(9999, 1, 1))
                aca_months.append(max(0.0, min(ret_frac, (12 - m65) / 12)) if s.aca_enabled else 0.0)
            on_aca = any(m > 0 for m in aca_months)
            household = sum(alive) + int(s.dependents)

            # ---- solve the year
            base = dict(
                y=y, t=t, filing=filing, tix=tix, hix=hix, ages=ages, alive=alive,
                wages=sum(wages), ordinary=ordinary + pension + int_income + f_ord,
                retirement=pension + f_ret, qualified=qualified + div_income + f_ltcg,
                treasury=treas_income, ss=ss_total, pen=f_pen,
                n65=sum(1 for i in range(len(people)) if alive[i] and ages[i] >= 65),
                fixed_need=expenses + deposit_cash + medicare + irmaa + fica
                - (gross_wages + ordinary + pension + qualified + tax_free + ss_total + forced_cash),
                aca_months=aca_months, household=household, acct_early=acct_early,
                ladder_cash=ladder_cash,
            )

            # ---- Yale endowment rule: portfolio withdrawal budget
            #   first year: rate x portfolio
            #   then: (1 - w) x rate x portfolio + w x last year's withdrawal x (1 + inflation)
            rule_wd = 0.0
            if y >= ap_start and s.yale_enabled:
                target = s.yale_rate / 100 * start_total
                if ap_prev is None:
                    rule_wd = target
                else:
                    w = s.yale_weight / 100
                    rule_wd = (1 - w) * target + w * ap_prev * (1 + self.seq[t - 1]["inflation"])
                rule_wd = max(0.0, min(rule_wd, start_total))
                ap_prev = rule_wd
            base["floor"] = max(0.0, rule_wd - forced_cash)

            conv_target = 0.0
            r_start = roth_cfg.start_year or self.y0
            r_end = roth_cfg.end_year or self.y1
            trad_avail = sum(a.bal for a in accts if a.type == "trad")
            if roth_cfg.mode != "none" and r_start <= y <= r_end and trad_avail > 0:
                if roth_cfg.mode == "fixed":
                    conv_target = min(trad_avail, roth_cfg.fixed_amount * cpi)
                else:
                    conv_target = self._optimize_conversion(base, trad_avail, roth_cfg, on_aca)
            sol = self._solve(base, conv_target)

            # ---- commit
            for i, a in enumerate(accts):
                a.bal, a.basis = sol["bal"][i], sol["basis"][i]
                withdrawn[i] += sol["draws"][i]
            # required = need + forced (net of income); rule money beyond it is spent
            port_out = forced_cash + sum(sol["draws"])
            required = sol["need"] + forced_cash
            discretionary = max(0.0, min(port_out, rule_wd) - required) if rule_wd else 0.0
            discretionary = min(discretionary, max(0.0, port_out - required))
            surplus = max(0.0, port_out - required - discretionary)
            if surplus > 0:
                si = self.by_name.get(s.surplus_account)
                if si is None or accts[si].type != "taxable":
                    si = next(i for i, a in enumerate(accts) if a.type == "taxable")
                accts[si].bal += surplus
                accts[si].basis += surplus
            if sol["short"] > 1.0:
                success = False
                first_short = first_short or y

            magi_hist.append(sol["tax"].magi_irmaa)

            # ---- growth (taxable accounts reinvest dividends/interest into basis)
            for a in accts:
                r = sum(w * mk[k] for k, w in a.alloc.items())
                if a.type == "taxable":
                    inc = a.bal * sum((s.dividend_yield / 100 if k == "stocks" else max(0.0, mk[k])) * w
                                      for k, w in a.alloc.items())
                    a.basis += inc
                a.bal = max(0.0, a.bal * (1 + r))
                if a.type == "taxable":
                    a.basis = min(a.basis, a.bal) if a.bal > 0 else 0.0
                for rung in a.rungs:
                    rung[1] *= (1 + rung[3]) * ((1 + mk["inflation"]) if rung[2] == "TIPS" else 1)

            # ---- record
            T = sol["tax"]
            row.update({
                "wages": gross_wages, "pretax_deferral": pretax_deferral,
                "other_income": ordinary + pension + qualified + tax_free,
                "ss_total": ss_total, "invest_income": int_income + div_income,
                "expenses": expenses, "medicare": medicare, "irmaa": irmaa, "irmaa_tier": tier,
                "aca_premium": sol["aca_net"], "aca_subsidy": sol["aca_sub"], "fpl_pct": sol["fpl_pct"],
                "healthcare_auto": medicare + irmaa + sol["aca_net"],
                "conversion": sol["conv"], "withdrawals": sum(sol["draws"]) + forced_cash,
                "spend_withdrawals": sum(sol["draws"]),
                "fed_tax": T.federal, "niit": T.niit, "state_tax": T.state, "penalty": T.penalty,
                "fica": fica, "total_tax": T.total + fica,
                "agi": T.agi, "magi": T.magi_irmaa, "taxable_income": T.taxable_income,
                "ordinary_taxable": sol["ord_taxable"],
                "marginal": T.marginal, "surplus": surplus, "shortfall": sol["short"],
                "rule_withdrawal": rule_wd, "discretionary": discretionary,
            })
            for i, p in enumerate(people):
                row[f"ss_{p.name}"] = ss[i]
            for k, v in by_cat.items():
                row[f"exp_{k}"] = v
            totals = {"taxable": 0.0, "trad": 0.0, "roth": 0.0}
            ladder_total = 0.0
            for i, a in enumerate(accts):
                row[f"bal_{a.name}"] = a.total
                row[f"wd_{a.name}"] = withdrawn[i]
                totals[a.type] += a.bal
                ladder_total += sum(r[1] for r in a.rungs)
            row["bal_taxable"], row["bal_trad"], row["bal_roth"] = totals["taxable"], totals["trad"], totals["roth"]
            row["bal_ladder"] = ladder_total
            nw = sum(totals.values()) + ladder_total
            row["net_worth"] = nw
            trad_all = sum(a.total for a in accts if a.type == "trad")
            row["after_tax_worth"] = nw - trad_all * s.heir_tax_rate / 100
            end_cpi = self.cpi[t + 1]
            row["end_cpi"] = end_cpi
            row["net_worth_real"] = nw / end_cpi
            row["after_tax_worth_real"] = row["after_tax_worth"] / end_cpi
            rows.append(row)

        return self._result(rows, success, first_short)

    # ------------------------------------------------------------ year solver
    def _take(self, a, amt, is_early):
        """Withdraw `amt` from account; returns (ordinary, ltcg, penalty_base, retirement)."""
        amt = min(amt, a.bal)
        o = l = pe = rt = 0.0
        if amt <= 0:
            return o, l, pe, rt
        if a.type == "taxable":
            frac = a.basis / a.bal if a.bal > 0 else 1.0
            l = amt * (1 - frac)
            a.basis -= amt * frac
        elif a.type == "trad":
            o = rt = amt
            if is_early:
                pe = amt
        else:
            fb = min(amt, a.basis)
            a.basis -= fb
            earn = amt - fb
            if is_early and earn > 0:
                o = pe = earn
        a.bal -= amt
        return o, l, pe, rt

    def _conversion_order(self, bal, ctx):
        people, pr = self.people, self.plan.roth.priority
        trads = [i for i, a in enumerate(self.accts) if a.type == "trad" and bal[i] > 0]
        owner = lambda i: self._owner_index(self.accts[i].owner, ctx["alive"])
        if pr == "older first":
            trads.sort(key=lambda i: people[owner(i)].birth)
        elif pr == "younger first":
            trads.sort(key=lambda i: people[owner(i)].birth, reverse=True)
        elif pr == "larger balance first":
            trads.sort(key=lambda i: -bal[i])
        return trads

    def _roth_for(self, owner_idx):
        name = self.people[owner_idx].name
        for i, a in enumerate(self.accts):
            if a.type == "roth" and a.owner == name:
                return i
        return next(i for i, a in enumerate(self.accts) if a.type == "roth")

    def _solve(self, ctx, conv):
        """Find withdrawals that fund the year's need given conversion `conv`."""
        accts, s = self.accts, self.s
        nA = len(accts)
        bal0 = [a.bal for a in accts]
        basis0 = [a.basis for a in accts]
        types = [a.type for a in accts]
        early = ctx["acct_early"]

        # apply conversion
        conv_bal, conv_basis = bal0[:], basis0[:]
        conv_done = 0.0
        if conv > 0:
            order = self._conversion_order(conv_bal, ctx)
            if self.plan.roth.priority == "proportional":
                tot = sum(conv_bal[i] for i in order) or 1.0
                plan = [(i, conv * conv_bal[i] / tot) for i in order]
            else:
                plan, left = [], conv
                for i in order:
                    take = min(left, conv_bal[i])
                    plan.append((i, take))
                    left -= take
            for i, amt in plan:
                amt = min(amt, conv_bal[i])
                if amt <= 0:
                    continue
                conv_bal[i] -= amt
                ri = self._roth_for(self._owner_index(accts[i].owner, ctx["alive"]))
                conv_bal[ri] += amt
                conv_basis[ri] += amt
                conv_done += amt

        order_mode = s.withdrawal_order
        smart_top = tx.bracket_top(s.tax_smart_bracket / 100, ctx["filing"], ctx["tix"]) \
            if order_mode == "tax_smart" else 0.0

        floor = ctx.get("floor", 0.0)
        need = max(0.0, ctx["fixed_need"], floor)
        prev = None
        headroom = 0.0
        result = None
        for _ in range(40):
            bal, basis = conv_bal[:], conv_basis[:]
            draws = [0.0] * nA
            comp = {"o": 0.0, "l": 0.0, "pe": 0.0, "rt": 0.0}
            remaining = need

            def take(i, amt):
                nonlocal remaining
                amt = min(amt, bal[i], remaining)
                if amt <= 1e-9:
                    return
                if types[i] == "taxable":
                    frac = basis[i] / bal[i] if bal[i] > 0 else 1.0
                    comp["l"] += amt * (1 - frac)
                    basis[i] -= amt * frac
                elif types[i] == "trad":
                    comp["o"] += amt
                    comp["rt"] += amt
                    if early[i]:
                        comp["pe"] += amt
                else:
                    fb = min(amt, basis[i])
                    basis[i] -= fb
                    earn = amt - fb
                    if early[i] and earn > 0:
                        comp["o"] += earn
                        comp["pe"] += earn
                bal[i] -= amt
                draws[i] += amt
                remaining -= amt

            # matured ladder rungs are spent first
            for i in range(nA):
                if ctx["ladder_cash"][i] > 0:
                    take(i, ctx["ladder_cash"][i])

            def group(typ, want_early):
                return [i for i in range(nA) if types[i] == typ and early[i] == want_early]

            if order_mode == "proportional":
                free = [i for i in range(nA) if types[i] == "taxable" or not early[i]]
                penalized = [i for i in range(nA) if types[i] != "taxable" and early[i]]
                for idx in (free, penalized):
                    tot = sum(bal[i] for i in idx)
                    if tot > 0 and remaining > 0:
                        r0 = remaining
                        for i in idx:
                            take(i, r0 * bal[i] / tot)
            else:
                if order_mode == "tax_smart":
                    room = max(0.0, headroom)
                    for i in group("trad", False):
                        before = remaining
                        take(i, room)
                        room -= before - remaining
                    seq = ["taxable", "trad", "roth"]
                else:
                    seq = order_mode.split("_")
                for want_early in (False, True):
                    for typ in seq:
                        if typ == "taxable":
                            idx = [] if want_early else [i for i in range(nA) if types[i] == "taxable"]
                        else:
                            idx = group(typ, want_early)
                        for i in idx:
                            take(i, remaining)

            short = max(0.0, remaining)
            ti = tx.TaxInput(
                year=ctx["y"], filing=ctx["filing"], index=ctx["tix"], wages=ctx["wages"],
                ordinary=ctx["ordinary"] + conv_done + comp["o"],
                qualified=ctx["qualified"] + comp["l"], ss=ctx["ss"],
                retirement_income=ctx["retirement"] + comp["rt"] + conv_done,
                treasury_interest=ctx["treasury"], n65=ctx["n65"],
                ages=tuple(a for a, al in zip(ctx["ages"], ctx["alive"]) if al),
                state=s.state, state_rate=s.state_rate / 100,
                penalty_base=ctx["pen"] + comp["pe"],
            )
            T = tx.compute(ti)
            aca_net, aca_sub, fpl_pct = self._aca(ctx, T.magi_aca)
            ord_taxable = T.taxable_income - min(ti.qualified, T.taxable_income)
            headroom = smart_top - (ord_taxable - sum(draws[i] for i in range(nA) if types[i] == "trad"))
            new_need = ctx["fixed_need"] + T.total + aca_net
            result = dict(bal=bal, basis=basis, draws=draws, tax=T, aca_net=aca_net, aca_sub=aca_sub,
                          fpl_pct=fpl_pct, need=new_need, short=short, conv=conv_done,
                          ord_taxable=ord_taxable, magi_aca=T.magi_aca)
            target = max(0.0, new_need, floor)
            if prev is not None and abs(target - need) < 0.5:
                break
            prev = need
            need = target
        # only required spending (not the spending-rule budget) counts as a shortfall
        if result["need"] < 0:
            result["short"] = 0.0
        else:
            funded = sum(result["draws"])
            result["short"] = max(0.0, result["need"] - funded)
        return result

    def _aca(self, ctx, magi):
        s = self.s
        months = ctx["aca_months"]
        if not any(months):
            return 0.0, 0.0, 0.0
        bench = sum(s.aca_benchmark * m for m in months) * ctx["hix"]
        frac = max(months)
        net, sub, pct = bn.aca_cost(magi, ctx["household"], bench / frac, frac, ctx["tix"], s.aca_enhanced)
        return net, sub, pct

    def _optimize_conversion(self, ctx, trad_avail, cfg, on_aca):
        filing, tix = ctx["filing"], ctx["tix"]
        limits = []
        if cfg.max_bracket and cfg.max_bracket > 0:
            top = tx.bracket_top(cfg.max_bracket / 100, filing, tix)
            limits.append(lambda r: top - r["ord_taxable"])
        if cfg.irmaa_max_tier is not None and cfg.irmaa_max_tier >= 0:
            if any(al and a + 2 >= 65 for a, al in zip(ctx["ages"], ctx["alive"])):
                thr = bn.irmaa_threshold(int(cfg.irmaa_max_tier), filing, tix)
                limits.append(lambda r: thr - 1 - r["tax"].magi_irmaa)
        if cfg.ltcg_zero:
            top0 = td.LTCG_BRACKETS[filing][0][0] * tix
            limits.append(lambda r: top0 - r["tax"].taxable_income)
        if cfg.aca_max_fpl and on_aca:
            cap = cfg.aca_max_fpl / 100 * bn.fpl(ctx["household"], tix)
            limits.append(lambda r: cap - 1 - r["magi_aca"])
        if not limits:
            return trad_avail

        def ok(c):
            r = self._solve(ctx, c)
            return r["short"] <= 1.0 and all(f(r) >= 0 for f in limits)

        if not ok(0.0):
            return 0.0
        if ok(trad_avail):
            return trad_avail
        lo, hi = 0.0, trad_avail
        while hi - lo > max(50.0, 1e-4 * hi):
            mid = (lo + hi) / 2
            if ok(mid):
                lo = mid
            else:
                hi = mid
        return lo

    # ------------------------------------------------------------ summary
    def _result(self, rows, success, first_short):
        last = rows[-1] if rows else {}
        def tot(key, real=False):
            return sum(r[key] / (r["cpi"] if real else 1) for r in rows)
        summary = {
            "success": success,
            "first_shortfall": first_short,
            "end_net_worth": last.get("net_worth", 0.0),
            "end_net_worth_real": last.get("net_worth_real", 0.0),
            "end_after_tax_real": last.get("after_tax_worth_real", 0.0),
            "lifetime_tax": tot("total_tax"),
            "lifetime_tax_real": tot("total_tax", True),
            "lifetime_income_tax_real": sum((r["fed_tax"] + r["state_tax"] + r["niit"]) / r["cpi"] for r in rows),
            "lifetime_irmaa_real": tot("irmaa", True),
            "lifetime_aca_subsidy_real": tot("aca_subsidy", True),
            "lifetime_penalty_real": tot("penalty", True),
            "lifetime_conversions_real": tot("conversion", True),
            "lifetime_ss_real": tot("ss_total", True),
            "lifetime_rmd_real": tot("rmd", True),
            "lifetime_discretionary_real": tot("discretionary", True),
            "total_shortfall_real": tot("shortfall", True),
            "years": len(rows),
        }
        # what the plan delivers: rule spending above needs + what is left after tax
        summary["lifetime_value_real"] = summary["lifetime_discretionary_real"] + summary["end_after_tax_real"]
        return Result(self.plan.name, rows, success, first_short, summary)


def simulate(plan, sequence=None):
    return Simulator(plan, sequence).run()


def plan_years(plan):
    return max(p.death_year for p in plan.people) - int(plan.settings.start_year) + 1
