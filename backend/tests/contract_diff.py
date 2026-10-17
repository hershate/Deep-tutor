"""iter 374 R17 手工脚本（契约审计 Side C）：backend_contract.json × frontend_contract.json 双向 diff。

- 幽灵端点：前端调用在后端路由表中不存在（打点必 404/405）
- 幽灵字段：前端类型声明了后端从不返回的顶层/嵌套字段（运行时 undefined）
- 遗漏字段：后端返回但前端类型未收（信息不可见——分派后决定是否补）
输出人工分派清单 contract_diff_report.txt。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).parent
be = json.loads((HERE / "backend_contract.json").read_text(encoding="utf-8"))
fe = json.loads((HERE / "frontend_contract.json").read_text(encoding="utf-8"))


def norm_path(p: str) -> str:
    p = re.sub(r"\{[^}]*\}", "{}", p)
    if p.startswith("/api"):
        p = p[4:]
    return p


# 后端索引：method + path；path 兜底
be_by_mp: dict[tuple[str, str], dict] = {}
be_by_p: dict[str, list[dict]] = {}
for k, v in be.items():
    method, path = k.split(" ", 1)
    np_ = norm_path(path)
    be_by_mp[(method, np_)] = {**v, "key": k}
    be_by_p.setdefault(np_, []).append({**v, "key": k})

SKIP_KINDS = {"response", "none", "unknown"}

lines: list[str] = []
phantom_n = missing_n = 0

for c in fe["calls"]:
    url = c["url"]
    if not url or url in ("{}", "path") or c["type"] == "T":
        continue  # apiFetch 定义本身 / 非调用
    norm = norm_path(url)
    # 条件查询串模板（`/x${q ? '?a=1' : ''}`）归一后带尾部 {}：按序剥到命中为止
    cands_norm = [norm]
    while cands_norm[-1].endswith("{}"):
        cands_norm.append(cands_norm[-1][: -2])
    hit = None
    if c.get("method"):
        for n_ in cands_norm:
            hit = be_by_mp.get((c["method"], n_))
            if hit:
                break
    if hit is None:
        for n_ in cands_norm:
            cands = be_by_p.get(n_, [])
            gets = [x for x in cands if x["key"].split(" ")[0] == "GET"]
            if gets:  # 无 method 标注（默认 GET）且该 path 存在 GET
                hit = gets[0]
                break
            if len(cands) == 1:
                hit = cands[0]
                break
    if hit is None:
        lines.append(f"[幽灵端点] {c.get('method') or 'GET'} {url}  (type={c['type'][:60]})")
        phantom_n += 1
        continue
    if hit["kind"] in SKIP_KINDS or hit["kind"] == "error":
        continue
    be_top = set(hit.get("top") or [])
    fe_fields = set(c["fields"] or [])
    phantom = sorted(fe_fields - be_top)
    missing = sorted(be_top - fe_fields)
    entry = f"[{hit['key']}] fe_type={c['type'][:60]}"
    if phantom:
        entry += f"\n    幽灵字段: {phantom}"
    if missing:
        entry += f"\n    遗漏字段: {missing}"
    # 嵌套比对
    for fname, nested_name in (c.get("nested") or {}).items():
        base = nested_name.rstrip("[]")
        is_list = nested_name.endswith("[]")
        fe_nested = set(fe["interfaces"].get(base, []))
        be_n = (hit.get("nested") or {}).get(fname) or {}
        if isinstance(be_n, dict):
            be_keys: set[str] = set()
            if is_list and be_n.get("list_dict"):
                be_keys = set(be_n["list_dict"])
            elif not is_list and be_n.get("dict"):
                be_keys = set(be_n["dict"])
            elif be_n.get("fields"):
                be_keys = set(be_n["fields"])
            if be_keys:
                p2 = sorted(fe_nested - be_keys)
                m2 = sorted(be_keys - fe_nested)
                if p2:
                    entry += f"\n    嵌套[{fname}:{base}] 幽灵: {p2}"
                if m2:
                    entry += f"\n    嵌套[{fname}:{base}] 遗漏: {m2}"
    if phantom or missing or "[fname" in entry:
        lines.append(entry)
        phantom_n += len([ln for ln in entry.splitlines() if "幽灵" in ln])
        missing_n += len([ln for ln in entry.splitlines() if "遗漏" in ln])

report = "\n".join(lines)
(HERE / "contract_diff_report.txt").write_text(report, encoding="utf-8")
print(f"diff 完成：幽灵条目行 {phantom_n}、遗漏条目行 {missing_n}，详见 contract_diff_report.txt")
print("报告行数:", len(lines))
