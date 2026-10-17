"""iter 374 R17 手工脚本（契约审计 Side B）：前端 api.ts 的类型声明面提取。

- export interface X {...} -> 字段集（含 optional 标记）
- apiFetch<T>(`/url/${id}`) 或 apiFetch<T>('/a' + id + '/b') -> 调用点：URL 模板 + T
- T 解析：裸接口名 / 内联对象类型 / { k: Iface[] } 嵌套
- SSE（streamSse 等）/ 原始 fetch 下载类调用不在本口径（另行人工核）

输出 frontend_contract.json：calls=[{url, type, fields:{name: {optional, nested}}}]，
与 backend_contract.json 做双向 diff。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

API_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "api.ts"


def _balanced(s: str, start: int, open_ch: str, close_ch: str) -> tuple[int, int]:
    """返回从 s[start]=open_ch 开始的平衡区间（含两端）的 (start, end_exclusive)。
    计数含 <>（TS 泛型）；=> 的 > 在 api.ts 类型体中不构成失衡源（实测口径）。"""
    depth = 0
    i = start
    while i < len(s):
        c = s[i]
        if c in "([{<":
            depth += 1
        elif c in ")]}>":
            depth -= 1
            if depth == 0:
                return start, i + 1
        elif c in "'\"`":
            q = c
            i += 1
            while i < len(s) and s[i] != q:
                if s[i] == "\\":
                    i += 1
                i += 1
        i += 1
    return start, len(s)


def parse_interfaces(src: str) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for m in re.finditer(r"export interface (\w+)(?: extends (\w+))?\s*\{", src):
        name = m.group(1)
        _, end = _balanced(src, m.end() - 1, "{", "}")
        body = src[m.end() : end - 1]
        fields = parse_fields(body)
        if m.group(2):  # extends：并入基类字段（调用时基类已在前——接口按序定义）
            for k, v in out.get(m.group(2), {}).items():
                fields.setdefault(k, v)
        out[name] = fields
    return out


def parse_fields(body: str) -> dict[str, dict]:
    fields: dict[str, dict] = {}
    # 按顶层分号/换行切字段；类型内的 {} [] () <> 整段保护——保护段内部的
    # 分隔符替换为 \x00（split 不再切入），字段切完还原为 ';'
    i = 0
    buf = ""
    while i < len(body):
        c = body[i]
        if c in "([{<":
            _, en = _balanced(body, i, "(", ")")
            buf += body[i:en].replace(";", "\x00").replace("\n", "\x00")
            i = en
            continue
        if c in "'\"`":
            # _balanced 只用于括号（其返回依赖深度计数，引号不自增深度，从引号
            # 起调会吞掉余下全文）——字符串在此内联扫描到闭引号
            q = c
            j = i + 1
            while j < len(body) and body[j] != q:
                if body[j] == "\\":
                    j += 1
                j += 1
            j = min(j + 1, len(body))
            buf += body[i:j]
            i = j
            continue
        buf += c
        i += 1
    # 顶层切分后逐字段取 name/optional/type
    for chunk in re.split(r";|\n", buf):
        chunk = chunk.replace("\x00", ";").strip().rstrip(",").strip()
        if not chunk or chunk.startswith("//") or chunk.startswith("/*"):
            continue
        chunk = re.sub(r"//.*$", "", chunk).strip()
        m = re.match(r"(\w+)(\?)?:\s*(.+)$", chunk, re.S)
        if not m:
            continue
        fields[m.group(1)] = {"optional": bool(m.group(2)), "type": m.group(3).strip()}
    return fields


def _lexer_end(src: str, start: int) -> int:
    """从 start（'('）起做轻量词法扫描到配对 ')' 的下一个位置。
    处理：字符串/模板串（模板内 ${...} 表达式可再嵌模板/字符串）、嵌套括号。"""
    depth = 0
    stack: list[str] = []  # 'tpl'（模板串内）| 'expr'（模板 ${} 内）
    i = start
    while i < len(src):
        c = src[i]
        top = stack[-1] if stack else None
        if top == "tpl":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                stack.pop()
            elif c == "$" and i + 1 < len(src) and src[i + 1] == "{":
                stack.append("expr")
                i += 2
                continue
            i += 1
            continue
        if top == "expr":
            if c == "}":
                stack.pop()
            elif c == "`":
                stack.append("tpl")
            elif c in "'\"":
                q = c
                i += 1
                while i < len(src) and src[i] != q:
                    if src[i] == "\\":
                        i += 1
                    i += 1
            i += 1
            continue
        # 普通代码上下文
        if c == "`":
            stack.append("tpl")
        elif c in "'\"":
            q = c
            i += 1
            while i < len(src) and src[i] != q:
                if src[i] == "\\":
                    i += 1
                i += 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0 and c == ")":
                return i + 1
        i += 1
    return len(src)


def _tpl_normalize(lit: str) -> str:
    """模板字面量体 -> URL 模板：每个顶层 ${...}（可嵌套）替换为 {}。"""
    out = []
    i = 0
    while i < len(lit):
        c = lit[i]
        if c == "$" and i + 1 < len(lit) and lit[i + 1] == "{":
            depth = 0
            j = i + 1
            while j < len(lit):
                if lit[j] == "{":
                    depth += 1
                elif lit[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append("{}")
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _strip_query(url: str) -> str:
    return url.split("?", 1)[0]


def _first_arg_url(src: str, call_paren: int) -> str | None:
    """取 apiFetch( 第一个顶层实参并归一为 URL 模板（查询串剥离、${x}/拼接段 -> {}）。"""
    end = _lexer_end(src, call_paren)
    depth = 0
    arg_end = None
    i = call_paren + 1
    stack: list[str] = []
    while i < end:
        c = src[i]
        top = stack[-1] if stack else None
        if top == "tpl":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                stack.pop()
            elif c == "$" and src[i + 1 : i + 2] == "{":
                stack.append("expr")
                i += 2
                continue
            i += 1
            continue
        if top == "expr":
            if c == "}":
                stack.pop()
            elif c == "`":
                stack.append("tpl")
            i += 1
            continue
        if c == "`":
            stack.append("tpl")
        elif c in "'\"":
            q = c
            i += 1
            while i < end and src[i] != q:
                i += 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            arg_end = i
            break
        i += 1
    arg = src[call_paren + 1 : (arg_end if arg_end is not None else end - 1)].strip()
    if arg.startswith("`") and arg.endswith("`"):
        return _strip_query(_tpl_normalize(arg[1:-1]))
    if arg.startswith(("'", '"')) and arg.endswith(arg[0]) and len(arg) >= 2:
        return _strip_query(arg[1:-1])
    # 'a' + x + '/b' 拼接：字面量段保留，非字面量段 -> {}
    parts = re.findall(r"'([^']*)'|\"([^\"]*)\"|(\w+)", arg)
    url = ""
    for a, b, w in parts:
        url += (a or b) if (a or b) else "{}"
    return _strip_query(url) if url else None


def parse_calls(src: str) -> list[dict]:
    calls = []
    for m in re.finditer(r"apiFetch<", src):
        tstart = m.end() - 1
        # 泛型参数平衡 <>（内部可能含 {} [] 等复杂类型）
        depth = 0
        i = tstart
        type_end = None
        while i < len(src):
            c = src[i]
            if c in "([{":
                _, en = _balanced(src, i, "(", ")")
                i = en
                continue
            if c == "<":
                depth += 1
            elif c == ">":
                depth -= 1
                if depth == 0:
                    type_end = i
                    break
            elif c in "'\"`":
                _, en = _balanced(src, i, c, c)
                i = en
                continue
            i += 1
        if type_end is None:
            continue
        type_text = src[tstart + 1 : type_end].strip()
        # 紧随其后的 ( 取 URL
        rest = src[type_end + 1 :].lstrip()
        if not rest.startswith("("):
            continue
        call_paren = len(src) - len(rest)
        url = _first_arg_url(src, call_paren)
        _, call_end = _balanced(src, call_paren, "(", ")")
        mm = re.search(r"method:\s*['\"]([A-Z]+)['\"]", src[call_paren:call_end])
        calls.append({"url": url, "type": type_text, "method": mm.group(1) if mm else None})
    return calls


def _split_union(t: str) -> list[str]:
    """顶层按 | 切联合类型（括号/尖括号/字符串内的 | 不切）。"""
    parts, buf, depth = [], "", 0
    i = 0
    while i < len(t):
        c = t[i]
        if c in "([{<":
            depth += 1
        elif c in ")]}>":
            depth -= 1
        elif c in "'\"`":
            q = c
            buf += c
            i += 1
            while i < len(t) and t[i] != q:
                buf += t[i]
                i += 1
            buf += q if i < len(t) else ""
            i += 1
            continue
        if c == "|" and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += c
        i += 1
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def resolve_type(type_text: str, interfaces: dict) -> dict:
    """把类型文本解析为 {fields: {name: {optional, nested}}}; 无法解析则 {unresolved}。
    联合类型（按格式分支返回不同形状）：字段并集，optional=True（可能不在某分支）。"""
    branches = _split_union(type_text.strip())
    if len(branches) > 1:
        merged: dict[str, dict] = {}
        ok = True
        for b in branches:
            r = resolve_type(b, interfaces)
            if r.get("fields") is None:
                ok = False
                break
            for k, v in r["fields"].items():
                if k in merged:
                    merged[k]["optional"] = True
                else:
                    merged[k] = {**v, "optional": True}
        if ok:
            return {"fields": merged, "list": False}
    t = type_text.strip()
    m = re.match(r"^(\w+)(\[\])?$", t)
    if m and m.group(1) in interfaces:
        return {"fields": interfaces[m.group(1)], "list": bool(m.group(2))}
    if t.startswith("{") and t.endswith("}"):
        return {"fields": parse_fields(t[1:-1]), "list": False}
    if t == "void":
        return {"fields": {}, "list": False}
    return {"fields": None, "list": False, "unresolved": t}


def main() -> None:
    src = API_TS.read_text(encoding="utf-8")
    interfaces = parse_interfaces(src)
    raw_calls = parse_calls(src)
    calls = []
    for c in raw_calls:
        if c["url"] is None:
            continue
        resolved = resolve_type(c["type"], interfaces)
        # 嵌套接口引用：字段 type 形如 Iface / Iface[] -> nested 名
        nested = {}
        for fname, f in (resolved.get("fields") or {}).items():
            mm = re.match(r"^(\w+)(\[\])?$", f["type"])
            if mm and mm.group(1) in interfaces:
                nested[fname] = mm.group(1) + ("[]" if mm.group(2) else "")
        calls.append(
            {
                "url": c["url"],
                "method": c["method"],
                "type": c["type"][:80],
                "fields": sorted((resolved.get("fields") or {}).keys()),
                "optional": {k: v["optional"] for k, v in (resolved.get("fields") or {}).items()},
                "nested": nested,
                "unresolved": resolved.get("unresolved"),
            }
        )
    out = {"interfaces": {k: sorted(v.keys()) for k, v in interfaces.items()}, "calls": calls}
    (Path(__file__).parent / "frontend_contract.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"interfaces: {len(interfaces)}  calls: {len(calls)}")


if __name__ == "__main__":
    main()
