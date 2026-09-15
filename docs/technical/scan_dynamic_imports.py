"""扫一遍算法工程：有没有"运行期才知道模块名"的动态导入。

判据：`importlib.import_module(...)` / `__import__(...)` / `_timed_import(...)` 的第一个
参数**不是字符串字面量**，就是可疑的（重构改名时会漏掉，且只在运行到那行才炸）。
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/mnt/d/code/mushroom/src")
CALLS = {"import_module", "__import__", "_timed_import"}

literal: list[tuple[pathlib.Path, int, str, str]] = []
dynamic: list[tuple[pathlib.Path, int, str, str]] = []

for path in sorted(ROOT.rglob("*.py")):
    if "__pycache__" in path.parts:
        continue
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        print(f"!! 解析失败 {path}: {e}")
        continue
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in CALLS or not node.args:
            continue
        first = node.args[0]
        src = ast.unparse(node)[:100]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            literal.append((path, node.lineno, first.value, src))
        else:
            dynamic.append((path, node.lineno, ast.unparse(first)[:60], src))

print(f"字面量动态导入：{len(literal)} 处")
for path, line, mod, src in literal:
    print(f"  {path.relative_to(ROOT)}:{line}  {mod}")
print()
print(f"**运行期才知道模块名：{len(dynamic)} 处**")
for path, line, arg, src in dynamic:
    print(f"  {path.relative_to(ROOT)}:{line}  参数= {arg}\n      {src}")
