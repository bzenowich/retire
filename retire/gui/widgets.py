"""Reusable wx widgets: dataclass-bound grid editor, form, chart view, result grid."""
import copy
import os
import tempfile
import webbrowser

import wx
import wx.grid as gridlib

try:
    import wx.html2 as webview
    HAVE_WEBVIEW = True
except ImportError:          # Debian/Ubuntu: apt install python3-wxgtk-webview4.0
    webview = None
    HAVE_WEBVIEW = False

CHART_DIR = os.path.join(tempfile.gettempdir(), "retire-charts")


class Col:
    """Column/field spec. kind: text | float | int | choice | bool."""

    def __init__(self, attr, label, kind="text", choices=None, width=100, tip=None):
        self.attr, self.label, self.kind = attr, label, kind
        self.choices, self.width, self.tip = choices, width, tip

    def choice_list(self):
        c = self.choices
        return list(c() if callable(c) else (c or []))


def _to_str(v, kind):
    if kind == "bool":
        return "1" if v else ""
    if kind == "float":
        return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, (int, float)) else str(v or "")
    return "" if v is None else str(v)


def _from_str(s, kind, old):
    s = (s or "").strip()
    if kind == "bool":
        return s in ("1", "true", "True", "yes")
    if kind == "float":
        try:
            return float(s.replace(",", "").replace("$", "").replace("%", "")) if s else 0.0
        except ValueError:
            return old
    if kind == "int":
        try:
            return int(float(s.replace(",", ""))) if s else 0
        except ValueError:
            return old
    return s


class TableEditor(wx.Panel):
    """Editable grid bound to a list of dataclass instances."""

    def __init__(self, parent, title, cols, factory, on_change=None, help_text=None):
        super().__init__(parent)
        self.cols, self.factory, self.on_change = cols, factory, on_change
        self.items, self.source, self.pred = [], [], None
        box = wx.StaticBoxSizer(wx.VERTICAL, self, title)
        if help_text:
            t = wx.StaticText(box.GetStaticBox(), label=help_text)
            t.SetForegroundColour(wx.Colour(82, 81, 78))
            box.Add(t, 0, wx.ALL, 4)
        self.grid = gridlib.Grid(box.GetStaticBox())
        self.grid.CreateGrid(0, len(cols))
        self.grid.SetRowLabelSize(32)
        for i, c in enumerate(cols):
            self.grid.SetColLabelValue(i, c.label)
            self.grid.SetColSize(i, c.width)
        self.grid.SetColLabelSize(28)
        self.grid.Bind(gridlib.EVT_GRID_CELL_CHANGED, self._changed)
        box.Add(self.grid, 1, wx.EXPAND | wx.ALL, 4)
        bar = wx.BoxSizer(wx.HORIZONTAL)
        for label, fn in (("Add", self._add), ("Duplicate", self._dup), ("Delete", self._del),
                          ("Move up", lambda e: self._move(-1)), ("Move down", lambda e: self._move(1))):
            b = wx.Button(box.GetStaticBox(), label=label)
            b.Bind(wx.EVT_BUTTON, fn)
            bar.Add(b, 0, wx.RIGHT, 4)
        box.Add(bar, 0, wx.ALL, 4)
        self.SetSizer(box)

    def bind(self, source, pred=None):
        """Show the items of `source` matching `pred`; edits write through to `source`."""
        self.source, self.pred = source, pred
        self.refresh()

    def _view(self):
        return [x for x in self.source if self.pred is None or self.pred(x)]

    def refresh(self):
        self.items = self._view()
        g = self.grid
        g.Freeze()
        if g.GetNumberRows():
            g.DeleteRows(0, g.GetNumberRows())
        g.AppendRows(len(self.items))
        for r, it in enumerate(self.items):
            for c, col in enumerate(self.cols):
                g.SetCellValue(r, c, _to_str(getattr(it, col.attr), col.kind))
                if col.kind == "choice":
                    g.SetCellEditor(r, c, gridlib.GridCellChoiceEditor(col.choice_list(), allowOthers=False))
                elif col.kind == "bool":
                    g.SetCellEditor(r, c, gridlib.GridCellBoolEditor())
                    g.SetCellRenderer(r, c, gridlib.GridCellBoolRenderer())
                elif col.kind in ("float", "int"):
                    g.SetCellAlignment(r, c, wx.ALIGN_RIGHT, wx.ALIGN_CENTER)
        g.Thaw()

    def _changed(self, evt):
        r, c = evt.GetRow(), evt.GetCol()
        it, col = self.items[r], self.cols[c]
        val = _from_str(self.grid.GetCellValue(r, c), col.kind, getattr(it, col.attr))
        setattr(it, col.attr, val)
        self.grid.SetCellValue(r, c, _to_str(val, col.kind))
        self._notify()

    def _notify(self):
        if self.on_change:
            self.on_change()

    def _selected(self):
        rows = self.grid.GetSelectedRows()
        if rows:
            return rows[0]
        r = self.grid.GetGridCursorRow()
        return r if 0 <= r < len(self.items) else None

    def _src_index(self, item):
        return next(i for i, x in enumerate(self.source) if x is item)

    def _add(self, _):
        self.grid.SaveEditControlValue()
        self.source.append(self.factory())
        self.refresh()
        self.grid.SetGridCursor(len(self.items) - 1, 0)
        self._notify()

    def _dup(self, _):
        r = self._selected()
        if r is None:
            return
        self.source.insert(self._src_index(self.items[r]) + 1, copy.deepcopy(self.items[r]))
        self.refresh()
        self._notify()

    def _del(self, _):
        r = self._selected()
        if r is None:
            return
        del self.source[self._src_index(self.items[r])]
        self.refresh()
        self._notify()

    def _move(self, d):
        r = self._selected()
        if r is None or not 0 <= r + d < len(self.items):
            return
        i, j = self._src_index(self.items[r]), self._src_index(self.items[r + d])
        self.source[i], self.source[j] = self.source[j], self.source[i]
        self.refresh()
        self.grid.SetGridCursor(r + d, 0)
        self._notify()


