"""Regenerate the README screenshots from the example plan.

    xvfb-run -a -s "-screen 0 1400x900x24" .venv/bin/python tools/screenshots.py
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "docs", "screenshots") + os.sep

def main():
    import wx
    from retire.gui.main_window import MainFrame
    from retire.gui import widgets
    import retire.gui.main_window as mw
    widgets.webbrowser.open = lambda *a, **k: None
    mw.wx.MessageBox = lambda *a, **k: (print("DIALOG:", a[:1]), wx.OK)[1]
    mw.wx.GetTextFromUser = lambda *a, **k: "Retire 2 years later"
    errors = []
    sys.excepthook = lambda *a: errors.append(''.join(traceback.format_exception(*a)))
    app = wx.App()
    f = MainFrame(); f.SetPosition((0, 0)); f.SetSize((1400, 900)); f.Show()
    def shot(name):
        def go():
            w, h = f.GetClientSize()
            dc = wx.ClientDC(f); bmp = wx.Bitmap(w, h); mdc = wx.MemoryDC(bmp)
            mdc.Blit(0, 0, w, h, dc, 0, 0); mdc.SelectObject(wx.NullBitmap)
            bmp.SaveFile(OUT + name + ".png", wx.BITMAP_TYPE_PNG)
        return go
    def tab(i): return lambda: f.nb.SetSelection(i)
    W = ("wait", lambda: None)
    def later_retire():
        for p in f.plan.people:
            d = p.retire; p.retirement_date = d.replace(year=d.year + 2).isoformat()
        f._select_plan(f.plans.index(f.plan))
    steps = [
        ("household", tab(0)), W, ("shot", shot("household")),
        ("accounts", tab(1)), W, ("shot", shot("accounts")),
        ("cashflow", tab(2)), W, ("shot", shot("cashflow")),
        ("projection", lambda: (tab(f.proj_page)(), f._run_optimize())),
        ("balances", lambda: (f.proj_chart_choice.SetSelection(0), f._show_projection_chart())), W, W,
        ("shot", shot("projection-balances")),
        ("cash chart", lambda: (f.proj_chart_choice.SetSelection(1), f.proj_tabs.SetSelection(1), f._show_projection_chart())), W, W,
        ("shot", shot("projection-cashflow")),
        ("taxes", lambda: (f.proj_chart_choice.SetSelection(2), f.proj_tabs.SetSelection(0), f._show_projection_chart())), W, W,
        ("shot", shot("projection-taxes")),
        ("mc", lambda: (tab(f.proj_page + 1)(), f.st_mode.SetSelection(1), f.st_n.SetValue(500), f._run_stress())), W, W,
        ("shot", shot("monte-carlo")),
        ("hist", lambda: (f.st_mode.SetSelection(0), f._run_stress())), W, W, ("shot", shot("historical")),
        ("roth", lambda: (tab(f.proj_page + 2)(), f._run_roth())), W, W, ("shot", shot("roth-explorer")),
        ("ss", lambda: (tab(f.proj_page + 3)(), f._run_ss())), W, W, ("shot", shot("social-security")),
        ("dup", lambda: (f._dup_plan(None), later_retire())),
        ("compare", lambda: (tab(f.proj_page + 4)(), f._run_compare())), W, W, ("shot", shot("compare")),
    ]
    def nxt():
        if f._busy:
            wx.CallLater(300, nxt); return
        if not steps:
            print("ERRORS:", errors or "none", flush=True); f.dirty = False; f.Close(); return
        name, fn = steps.pop(0)
        print("step", name, flush=True)
        try: fn()
        except Exception: errors.append(traceback.format_exc())
        wx.CallLater(900, nxt)
    wx.CallLater(1500, nxt)
    app.MainLoop()

if __name__ == "__main__":
    main()
