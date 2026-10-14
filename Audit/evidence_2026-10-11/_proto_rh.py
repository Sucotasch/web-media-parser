# -*- coding: utf-8 -*-
"""R-H data: file sizes + top-level seams (R-H-2) and .gitignore state (R-H-1)."""
import ast
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("### R-H-2: files >= 300 lines, with top-level seams")
rows = []
for p in sorted(pathlib.Path("src").rglob("*.py")):
    txt = p.read_text(encoding="utf-8")
    n = len(txt.splitlines())
    if n < 300:
        continue
    tree = ast.parse(txt)
    classes = [x.name for x in tree.body if isinstance(x, ast.ClassDef)]
    funcs = [x.name for x in tree.body if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))]
    methods = {c.name: len([y for y in c.body if isinstance(y, (ast.FunctionDef, ast.AsyncFunctionDef))])
               for c in tree.body if isinstance(c, ast.ClassDef)}
    rows.append((n, str(p).replace("\\", "/"), classes, len(funcs), methods))
for n, p, classes, nf, methods in sorted(rows, reverse=True):
    print(f"  {n:5} lines  {p}")
    print(f"        top-level classes={classes} funcs={nf}")
    if methods:
        print(f"        methods per class={methods}")

print("\n### R-H-1: .gitignore vs untracked paths")
gi = pathlib.Path(".gitignore").read_text(encoding="utf-8", errors="replace")
lines = [l.strip() for l in gi.splitlines()]
for cand in [".ext-dev-loop", ".unlazy", ".agents/", "Errors2.txt", "Errors.txt", "downloads/"]:
    print(f"  {cand:16} present={cand in lines}  (line {lines.index(cand) + 1 if cand in lines else '-'})")
out = subprocess.run(["git", "status", "--short"], capture_output=True, text=True).stdout
print("  untracked now:", [l[3:].strip() for l in out.splitlines() if l.startswith("??")])
tracked_agents = subprocess.run(["git", "ls-files", ".agents"], capture_output=True, text=True).stdout
print(f"  tracked files under .agents/: {len(tracked_agents.splitlines())}")
for l in tracked_agents.splitlines()[:5]:
    print("   ", l)