class Form(wx.Panel):
    """Label/control form bound to the attributes of one object."""

    def __init__(self, parent, title, fields, on_change=None, columns=2):
        super().__init__(parent)
        self.fields, self.on_change, self.obj = fields, on_change, None
        self.ctrls = {}
        box = wx.StaticBoxSizer(wx.VERTICAL, self, title)
        sb = box.GetStaticBox()
        fg = wx.FlexGridSizer(cols=columns * 2, vgap=4, hgap=8)
        for f in fields:
            lab = wx.StaticText(sb, label=f.label)
            if f.kind == "choice":
                ctrl = wx.Choice(sb, choices=f.choice_list())
                ctrl.Bind(wx.EVT_CHOICE, lambda e, f=f: self._commit(f))
            elif f.kind == "bool":
                ctrl = wx.CheckBox(sb)
                ctrl.Bind(wx.EVT_CHECKBOX, lambda e, f=f: self._commit(f))
            else:
                ctrl = wx.TextCtrl(sb, size=(f.width, -1), style=wx.TE_PROCESS_ENTER)
                ctrl.Bind(wx.EVT_KILL_FOCUS, lambda e, f=f: (self._commit(f), e.Skip()))
                ctrl.Bind(wx.EVT_TEXT_ENTER, lambda e, f=f: self._commit(f))
            if f.tip:
                ctrl.SetToolTip(f.tip)
                lab.SetToolTip(f.tip)
            self.ctrls[f.attr] = ctrl
            fg.Add(lab, 0, wx.ALIGN_CENTER_VERTICAL)
            fg.Add(ctrl, 0, wx.EXPAND)
        box.Add(fg, 0, wx.ALL, 6)
        self.SetSizer(box)

    def bind(self, obj):
        self.obj = obj
        self.refresh()

    def refresh(self):
        if self.obj is None:
            return
        for f in self.fields:
            ctrl, v = self.ctrls[f.attr], getattr(self.obj, f.attr)
            if f.kind == "choice":
                opts = f.choice_list()
                keys = list(f.choices.keys()) if isinstance(f.choices, dict) else opts
                ctrl.Set([f.choices[k] for k in keys] if isinstance(f.choices, dict) else opts)
                ctrl.SetSelection(keys.index(v) if v in keys else wx.NOT_FOUND)
            elif f.kind == "bool":
                ctrl.SetValue(bool(v))
            else:
                ctrl.ChangeValue(_to_str(v, f.kind))

    def _commit(self, f):
        if self.obj is None:
            return
        ctrl = self.ctrls[f.attr]
        if f.kind == "choice":
            keys = list(f.choices.keys()) if isinstance(f.choices, dict) else f.choice_list()
            i = ctrl.GetSelection()
            val = keys[i] if 0 <= i < len(keys) else getattr(self.obj, f.attr)
        elif f.kind == "bool":
            val = ctrl.GetValue()
        else:
            val = _from_str(ctrl.GetValue(), f.kind, getattr(self.obj, f.attr))
            ctrl.ChangeValue(_to_str(val, f.kind))
        if val != getattr(self.obj, f.attr):
            setattr(self.obj, f.attr, val)
            if self.on_change:
                self.on_change()


