#!/usr/bin/env python3
"""Find long QLabels that will make their window too wide.

    python tools/sv_dialog_width_audit.py        # exits 1 if any are found

A QLabel without word wrap can only draw its text as one line, so it answers
the layout's "how narrow can you be?" with the full width of that line. A
window's minimum is the widest answer among its rows, and a layout minimum
silently overrules resize() -- so one paragraph sets the width of a whole
dialog and no size request can take it back. "Empty a card" asked for 820 px
and opened at 3310.

The trap is the INLINE form, `addWidget(QLabel("long text"))`: no reference is
kept, so setWordWrap can never be called on it. Use `gui.widgets.wrapped()`
instead -- it is exactly as short and cannot be got wrong.

Static, so it needs no display and no Qt event loop; run it anywhere.
"""
import sys
import ast, pathlib

def longest_line(s):
    return max((len(p) for p in s.split("\n")), default=0)

def literal_of(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant))
    if isinstance(node, ast.BinOp):
        return literal_of(node.left) + literal_of(node.right)
    return ""

LIMIT = 90        # characters on one unbroken line
ROOT = pathlib.Path(__file__).resolve().parents[1] / "speciesviewer" / "gui"

found = 0
for f in sorted(ROOT.rglob("*.py")):
    try:
        tree = ast.parse(f.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    src = f.read_text(encoding="utf-8")
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "QLabel" and node.args):
            continue
        text = literal_of(node.args[0])
        if longest_line(text) <= LIMIT:
            continue
        # find the name it was assigned to, if any
        name = None
        for a in ast.walk(tree):
            if isinstance(a, ast.Assign) and a.value is node:
                t = a.targets[0]
                name = (t.attr if isinstance(t, ast.Attribute)
                        else t.id if isinstance(t, ast.Name) else None)
        wrapped = bool(name) and f"{name}.setWordWrap" in src
        if not wrapped:
            found += 1
            print(f"{longest_line(text):5d} chars  {f.name}:{node.lineno}  "
                  f"{'inline' if not name else 'as ' + name:14s}  {text[:45]}...")

if found:
    print(f"\n{found} label(s) will widen their window. "
          f"Use gui.widgets.wrapped() for each.")
else:
    print("No over-wide labels: every long QLabel wraps.")
sys.exit(1 if found else 0)
