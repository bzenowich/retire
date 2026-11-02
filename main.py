#!/usr/bin/env python3
"""Retirement planner - wxPython desktop app with Plotly charts.

Usage: python main.py [workspace.json]
"""
import sys


def main():
    import wx
    from retire import model
    from retire.gui.main_window import MainFrame

    plans, path = None, None
    if len(sys.argv) > 1:
        path = sys.argv[1]
        plans = model.load_workspace(path)
    app = wx.App()
    frame = MainFrame(plans, path)
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
