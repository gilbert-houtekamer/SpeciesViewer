"""Fail if any module other than utils/raw_extensions.py hard-codes a set of RAW
extensions. Read-only; changes nothing.

This is the guardrail half of docs/plans/raw_extension_table.md. Consolidating 39
literal sets into one table is worth little on its own — without a check, set number 40
gets written next month and the drift starts again. The audit that found the original 39
is kept here so it can be re-run.

    python tools/svr_extension_audit.py          # report, exit 1 if anything is loose
    python tools/svr_extension_audit.py -v       # also list the exempt sets

An offender is a list/set/tuple literal of string constants containing two or more RAW
extensions. Two kinds of set are exempt, and each exemption states its reason — a set
that is genuinely a different question, not a copy of the table.
"""

import argparse
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCAN = ("speciesviewer", "tools")

# The table itself, and the audit you are reading.
CANONICAL = ("speciesviewer/utils/raw_extensions.py", "tools/svr_extension_audit.py")

# Sets that legitimately list extensions but are NOT a RAW-membership test. Each entry
# is (file, symbol, why). If you add one, the "why" is the point of the entry.
EXEMPT = {
    ("speciesviewer/engine/exifcache.py", "IMMUTABLE_EXTENSIONS"):
        "'EXIF that never changes' — correlates with RAW-ness but is a different question",
    ("speciesviewer/engine/projectlib.py", "findBestItem()"):
        "ordered preference (JPG before NEF), a ranking not a membership test",
}

RAW_EXTS = {"nef", "nrw", "cr2", "cr3", "crw", "arw", "sr2", "orf", "ori", "rw2",
            "raf", "dng", "pef", "srw", "x3f", "3fr", "mrw", "kdc", "dcr", "iiq", "rwl"}


def _norm(s):
    return s.lower().lstrip("*").lstrip(".")


def _literal(node):
    if not isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return None
    out = []
    for e in node.elts:
        if not (isinstance(e, ast.Constant) and isinstance(e.value, str)):
            return None
        out.append(e.value)
    return out or None


class _Finder(ast.NodeVisitor):
    def __init__(self, rel):
        self.rel = rel
        self.hits = []

    def _check(self, node, name):
        vals = _literal(node)
        if not vals:
            return
        raw = sorted({_norm(v) for v in vals} & RAW_EXTS)
        if len(raw) >= 2:
            self.hits.append((node.lineno, name, raw))

    def visit_Assign(self, node):
        name = None
        if node.targets and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        elif node.targets and isinstance(node.targets[0], ast.Attribute):
            name = node.targets[0].attr
        self._check(node.value, name)
        if isinstance(node.value, ast.Call) and node.value.args:
            self._check(node.value.args[0], name)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            n = node.target.id if isinstance(node.target, ast.Name) else None
            self._check(node.value, n)
        self.generic_visit(node)

    def visit_Call(self, node):
        fn = node.func
        label = (fn.attr + "()" if isinstance(fn, ast.Attribute)
                 else fn.id + "()" if isinstance(fn, ast.Name) else None)
        for a in node.args:
            self._check(a, label)
        self.generic_visit(node)

    def visit_Compare(self, node):
        for c in node.comparators:
            self._check(c, "in-test")
        self.generic_visit(node)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also list the exempt sets and their reasons")
    args = ap.parse_args()

    offenders, exempted, unparsed = [], [], []
    for top in SCAN:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, top)):
            dirnames[:] = [d for d in dirnames
                           if d not in {"__pycache__", ".git"} and not d.startswith(".venv")]
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, ROOT).replace("\\", "/")
                if rel in CANONICAL:
                    continue
                try:
                    tree = ast.parse(io.open(path, encoding="utf-8").read())
                except Exception as e:
                    unparsed.append((rel, str(e)[:70]))
                    continue
                f = _Finder(rel)
                f.visit(tree)
                for lineno, name, raw in f.hits:
                    row = (rel, lineno, name, raw)
                    if (rel, name) in EXEMPT:
                        exempted.append(row)
                    else:
                        offenders.append(row)

    if unparsed:
        print("could not parse:")
        for rel, err in unparsed:
            print(f"  {rel}: {err}")

    if args.verbose and exempted:
        print(f"exempt ({len(exempted)}):")
        for rel, lineno, name, raw in exempted:
            print(f"  {rel}:{lineno} {name} — {EXEMPT[(rel, name)]}")
        print()

    if not offenders:
        print("OK — no module outside utils/raw_extensions.py hard-codes RAW extensions.")
        return 0

    print(f"{len(offenders)} literal RAW-extension set(s) outside the canonical table:\n")
    for rel, lineno, name, raw in offenders:
        print(f"  {rel}:{lineno}  {name or '(unnamed)'}")
        print(f"      {' '.join(raw)}")
    print("\nImport from utils.raw_extensions instead. If the set is genuinely a")
    print("different question, add it to EXEMPT in this file WITH a reason.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