class ChartView(wx.Panel):
    """Shows a Plotly figure: embedded WebView if available, else in the browser."""
    _counter = 0

    def __init__(self, parent):
        super().__init__(parent)
        self.path = None
        s = wx.BoxSizer(wx.VERTICAL)
        bar = wx.BoxSizer(wx.HORIZONTAL)
        self.open_btn = wx.Button(self, label="Open chart in browser")
        self.open_btn.Bind(wx.EVT_BUTTON, lambda e: self.open_external())
        self.open_btn.Disable()
        bar.Add(self.open_btn, 0, wx.RIGHT, 6)
        if HAVE_WEBVIEW:
            self.web = webview.WebView.New(self)
            body = self.web
        else:
            self.web = None
            body = wx.StaticText(self, label=(
                "Embedded charts need wxPython's WebView module, which is not installed.\n"
                "On Debian/Ubuntu/elementary:  sudo apt install python3-wxgtk-webview4.0\n\n"
                "Charts are still generated - use 'Open chart in browser'."))
        s.Add(bar, 0, wx.ALL, 4)
        s.Add(body, 1, wx.EXPAND)
        self.SetSizer(s)
        self.auto_open = not HAVE_WEBVIEW

    def show(self, fig, open_external=False):
        from .. import charts
        os.makedirs(CHART_DIR, exist_ok=True)
        ChartView._counter += 1
        self.path = os.path.join(CHART_DIR, f"chart_{os.getpid()}_{ChartView._counter}.html")
        with open(self.path, "w") as f:
            f.write(charts.to_html(fig))
        self.open_btn.Enable()
        if self.web:
            self.web.LoadURL("file://" + self.path)
        elif open_external:
            self.open_external()

    def open_external(self):
        if self.path:
            webbrowser.open("file://" + self.path)


class ResultGrid(gridlib.Grid):
    """Read-only table of row dicts."""

    def __init__(self, parent):
        super().__init__(parent)
        self.CreateGrid(0, 0)
        self.EnableEditing(False)
        self.SetRowLabelSize(0)
        self.columns, self.rows = [], []
        self.selected_row = None
        self.Bind(gridlib.EVT_GRID_SELECT_CELL, self._on_select)

    def _on_select(self, evt):
        self.selected_row = evt.GetRow()
        evt.Skip()

    def show(self, columns, rows):
        """columns: list of (label, key or callable, fmt)."""
        self.columns, self.rows = columns, rows
        self.selected_row = None
        self.Freeze()
        if self.GetNumberRows():
            self.DeleteRows(0, self.GetNumberRows())
        if self.GetNumberCols():
            self.DeleteCols(0, self.GetNumberCols())
        self.AppendCols(len(columns))
        self.AppendRows(len(rows))
        for c, (label, _, _) in enumerate(columns):
            self.SetColLabelValue(c, label)
        for r, row in enumerate(rows):
            for c, (_, key, fmt) in enumerate(columns):
                v = key(row) if callable(key) else row.get(key)
                self.SetCellValue(r, c, "" if v is None else (fmt.format(v) if fmt else str(v)))
                if fmt and "," in fmt:
                    self.SetCellAlignment(r, c, wx.ALIGN_RIGHT, wx.ALIGN_CENTER)
        self.AutoSizeColumns(False)
        self.Thaw()
        self.selected_row = None

    def to_csv(self, path):
        import csv
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in self.columns])
            for r in range(self.GetNumberRows()):
                w.writerow([self.GetCellValue(r, c).replace(",", "") for c in range(self.GetNumberCols())])
