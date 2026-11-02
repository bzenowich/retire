"""Main application window."""
import copy
import os
import threading
import traceback

import wx

from .. import analysis, charts, markets, optimize
from .. import model as m
from ..engine import simulate
from ..tax_data import STATES
from .widgets import ChartView, Col, Form, HAVE_WEBVIEW, ResultGrid, TableEditor

MONEY = "{:,.0f}"
PCT = "{:.1f}%"


class MainFrame(wx.Frame):
    def __init__(self, plans=None, path=None):
        super().__init__(None, title="Retirement Planner", size=(1400, 900))
        self.plans = plans or [m.example_plan()]
        self.path = path
        self.dirty = False
        self.plan = self.plans[0]
        self.last_result = None
        self._busy = False
        self._build_menu()
        self._build_ui()
        self.CreateStatusBar(2)
        self.SetStatusWidths([-3, -1])
        self._select_plan(0)
        self._update_title()
        self.Bind(wx.EVT_CLOSE, self._on_close)

    # ------------------------------------------------------------ menus
    def _build_menu(self):
        mb = wx.MenuBar()
        fm = wx.Menu()
        items = [
            (wx.ID_NEW, "&New workspace\tCtrl+N", self._new_workspace),
            (wx.ID_OPEN, "&Open…\tCtrl+O", self._open),
            (wx.ID_SAVE, "&Save\tCtrl+S", self._save),
            (wx.ID_SAVEAS, "Save &As…\tCtrl+Shift+S", self._save_as),
            (None, None, None),
            (wx.ID_ANY, "Load &example plan", self._load_example),
            (None, None, None),
            (wx.ID_EXIT, "&Quit\tCtrl+Q", lambda e: self.Close()),
        ]
        for wid, label, fn in items:
            if wid is None:
                fm.AppendSeparator()
                continue
            it = fm.Append(wid, label)
            self.Bind(wx.EVT_MENU, fn, it)
        mb.Append(fm, "&File")
        pm = wx.Menu()
        it = pm.Append(wx.ID_ANY, "&Run projection\tF5")
        self.Bind(wx.EVT_MENU, lambda e: self._run_projection(), it)
        it = pm.Append(wx.ID_ANY, "&Duplicate plan (what-if)\tCtrl+D")
        self.Bind(wx.EVT_MENU, self._dup_plan, it)
        mb.Append(pm, "&Plan")
        self.SetMenuBar(mb)

    # ------------------------------------------------------------ layout
    def _build_ui(self):
        split = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        left = wx.Panel(split)
        ls = wx.BoxSizer(wx.VERTICAL)
        ls.Add(wx.StaticText(left, label="Plans"), 0, wx.ALL, 6)
        self.plan_list = wx.ListBox(left)
        self.plan_list.Bind(wx.EVT_LISTBOX, lambda e: self._select_plan(self.plan_list.GetSelection()))
        ls.Add(self.plan_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        for label, fn in (("New plan", self._new_plan), ("Duplicate (what-if)", self._dup_plan),
                          ("Rename…", self._rename_plan), ("Delete", self._delete_plan)):
            b = wx.Button(left, label=label)
            b.Bind(wx.EVT_BUTTON, fn)
            ls.Add(b, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)
        ls.AddSpacer(8)
        left.SetSizer(ls)

        self.nb = wx.Notebook(split)
        self._tab_household()
        self._tab_accounts()
        self._tab_cashflows()
        self._tab_projection()
        self._tab_stress()
        self._tab_roth()
        self._tab_ss()
        self._tab_compare()
        split.SplitVertically(left, self.nb, 210)
        split.SetMinimumPaneSize(150)

    def _scrolled(self, label):
        p = wx.ScrolledWindow(self.nb)
        p.SetScrollRate(10, 10)
        self.nb.AddPage(p, label)
        return p

    # names for choice columns
    def _people_names(self):
        return [p.name for p in self.plan.people]

    def _owner_choices(self):
        return [""] + self._people_names()

    def _account_names(self):
        return [a.name for a in self.plan.accounts]

    def _tab_household(self):
        p = self._scrolled("Household")
        s = wx.BoxSizer(wx.VERTICAL)
        self.people_tbl = TableEditor(p, "Investors", [
            Col("name", "Name", width=110),
            Col("dob", "Date of birth", width=105),
            Col("retirement_date", "Retirement date", width=115),
            Col("life_expectancy", "Plan to age", "int", width=85),
            Col("ss_pia", "SS benefit @FRA ($/mo)", "float", width=160),
            Col("ss_claim_age", "SS claim age", "float", width=95),
        ], lambda: m.Person(name=f"Person {len(self.plan.people) + 1}"), self._changed_structure,
            "Dates are YYYY-MM-DD. SS benefit is your Primary Insurance Amount from ssa.gov, in today's dollars.")
        self.people_tbl.SetMinSize((-1, 200))
        s.Add(self.people_tbl, 0, wx.EXPAND | wx.ALL, 6)
        self.settings_form = Form(p, "Assumptions", [
            Col("start_year", "Plan start year", "int", width=80),
            Col("inflation", "Inflation %", "float", width=80),
            Col("state", "State", "choice", list(STATES)),
            Col("state_rate", "Custom state rate %", "float", width=80),
            Col("healthcare_inflation", "Healthcare inflation %", "float", width=80),
            Col("college_inflation", "College inflation %", "float", width=80),
            Col("dependents", "Dependents (ACA household)", "int", width=80),
            Col("heir_tax_rate", "Heir tax rate on Trad %", "float", width=80,
                tip="Used to value Traditional balances after tax at the end of the plan"),
        ], self._changed)
        s.Add(self.settings_form, 0, wx.EXPAND | wx.ALL, 6)
        self.health_form = Form(p, "Healthcare (auto-calculated premiums)", [
            Col("aca_enabled", "Use ACA between retirement and 65", "bool"),
            Col("aca_benchmark", "ACA benchmark silver, $/yr per adult", "float", width=100),
            Col("aca_enhanced", "Enhanced ACA subsidies (no 400% cliff)", "bool"),
            Col("medicare_other", "Part D + Medigap, $/mo per person", "float", width=100),
            Col("prior_magi", "MAGI for the 2 prior years (IRMAA)", "float", width=100),
        ], self._changed)
        s.Add(self.health_form, 0, wx.EXPAND | wx.ALL, 6)
        p.SetSizer(s)

    def _tab_accounts(self):
        p = self._scrolled("Accounts")
        s = wx.BoxSizer(wx.VERTICAL)
        self.accounts_tbl = TableEditor(p, "Existing accounts (balances at plan start)", [
            Col("name", "Account", width=150),
            Col("owner", "Owner", "choice", self._people_names, width=90),
            Col("type", "Type", "choice", m.ACCOUNT_TYPES, width=80),
            Col("balance", "Balance", "float", width=100),
            Col("cost_basis", "Basis / Roth contrib.", "float", width=140),
        ] + [Col(k, f"% {k}", "float", width=85) for k in m.ASSET_CLASSES],
            lambda: m.Account(name=f"Account {len(self.plan.accounts) + 1}",
                              owner=self.plan.people[0].name if self.plan.people else ""),
            self._changed_structure,
            "Taxable: cost basis drives capital gains. Roth: contributions/conversions withdrawable tax- and penalty-free.")
        self.accounts_tbl.SetMinSize((-1, 260))
        s.Add(self.accounts_tbl, 0, wx.EXPAND | wx.ALL, 6)
        self.alloc_form = Form(p, "Allocation strategy", [
            Col("allocation_preset", "Apply to all accounts", "choice", list(m.ALLOCATION_PRESETS),
                tip="Stocks/bonds preset overrides the per-account allocations"),
        ], self._changed, columns=1)
        s.Add(self.alloc_form, 0, wx.EXPAND | wx.ALL, 6)
        self.returns_form = Form(p, "Expected nominal returns for the deterministic projection (%/yr)", [
            Col("ret_stocks", "Stocks", "float", width=70), Col("ret_bonds", "Bonds", "float", width=70),
            Col("ret_tips", "TIPS", "float", width=70), Col("ret_treasuries", "Treasuries", "float", width=70),
            Col("ret_moneymarket", "Money market", "float", width=70), Col("ret_cash", "Cash", "float", width=70),
            Col("dividend_yield", "Stock dividend yield", "float", width=70),
        ], self._changed, columns=4)
        s.Add(self.returns_form, 0, wx.EXPAND | wx.ALL, 6)
        p.SetSizer(s)

    def _tab_cashflows(self):
        p = self._scrolled("Cash Flow")
        s = wx.BoxSizer(wx.VERTICAL)
        common = [
            Col("title", "Title", width=200),
            Col("owner", "Owner", "choice", self._owner_choices, width=80),
            Col("start", "Start", width=90),
            Col("end", "End", width=90),
            Col("amount", "Amount", "float", width=90),
            Col("frequency", "Frequency", "choice", m.FREQUENCIES, width=90),
            Col("indexing", "Grows with", "choice", m.INDEXING, width=95),
            Col("rate", "Custom %", "float", width=70),
        ]
        dates_help = ("Dates: YYYY, YYYY-MM or YYYY-MM-DD; blank start = plan start, blank end = plan end. "
                      "End is exclusive; 'retirement' or 'death' ties it to the owner. Amounts are per "
                      "occurrence in today's dollars (yearly = annual rate, prorated for partial years).")
        self.income_tbl = TableEditor(
            p, "Income", common + [Col("tax_type", "Tax type", "choice", m.INCOME_TAX_TYPES, width=100)],
            lambda: m.CashFlow(kind="income", title="New income", tax_type="ordinary", frequency="yearly"),
            self._changed, dates_help + "\nA home sale can be one-time 'tax-free' (excluded gain) or 'ltcg' income.")
        self.income_tbl.SetMinSize((-1, 200))
        s.Add(self.income_tbl, 0, wx.EXPAND | wx.ALL, 6)
        self.expense_tbl = TableEditor(
            p, "Expenses (required spending)", common + [Col("category", "Category", width=120)],
            lambda: m.CashFlow(kind="expense", title="New expense"), self._changed,
            "Housing, college, healthcare (ACA/Medicare/IRMAA premiums are added automatically), etc. "
            "Shortfalls against these count as plan failure.")
        self.expense_tbl.SetMinSize((-1, 280))
        s.Add(self.expense_tbl, 0, wx.EXPAND | wx.ALL, 6)
        self.dep_tbl = TableEditor(p, "Investments (contributions)", [
            Col("title", "Title", width=170),
            Col("account", "Into account", "choice", self._account_names, width=140),
            Col("start", "Start", width=80), Col("end", "End", width=90),
            Col("amount", "Amount", "float", width=90),
            Col("frequency", "Frequency", "choice", m.FREQUENCIES, width=90),
            Col("indexing", "Grows with", "choice", m.INDEXING, width=90),
            Col("rate", "Custom %", "float", width=70),
            Col("source", "Funded from", "choice", m.DEPOSIT_SOURCES, width=95),
        ], lambda: m.Deposit(account=(self._account_names() or [""])[0]), self._changed,
            "payroll = from the owner's wages (pre-tax when the account is Trad); cash = from spending money; "
            "employer = match/contribution that costs you nothing.")
        self.dep_tbl.SetMinSize((-1, 190))
        s.Add(self.dep_tbl, 0, wx.EXPAND | wx.ALL, 6)
        self.lad_tbl = TableEditor(p, "CD / TIPS ladders", [
            Col("name", "Name", width=130),
            Col("account", "Held in", "choice", self._account_names, width=140),
            Col("kind", "Kind", "choice", ["TIPS", "CD"], width=70),
            Col("mode", "Mode", "choice", ["fixed", "rolling"], width=80),
            Col("years", "Years", "int", width=60),
            Col("start", "Start", width=70),
            Col("end", "Last maturity", width=100),
            Col("annual_amount", "Per rung (today $)", "float", width=130),
            Col("yield_pct", "Yield % (real for TIPS)", "float", width=150),
        ], lambda: m.Ladder(account=(self._account_names() or [""])[0]), self._changed,
            "fixed = a one-time n-year tent bought at the start date; rolling = keep n years laddered, "
            "buying a new rung every year until 'last maturity'. Matured rungs are spent first.")
        self.lad_tbl.SetMinSize((-1, 150))
        s.Add(self.lad_tbl, 0, wx.EXPAND | wx.ALL, 6)
        p.SetSizer(s)

    def _chart_tab(self, label, controls):
        """Notebook page with a control row, chart (top) and table (bottom)."""
        p = wx.Panel(self.nb)
        self.nb.AddPage(p, label)
        s = wx.BoxSizer(wx.VERTICAL)
        bar = wx.BoxSizer(wx.HORIZONTAL)
        controls(p, bar)
        s.Add(bar, 0, wx.ALL | wx.EXPAND, 6)
        split = wx.SplitterWindow(p, style=wx.SP_LIVE_UPDATE)
        chart = ChartView(split)
        bottom = wx.Panel(split)
        bs = wx.BoxSizer(wx.VERTICAL)
        summary = wx.StaticText(bottom, label="")
        bs.Add(summary, 0, wx.ALL, 4)
        grid = ResultGrid(bottom)
        bs.Add(grid, 1, wx.EXPAND)
        bottom.SetSizer(bs)
        split.SplitHorizontally(chart, bottom, 480 if HAVE_WEBVIEW else 120)
        split.SetMinimumPaneSize(80)
        s.Add(split, 1, wx.EXPAND)
        p.SetSizer(s)
        return chart, summary, grid

    def _tab_projection(self):
        p = wx.Panel(self.nb)
        self.nb.AddPage(p, "Projection")
        self.proj_page = self.nb.GetPageCount() - 1
        s = wx.BoxSizer(wx.VERTICAL)
        bar = wx.BoxSizer(wx.HORIZONTAL)
        b = wx.Button(p, label="Optimize strategy")
        b.SetToolTip("Search withdrawal order, surplus account and Roth conversion strategy for the "
                     "highest success rate, then apply it and run the projection")
        b.Bind(wx.EVT_BUTTON, lambda e: self._run_optimize())
        bar.Add(b, 0, wx.RIGHT, 6)
        bar.Add(wx.StaticText(p, label="against"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self.opt_basis = wx.Choice(p, choices=["every historical start year", "Monte Carlo (200, fixed seed)"])
        self.opt_basis.SetSelection(0)
        bar.Add(self.opt_basis, 0, wx.RIGHT, 6)
        self.opt_gauge = wx.Gauge(p, range=100, size=(120, -1))
        bar.Add(self.opt_gauge, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
        r = wx.Button(p, label="Run projection (F5)")
        r.Bind(wx.EVT_BUTTON, lambda e: self._run_projection())
        bar.Add(r, 0, wx.RIGHT, 12)
        bar.Add(wx.StaticText(p, label="Chart:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self.proj_chart_choice = wx.Choice(p, choices=["Balances", "Cash flow", "Taxes & conversions",
                                                       "Healthcare premiums"])
        self.proj_chart_choice.SetSelection(0)
        self.proj_chart_choice.Bind(wx.EVT_CHOICE, lambda e: self._show_projection_chart(True))
        bar.Add(self.proj_chart_choice, 0, wx.RIGHT, 12)
        self.proj_real = wx.CheckBox(p, label="Today's dollars")
        self.proj_real.SetValue(True)
        self.proj_real.Bind(wx.EVT_CHECKBOX, lambda e: self._show_projection())
        bar.Add(self.proj_real, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        e = wx.Button(p, label="Export table CSV…")
        e.Bind(wx.EVT_BUTTON, lambda ev: self._export_csv(self.proj_grid))
        bar.Add(e, 0)
        s.Add(bar, 0, wx.ALL | wx.EXPAND, 6)

        row2 = wx.BoxSizer(wx.HORIZONTAL)
        self.spend_form = Form(p, "Withdrawals: Yale endowment rule", [
            Col("yale_enabled", "Enabled", "bool"),
            Col("yale_rate", "Withdrawal rate %", "float", width=60),
            Col("yale_weight", "Weight on last year %", "float", width=60,
                tip="First year = rate × portfolio. Then (1 − weight) × rate × portfolio + "
                    "weight × last year's withdrawal × (1 + inflation)"),
        ], self._changed, columns=3)
        row2.Add(self.spend_form, 0, wx.RIGHT, 8)
        sbox = wx.StaticBoxSizer(wx.VERTICAL, p, "Strategy in use")
        self.strategy_label = wx.StaticText(sbox.GetStaticBox(), label="")
        sbox.Add(self.strategy_label, 1, wx.EXPAND | wx.ALL, 6)
        row2.Add(sbox, 1, wx.EXPAND)
        s.Add(row2, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        split = wx.SplitterWindow(p, style=wx.SP_LIVE_UPDATE)
        self.proj_chart = ChartView(split)
        bottom = wx.Panel(split)
        bs = wx.BoxSizer(wx.VERTICAL)
        self.proj_summary = wx.StaticText(bottom, label="")
        bs.Add(self.proj_summary, 0, wx.ALL, 4)
        self.proj_tabs = wx.Notebook(bottom)
        self.proj_grid = ResultGrid(self.proj_tabs)
        self.opt_grid = ResultGrid(self.proj_tabs)
        self.proj_tabs.AddPage(self.proj_grid, "Year by year")
        self.proj_tabs.AddPage(self.opt_grid, "Strategy search")
        bs.Add(self.proj_tabs, 1, wx.EXPAND)
        bottom.SetSizer(bs)
        split.SplitHorizontally(self.proj_chart, bottom, 440)
        split.SetMinimumPaneSize(80)
        s.Add(split, 1, wx.EXPAND)
        p.SetSizer(s)

    def _tab_stress(self):
        def controls(p, bar):
            self.st_mode = wx.Choice(p, choices=["Historical: every start year", "Monte Carlo (bootstrap)",
                                                 "Stress periods"])
            self.st_mode.SetSelection(0)
            bar.Add(self.st_mode, 0, wx.RIGHT, 8)
            bar.Add(wx.StaticText(p, label="Simulations:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
            self.st_n = wx.SpinCtrl(p, min=50, max=10000, initial=500, size=(90, -1))
            bar.Add(self.st_n, 0, wx.RIGHT, 8)
            bar.Add(wx.StaticText(p, label="Block (yrs):"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
            self.st_block = wx.SpinCtrl(p, min=1, max=20, initial=5, size=(70, -1))
            bar.Add(self.st_block, 0, wx.RIGHT, 8)
            self.st_wrap = wx.CheckBox(p, label="Wrap history (more start years)")
            bar.Add(self.st_wrap, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
            self.st_view = wx.Choice(p, choices=["Summary chart", "All paths"])
            self.st_view.SetSelection(0)
            self.st_view.Bind(wx.EVT_CHOICE, lambda e: self._show_stress(True))
            bar.Add(self.st_view, 0, wx.RIGHT, 8)
            b = wx.Button(p, label="Run")
            b.Bind(wx.EVT_BUTTON, lambda e: self._run_stress())
            bar.Add(b, 0, wx.RIGHT, 8)
            self.st_gauge = wx.Gauge(p, range=100, size=(150, -1))
            bar.Add(self.st_gauge, 0, wx.ALIGN_CENTER_VERTICAL)
        self.st_chart, self.st_summary, self.st_grid = self._chart_tab("Stress test && Monte Carlo", controls)
        self.st_data = None

    def _tab_roth(self):
        def controls(p, bar):
            bar.Add(wx.StaticText(p, label="Spouse priority:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
            self.rx_priority = wx.Choice(p, choices=m.ROTH_PRIORITY)
            self.rx_priority.SetSelection(0)
            bar.Add(self.rx_priority, 0, wx.RIGHT, 8)
            self.rx_view = wx.Choice(p, choices=["Ending wealth by strategy", "Conversion schedule"])
            self.rx_view.SetSelection(0)
            self.rx_view.Bind(wx.EVT_CHOICE, lambda e: self._show_roth(True))
            bar.Add(self.rx_view, 0, wx.RIGHT, 8)
            b = wx.Button(p, label="Explore conversions")
            b.Bind(wx.EVT_BUTTON, lambda e: self._run_roth())
            bar.Add(b, 0, wx.RIGHT, 8)
            a = wx.Button(p, label="Apply selected (or best) to plan")
            a.Bind(wx.EVT_BUTTON, self._apply_roth)
            bar.Add(a, 0)
        self.rx_chart, self.rx_summary, self.rx_grid = self._chart_tab("Roth explorer", controls)
        self.rx_data = None

    def _tab_ss(self):
        def controls(p, bar):
            b = wx.Button(p, label="Optimize claiming ages")
            b.Bind(wx.EVT_BUTTON, lambda e: self._run_ss())
            bar.Add(b, 0, wx.RIGHT, 8)
            a = wx.Button(p, label="Apply selected (or best) to plan")
            a.Bind(wx.EVT_BUTTON, self._apply_ss)
            bar.Add(a, 0, wx.RIGHT, 8)
            bar.Add(wx.StaticText(p, label="Each combination runs the full plan (taxes, IRMAA, ACA, "
                                           "survivor benefits); ranked by success, then spending + ending after-tax wealth."),
                    0, wx.ALIGN_CENTER_VERTICAL)
        self.ss_chart, self.ss_summary, self.ss_grid = self._chart_tab("Social Security", controls)
        self.ss_data = None

    def _tab_compare(self):
        def controls(p, bar):
            bar.Add(wx.StaticText(p, label="Plans:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
            self.cmp_list = wx.CheckListBox(p, size=(260, 70))
            bar.Add(self.cmp_list, 0, wx.RIGHT, 8)
            self.cmp_metric = wx.Choice(p, choices=list(charts.METRICS))
            self.cmp_metric.SetSelection(0)
            bar.Add(self.cmp_metric, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 8)
            b = wx.Button(p, label="Compare")
            b.Bind(wx.EVT_BUTTON, lambda e: self._run_compare())
            bar.Add(b, 0, wx.ALIGN_CENTER_VERTICAL)
        self.cmp_chart, self.cmp_summary, self.cmp_grid = self._chart_tab("Compare plans", controls)

    # ------------------------------------------------------------ plan list
    def _refresh_plan_list(self):
        self.plan_list.Set([p.name for p in self.plans])
        i = self.plans.index(self.plan)
        self.plan_list.SetSelection(i)
        known = set(self.cmp_list.GetStrings())
        checked = set(self.cmp_list.GetCheckedStrings())
        self.cmp_list.Set([p.name for p in self.plans])
        for k, p in enumerate(self.plans):
            if p.name in checked or p.name not in known:      # new plans start ticked
                self.cmp_list.Check(k)

    def _select_plan(self, i):
        if i < 0 or i >= len(self.plans):
            return
        self.plan = self.plans[i]
        self.last_result = None
        self.people_tbl.bind(self.plan.people)
        self.accounts_tbl.bind(self.plan.accounts)
        self.income_tbl.bind(self.plan.cashflows, lambda c: c.kind == "income")
        self.expense_tbl.bind(self.plan.cashflows, lambda c: c.kind != "income")
        self.dep_tbl.bind(self.plan.deposits)
        self.lad_tbl.bind(self.plan.ladders)
        for f in (self.settings_form, self.health_form, self.alloc_form, self.returns_form,
                  self.spend_form):
            f.bind(self.plan.settings)
        self._update_strategy_label()
        self._refresh_plan_list()
        self.SetStatusText(f"Plan: {self.plan.name}", 0)

    def _new_plan(self, _):
        p = m.Plan(name=f"Plan {len(self.plans) + 1}")
        p.people = [m.Person()]
        p.accounts = [m.Account("Brokerage", "Me", "taxable", 100_000, 80_000),
                      m.Account("IRA", "Me", "trad", 300_000)]
        p.cashflows = [m.CashFlow("expense", "Living expenses", "", "", "", 5000, "monthly")]
        self.plans.append(p)
        self._mark_dirty()
        self._select_plan(len(self.plans) - 1)

    def _dup_plan(self, _):
        name = wx.GetTextFromUser("Name for the what-if copy:", "Duplicate plan",
                                  f"{self.plan.name} – what if", self)
        if not name:
            return
        p = self.plan.duplicate(self._unique_name(name))
        self.plans.insert(self.plans.index(self.plan) + 1, p)
        self._mark_dirty()
        self._select_plan(self.plans.index(p))

    def _unique_name(self, name):
        names, base, k = {p.name for p in self.plans}, name, 2
        while name in names:
            name, k = f"{base} {k}", k + 1
        return name

    def _rename_plan(self, _):
        name = wx.GetTextFromUser("New name:", "Rename plan", self.plan.name, self)
        if name and name != self.plan.name:
            self.plan.name = self._unique_name(name)
            self._mark_dirty()
            self._refresh_plan_list()

    def _delete_plan(self, _):
        if len(self.plans) == 1:
            wx.MessageBox("A workspace needs at least one plan.", "Delete plan", parent=self)
            return
        if wx.MessageBox(f"Delete '{self.plan.name}'?", "Delete plan", wx.YES_NO, self) != wx.YES:
            return
        i = self.plans.index(self.plan)
        self.plans.remove(self.plan)
        self._mark_dirty()
        self._select_plan(max(0, i - 1))

    # ------------------------------------------------------------ change tracking
    def _changed(self):
        self._mark_dirty()

    def _changed_structure(self):
        # names of people/accounts feed choice lists elsewhere
        self._mark_dirty()
        for t in (self.accounts_tbl, self.income_tbl, self.expense_tbl, self.dep_tbl, self.lad_tbl):
            t.refresh()

    def _mark_dirty(self):
        self.dirty = True
        self._update_title()

    def _update_title(self):
        name = os.path.basename(self.path) if self.path else "untitled"
        self.SetTitle(f"Retirement Planner — {name}{' *' if self.dirty else ''}")

    # ------------------------------------------------------------ files
    def _confirm_discard(self):
        if not self.dirty:
            return True
        r = wx.MessageBox("Save changes to the current workspace?", "Unsaved changes",
                          wx.YES_NO | wx.CANCEL, self)
        if r == wx.CANCEL:
            return False
        if r == wx.YES:
            return self._save(None)
        return True

    def _new_workspace(self, _):
        if not self._confirm_discard():
            return
        self.plans, self.path = [], None
        self._new_plan(None)
        self.plans = self.plans[-1:]
        self.dirty = False
        self._select_plan(0)
        self._update_title()

    def _load_example(self, _):
        p = m.example_plan()
        p.name = self._unique_name("Example plan")
        self.plans.append(p)
        self._mark_dirty()
        self._select_plan(len(self.plans) - 1)

    def _open(self, _):
        if not self._confirm_discard():
            return
        with wx.FileDialog(self, "Open workspace", wildcard="Retirement plans (*.json)|*.json",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            self.plans = m.load_workspace(path)
        except Exception as e:  # noqa: BLE001 - show any load error to the user
            wx.MessageBox(f"Could not open {path}:\n{e}", "Open", wx.ICON_ERROR, self)
            return
        self.path, self.dirty = path, False
        self._select_plan(0)
        self._update_title()

    def _save(self, _):
        if not self.path:
            return self._save_as(None)
        m.save_workspace(self.path, self.plans)
        self.dirty = False
        self._update_title()
        self.SetStatusText(f"Saved {self.path}", 0)
        return True

    def _save_as(self, _):
        with wx.FileDialog(self, "Save workspace", wildcard="Retirement plans (*.json)|*.json",
                           defaultFile="plans.json", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            path = dlg.GetPath()
        if not path.endswith(".json"):
            path += ".json"
        self.path = path
        return self._save(None)

    def _on_close(self, evt):
        if evt.CanVeto() and not self._confirm_discard():
            evt.Veto()
            return
        self.Destroy()

    def _export_csv(self, grid):
        if not grid.GetNumberRows():
            return
        with wx.FileDialog(self, "Export CSV", wildcard="CSV (*.csv)|*.csv", defaultFile="projection.csv",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                grid.to_csv(dlg.GetPath())

    # ------------------------------------------------------------ background jobs
    def _flush_edits(self):
        for t in (self.people_tbl, self.accounts_tbl, self.income_tbl, self.expense_tbl, self.dep_tbl,
                  self.lad_tbl):
            if t.grid.IsCellEditControlEnabled():
                t.grid.SaveEditControlValue()
                t.grid.DisableCellEditControl()

    def _validate(self):
        p = self.plan
        problems = []
        if not p.people:
            problems.append("Add at least one investor.")
        if len(p.people) > 2:
            problems.append("Social Security spousal/survivor logic and filing status support up to two investors.")
        names = [a.name for a in p.accounts]
        if len(set(names)) != len(names):
            problems.append("Account names must be unique.")
        for a in p.accounts:
            if a.owner not in self._people_names():
                problems.append(f"Account '{a.name}' has unknown owner '{a.owner}'.")
        if problems:
            wx.MessageBox("\n".join(problems), "Check the plan", wx.ICON_WARNING, self)
        return not any("unknown owner" in x or "at least one" in x or "unique" in x for x in problems)

    def _background(self, label, work, done, gauge=None):
        if self._busy:
            self.SetStatusText("Busy — wait for the current run to finish", 1)
            return
        self._flush_edits()
        if not self._validate():
            return
        self._busy = True
        self.SetStatusText(f"{label}…", 1)
        wx.BeginBusyCursor()
        plan = copy.deepcopy(self.plan)

        def progress(k, n):
            if gauge:
                wx.CallAfter(gauge.SetValue, int(100 * k / max(1, n)))

        def runner():
            try:
                res, err = work(plan, progress), None
            except Exception:  # noqa: BLE001 - report any engine failure
                res, err = None, traceback.format_exc()
            wx.CallAfter(finish, res, err)

        def finish(res, err):
            self._busy = False
            if wx.IsBusy():
                wx.EndBusyCursor()
            self.SetStatusText("", 1)
            if err:
                wx.MessageBox(err, f"{label} failed", wx.ICON_ERROR, self)
                return
            done(res)
            self.SetStatusText(f"{label}: done", 1)

        threading.Thread(target=runner, daemon=True).start()

    # ------------------------------------------------------------ strategy
    def _update_strategy_label(self, extra=""):
        text = optimize.strategy_label(optimize.current(self.plan)).replace("; ", "\n")
        self.strategy_label.SetLabel(text + (f"\n{extra}" if extra else ""))
        self.strategy_label.GetGrandParent().Layout()

    def _run_optimize(self):
        basis = "historical" if self.opt_basis.GetSelection() == 0 else "montecarlo"
        self.nb.SetSelection(self.proj_page)
        self.opt_gauge.SetValue(0)
        self._background("Strategy optimization", lambda p, prog: optimize.optimize(p, basis, prog),
                         self._optimize_done, self.opt_gauge)

    def _optimize_done(self, res):
        optimize.apply(self.plan, res["best"])
        self._mark_dirty()
        best, base = res["best_row"], res["baseline"]
        src = "historical start years" if res["basis"] == "historical" else "Monte Carlo runs"
        self._update_strategy_label(
            f"Success rate {best['success_rate'] * 100:.1f}% over {res['runs_per_strategy']} {src} "
            f"(was {base['success_rate'] * 100:.1f}%); {len(res['evaluated'])} strategies tested.")
        self.opt_grid.show([
            ("Success rate", lambda r: r["success_rate"] * 100, PCT),
            ("Median spending + ending after-tax wealth (today $)", "median_value", MONEY),
            ("Withdrawal order", lambda r: optimize.order_label(r["cfg"]["order"]), None),
            ("Surplus to", lambda r: r["cfg"]["surplus"], None),
            ("Roth conversions", lambda r: optimize.roth_label(r["cfg"]["roth"]), None),
            ("Spouse priority", lambda r: r["cfg"].get("priority") or "", None),
        ], res["evaluated"])
        self.proj_tabs.SetSelection(1)
        self._run_projection()

    # ------------------------------------------------------------ projection
    def _run_projection(self):
        self.nb.SetSelection(self.proj_page)
        self._background("Projection", lambda p, prog: simulate(p), self._projection_done)

    def _projection_done(self, res):
        self.last_result = res
        self._show_projection(open_external=True)

    def _show_projection_chart(self, open_external=False):
        res = self.last_result
        if not res:
            return
        real = self.proj_real.GetValue()
        fig = [charts.balances, charts.cashflow, charts.taxes, charts.healthcare][
            self.proj_chart_choice.GetSelection()](res, real)
        self.proj_chart.show(fig, open_external)

    def _show_projection(self, open_external=False):
        res = self.last_result
        if not res:
            return
        self._show_projection_chart(open_external)
        real = self.proj_real.GetValue()
        s = res.summary
        status = "✔ Plan succeeds" if res.success else f"✘ Runs out of money in {res.first_shortfall}"
        self.proj_summary.SetLabel(
            f"{status}.  Ending net worth (today's $): {s['end_net_worth_real']:,.0f}   "
            f"after-tax: {s['end_after_tax_real']:,.0f}   lifetime taxes (today's $): {s['lifetime_tax_real']:,.0f}   "
            f"IRMAA: {s['lifetime_irmaa_real']:,.0f}   ACA subsidies: {s['lifetime_aca_subsidy_real']:,.0f}   "
            f"Roth conversions: {s['lifetime_conversions_real']:,.0f}   RMDs: {s['lifetime_rmd_real']:,.0f}   "
            f"penalties: {s['lifetime_penalty_real']:,.0f}\n"
            f"Yale-rule discretionary spending (today's $): {s['lifetime_discretionary_real']:,.0f}")
        self.proj_summary.GetParent().Layout()
        d = (lambda key, r: r[key] / r["cpi"]) if real else (lambda key, r: r[key])
        de = (lambda key, r: r[key] / r["end_cpi"]) if real else (lambda key, r: r[key])
        cols = [("Year", "year", "{}")]
        for p in self.plan.people:
            cols.append((f"Age {p.name}", f"age_{p.name}", "{}"))
        for label, key in (("Wages", "wages"), ("Soc. Sec.", "ss_total"), ("Other income", "other_income"),
                           ("Expenses", "expenses"), ("ACA net", "aca_premium"), ("ACA subsidy", "aca_subsidy"),
                           ("Medicare", "medicare"), ("IRMAA", "irmaa"), ("Deposits", "deposits"),
                           ("RMD", "rmd"), ("Withdrawals", "withdrawals"), ("Yale rule", "rule_withdrawal"),
                           ("Discretionary", "discretionary"), ("Surplus saved", "surplus"),
                           ("Roth conv.", "conversion"),
                           ("Fed tax", "fed_tax"), ("State tax", "state_tax"), ("FICA", "fica"),
                           ("NIIT", "niit"), ("Penalty", "penalty"), ("MAGI", "magi")):
            cols.append((label, (lambda r, k=key: d(k, r)), MONEY))
        cols += [("Marginal", lambda r: r["marginal"] * 100, "{:.0f}%"),
                 ("IRMAA tier", "irmaa_tier", "{}"),
                 ("FPL %", lambda r: r["fpl_pct"] or None, "{:.0f}")]
        for label, key in (("Taxable", "bal_taxable"), ("Trad", "bal_trad"), ("Roth", "bal_roth"),
                           ("Ladder", "bal_ladder"), ("Net worth", "net_worth")):
            cols.append((label, (lambda r, k=key: de(k, r)), MONEY))
        cols.append(("Shortfall", lambda r: d("shortfall", r) or None, MONEY))
        self.proj_grid.show(cols, res.rows)

    # ------------------------------------------------------------ stress / MC
    def _run_stress(self):
        mode = self.st_mode.GetSelection()
        n, block, wrap = self.st_n.GetValue(), self.st_block.GetValue(), self.st_wrap.GetValue()
        if mode == 0:
            work = lambda p, prog: ("hist", analysis.historical(p, wrap, prog))
        elif mode == 1:
            work = lambda p, prog: ("mc", analysis.monte_carlo(p, n, block, None, prog))
        else:
            work = lambda p, prog: ("stress", analysis.stress(p, markets.STRESS_PERIODS))
        self.st_gauge.SetValue(0)
        self._background("Stress test", work, self._stress_done, self.st_gauge)

    def _stress_done(self, res):
        self.st_data = res
        self._show_stress(True)

    def _show_stress(self, open_external=False):
        if not self.st_data:
            return
        kind, data = self.st_data
        name = self.plan.name
        if kind == "stress":
            self.st_chart.show(charts.stress_lines(data, name), open_external)
            self.st_summary.SetLabel(f"{sum(r.success for r in data.values())} of {len(data)} stress periods succeed.")
            rows = [{"label": k, **r.summary} for k, r in data.items()]
            self.st_grid.show([("Period", "label", None), ("Succeeds", "success", None),
                               ("First shortfall", "first_shortfall", None),
                               ("End net worth (today $)", "end_net_worth_real", MONEY),
                               ("Lifetime tax (today $)", "lifetime_tax_real", MONEY)], rows)
            return
        allpaths = self.st_view.GetSelection() == 1
        if kind == "hist":
            fig = charts.spaghetti(data, name) if allpaths else charts.historical_bars(data, name)
            fails = [r for r in data["runs"] if not r["success"]]
            worst = min(data["runs"], key=lambda r: r["end_real"])
            self.st_summary.SetLabel(
                f"Success rate {data['success_rate'] * 100:.1f}% across {len(data['runs'])} start years"
                f"{' (wrapped)' if data['wrap'] else ''}.  Failing start years: "
                f"{', '.join(str(r['start_year']) for r in fails) or 'none'}.  Worst: {worst['start_year']}.")
            rows = data["runs"]
            self.st_grid.show([("Start year", "start_year", "{}"), ("Succeeds", "success", None),
                               ("First shortfall", "first_shortfall", None),
                               ("End net worth (today $)", "end_real", MONEY),
                               ("Lifetime tax (today $)", lambda r: r["summary"]["lifetime_tax_real"], MONEY)], rows)
        else:
            if allpaths:
                fake = {"runs": [dict(r, start_year=i) for i, r in enumerate(data["runs"][:200])],
                        "years": data["years"]}
                fig = charts.spaghetti(fake, name + " (first 200 simulations)")
            else:
                fig = charts.fan(data, name)
            ep = data["end_percentiles"]
            self.st_summary.SetLabel(
                f"Success rate {data['success_rate'] * 100:.1f}% of {len(data['runs'])} simulations.  "
                f"Ending net worth (today's $) — 10th: {ep[10]:,.0f}  median: {ep[50]:,.0f}  90th: {ep[90]:,.0f}")
            yrs = data["years"]
            rows = [{"year": y, **{p: data["bands"][p][t] for p in analysis.PERCENTILES}}
                    for t, y in enumerate(yrs[: len(data["bands"][50])])]
            self.st_grid.show([("Year", "year", "{}")] + [(f"{p}th pct", p, MONEY) for p in analysis.PERCENTILES],
                              rows)
        self.st_chart.show(fig, open_external)

    # ------------------------------------------------------------ Roth explorer
    def _run_roth(self):
        pr = m.ROTH_PRIORITY[self.rx_priority.GetSelection()]
        self._background("Roth explorer", lambda p, prog: analysis.roth_explorer(p, pr, prog), self._roth_done)

    def _roth_done(self, res):
        self.rx_data = res
        self._show_roth(True)

    def _show_roth(self, open_external=False):
        rx = self.rx_data
        if not rx:
            return
        fig = charts.roth_bars(rx) if self.rx_view.GetSelection() == 0 else charts.roth_schedule(rx)
        self.rx_chart.show(fig, open_external)
        base = next(v for v in rx["variants"] if v["label"] == "No conversions")["summary"]
        best = next(v for v in rx["variants"] if v["label"] == rx["best"])["summary"]
        self.rx_summary.SetLabel(
            f"Best: {rx['best']} — spending + ending after-tax wealth {best['lifetime_value_real']:,.0f} vs "
            f"{base['lifetime_value_real']:,.0f} with no conversions (today's $). Select a row and "
            f"'Apply' to copy that strategy to the plan.")
        cols = [("Strategy", "label", None),
                ("Succeeds", lambda v: v["summary"]["success"], None),
                ("Spending + end wealth (today $)", lambda v: v["summary"]["lifetime_value_real"], MONEY),
                ("End after-tax (today $)", lambda v: v["summary"]["end_after_tax_real"], MONEY),
                ("Lifetime income tax", lambda v: v["summary"]["lifetime_income_tax_real"], MONEY),
                ("Lifetime IRMAA", lambda v: v["summary"]["lifetime_irmaa_real"], MONEY),
                ("ACA subsidies", lambda v: v["summary"]["lifetime_aca_subsidy_real"], MONEY),
                ("Total converted", lambda v: v["summary"]["lifetime_conversions_real"], MONEY)]
        years = rx["years"]
        best_rows = next(v for v in rx["variants"] if v["label"] == rx["best"])["result"].rows
        for y, r in zip(years, best_rows):
            if r["conversion"] > 0:
                cols.append((f"{y} (best)", (lambda v, yy=y: next(
                    (rr["conversion"] / rr["cpi"] for rr in v["result"].rows if rr["year"] == yy), 0)), MONEY))
        self.rx_grid.show(cols, rx["variants"])

    def _apply_roth(self, _):
        if not self.rx_data:
            return
        r = self.rx_grid.selected_row
        label = self.rx_data["variants"][r]["label"] if r is not None else self.rx_data["best"]
        cfg = dict(next(c for lab, c in analysis.roth_variants(self.plan) if lab == label))
        for k, v in cfg.items():
            setattr(self.plan.roth, k, v)
        self.plan.roth.priority = m.ROTH_PRIORITY[self.rx_priority.GetSelection()]
        self._update_strategy_label()
        self._mark_dirty()
        self.SetStatusText(f"Applied Roth strategy: {label}", 0)

    # ------------------------------------------------------------ Social Security
    def _run_ss(self):
        self._background("Social Security optimizer", lambda p, prog: analysis.ss_optimizer(p, progress=prog),
                         self._ss_done)

    def _ss_done(self, res):
        self.ss_data = res
        self.ss_chart.show(charts.ss_heatmap(res), True)
        cur = tuple(int(p.ss_claim_age) for p in self.plan.people[:2])
        cur_g = next((g for g in res["grid"] if g["ages"] == cur), None)
        best = res["best"]
        msg = f"Best claiming ages: {', '.join(f'{n} {a}' for n, a in zip(res['people'], best['ages']))} — " \
              f"spending + ending after-tax wealth {best['value']:,.0f} (today's $)"
        if cur_g:
            msg += f"; current plan {cur}: {cur_g['value']:,.0f} (difference {best['value'] - cur_g['value']:,.0f})"
        self.ss_summary.SetLabel(msg)
        ranked = sorted(res["grid"], key=lambda g: -g["value"])
        self._ss_ranked = ranked
        cols = [(f"{n} age", (lambda g, i=i: g["ages"][i]), "{}") for i, n in enumerate(res["people"])]
        cols += [("Succeeds", lambda g: g["summary"]["success"], None),
                 ("Spending + end wealth (today $)", "value", MONEY),
                 ("Lifetime SS (today $)", lambda g: g["summary"]["lifetime_ss_real"], MONEY),
                 ("Lifetime tax (today $)", lambda g: g["summary"]["lifetime_tax_real"], MONEY)]
        self.ss_grid.show(cols, ranked)

    def _apply_ss(self, _):
        if not self.ss_data:
            return
        r = self.ss_grid.selected_row
        g = self._ss_ranked[r] if r is not None else self.ss_data["best"]
        for person, age in zip(self.plan.people, g["ages"]):
            person.ss_claim_age = float(age)
        self.people_tbl.refresh()
        self._mark_dirty()
        self.SetStatusText(f"Applied claiming ages {g['ages']}", 0)

    # ------------------------------------------------------------ compare
    def _run_compare(self):
        names = set(self.cmp_list.GetCheckedStrings())
        chosen = [copy.deepcopy(p) for p in self.plans if p.name in names]
        if not chosen:
            wx.MessageBox("Tick at least one plan.", "Compare", parent=self)
            return
        metric = list(charts.METRICS)[self.cmp_metric.GetSelection()]
        self._flush_edits()

        def work(_, prog):
            return [simulate(p) for p in chosen]

        def done(results):
            self.cmp_chart.show(charts.overlay(results, metric), True)
            self.cmp_summary.SetLabel("Deterministic projections with each plan's own assumptions.")
            self.cmp_grid.show([
                ("Plan", lambda r: r.plan_name, None), ("Succeeds", lambda r: r.success, None),
                ("First shortfall", lambda r: r.first_shortfall, None),
                ("End net worth (today $)", lambda r: r.summary["end_net_worth_real"], MONEY),
                ("End after-tax (today $)", lambda r: r.summary["end_after_tax_real"], MONEY),
                ("Discretionary (today $)", lambda r: r.summary["lifetime_discretionary_real"], MONEY),
                ("Lifetime tax (today $)", lambda r: r.summary["lifetime_tax_real"], MONEY),
                ("Lifetime IRMAA", lambda r: r.summary["lifetime_irmaa_real"], MONEY),
                ("ACA subsidies", lambda r: r.summary["lifetime_aca_subsidy_real"], MONEY),
                ("Roth converted", lambda r: r.summary["lifetime_conversions_real"], MONEY),
                ("Lifetime SS", lambda r: r.summary["lifetime_ss_real"], MONEY),
            ], results)

        self._background("Compare", work, done)
