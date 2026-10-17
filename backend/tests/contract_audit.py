"""iter 374 R17 手工脚本（契约审计 Side A）：后端每条路由的真实返回形状提取。

提取口径：
- 路由枚举：lifespan 内 app.routes 里的 APIRoute（含插件启动期挂载的端点）。
- 返回形状：函数 AST 的 return 语句并集——
  * return {...}           -> 顶层键集；值逐键解析（见下）
  * return Helper(...)     -> 跟进同模块 helper（深度<=2）
  * return Model(...).model_dump() / response_model -> pydantic 字段集
  * Response/StreamingResponse/None -> 标记 kind，不比对
- 值形状：model_dump() -> model:字段集；ListComp[...model_dump()] -> list[model:...]；
  同模块 helper 调用 -> 跟进取其 dict 键；其余视为标量。

输出 backend_contract.json：{"METHOD /path": {"module", "func", "kind", "top": [...], "nested": {...}}}
供与前端 api.ts 解析结果（frontend_contract.py）做双向 diff。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

_MAX_FOLLOW = 2


def _module_tree(module) -> tuple[ast.Module, dict]:
    src = inspect.getsource(module)
    tree = ast.parse(src)
    defs = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = node
    return tree, defs


def _model_fields(module, name: str) -> list[str] | None:
    cls = getattr(module, name, None)
    if cls is None or not hasattr(cls, "model_fields"):
        return None
    try:
        return list(cls.model_fields.keys())
    except Exception:  # noqa: BLE001
        return None


def _called_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _value_shape(v: ast.expr, module, mod_defs: dict, depth: int) -> dict:
    """返回值表达式的形状描述。"""
    if isinstance(v, ast.Call):
        name = _called_name(v)
        if name == "model_dump":
            # X(...).model_dump() 或 helper(...).model_dump()（helper 返回模型实例）
            mname = None
            inner = v.func
            if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Call):
                mname = _called_name(inner.value)
            if mname:
                flds = _model_fields(module, mname)
                if flds is not None:
                    return {"model": mname, "fields": flds}
                if mname in mod_defs and isinstance(mod_defs[mname], (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fshape = _func_shape(mod_defs[mname], module, mod_defs, depth + 1)
                    if fshape.get("top"):
                        return {"helper": mname, **fshape}
            return {"model": mname or "?", "fields": None}
        # asyncio.to_thread(helper, ...) / anyio.to_thread.run_sync(helper, ...)
        if name in ("to_thread", "run_sync") and v.args:
            return _value_shape(v.args[0], module, mod_defs, depth)
        if name and depth < _MAX_FOLLOW:
            if name in mod_defs and isinstance(mod_defs[name], (ast.FunctionDef, ast.AsyncFunctionDef)):
                shape = _func_shape(mod_defs[name], module, mod_defs, depth + 1)
                if shape.get("kind") in ("dict", "model", "list"):
                    return {"helper": name, **{k: shape[k] for k in ("kind", "top", "fields") if k in shape}}
            # 同模块 helper 类（pydantic）
            flds = _model_fields(module, name) if name else None
            if flds is not None:
                return {"model": name, "fields": flds}
            # 跨模块 import 的序列化 helper（from x import _serialize）：按属性名回溯定义处
            target = getattr(module, name, None)
            if callable(target) and inspect.isfunction(target):
                try:
                    tmod = importlib.import_module(target.__module__)
                    _, tdefs = _module_tree(tmod)
                    tsrc = textwrap.dedent(inspect.getsource(target))
                    tdef = ast.parse(tsrc).body[0]
                    shape = _func_shape(tdef, tmod, tdefs, depth + 1)
                    if shape.get("kind") in ("dict", "model", "list"):
                        return {"helper": f"{target.__module__}.{name}", **{k: shape[k] for k in ("kind", "top", "fields") if k in shape}}
                except Exception:  # noqa: BLE001
                    pass
        return {"call": name or "?"}
    if isinstance(v, ast.ListComp):
        elt = v.elt
        if isinstance(elt, ast.Call):
            inner = _value_shape(elt, module, mod_defs, depth)
            if "model" in inner:
                return {"list_model": inner["model"], "fields": inner["fields"]}
        if isinstance(elt, ast.Dict):
            return {"list_dict": sorted({k.value for k in elt.keys if isinstance(k, ast.Constant)})}
        return {"list": True}
    if isinstance(v, ast.Dict):
        return {"dict": sorted({k.value for k in v.keys if isinstance(k, ast.Constant)})}
    return {"scalar": True}


_KIND_RANK = {"none": 0, "unknown": 1, "response": 2, "helper": 3, "list": 4, "list_model": 5, "response_model": 6, "model": 7, "dict": 8}


def _func_shape(fn_node, module, mod_defs: dict, depth: int = 0) -> dict:
    """函数返回形状：return 语句并集（dict 键 / model 字段 / list 元素 / 标记）。
    多分支混合返回时按 _KIND_RANK 取最具体（dict 最高——键集已并集，不丢信息）。"""
    top: set[str] = set()
    nested: dict[str, dict] = {}
    kind = "none"

    def raise_kind(new: str) -> None:
        nonlocal kind
        if _KIND_RANK[new] > _KIND_RANK[kind]:
            kind = new
    assigned_dicts: dict[str, ast.Dict] = {}

    def visit(node):
        nonlocal kind
        for child in ast.iter_child_nodes(node):
            visit(child)

    # 先收集 name = {...} 赋值（return name 形态）与 name = <shape> / name["k"]=v 增补
    assigned_dicts: dict[str, ast.Dict] = {}
    assigned_shapes: dict[str, dict] = {}
    added_keys: dict[str, set] = {}
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name):
                if isinstance(node.value, ast.Dict):
                    assigned_dicts[t.id] = node.value
                else:
                    assigned_shapes[t.id] = _value_shape(node.value, module, mod_defs, depth)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):  # x: dict = {...}
            if isinstance(node.value, ast.Dict):
                assigned_dicts[node.target.id] = node.value
            elif node.value is not None:
                assigned_shapes[node.target.id] = _value_shape(node.value, module, mod_defs, depth)
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].value, ast.Name)
            and isinstance(node.targets[0].slice, ast.Constant)
        ):
            nm = node.targets[0].value.id
            added_keys.setdefault(nm, set()).add(str(node.targets[0].slice.value))

    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Return):
            continue
        v = node.value
        while isinstance(v, ast.Await):  # return await helper(...)
            v = v.value
        if v is None:
            continue
        if isinstance(v, ast.Dict):
            raise_kind("dict")
            # ast.Dict.keys 与 values 按位对应（无 ** 解包时）
            for k, val in zip(v.keys, v.values):
                if isinstance(k, ast.Constant):
                    top.add(str(k.value))
                    nested[str(k.value)] = _value_shape(val, module, mod_defs, depth)
            continue
        if isinstance(v, ast.Name) and v.id in assigned_dicts:
            raise_kind("dict")
            d = assigned_dicts[v.id]
            for k, val in zip(d.keys, d.values):
                if isinstance(k, ast.Constant):
                    top.add(str(k.value))
                    nested[str(k.value)] = _value_shape(val, module, mod_defs, depth)
            top.update(added_keys.get(v.id, ()))
            continue
        if isinstance(v, ast.Name) and v.id in assigned_shapes:
            sh = assigned_shapes[v.id]
            if "model" in sh:
                raise_kind("model")
                top.update(sh["fields"] or [])
            elif "helper" in sh:
                raise_kind(sh.get("kind", "helper"))
                top.update(sh.get("top") or [])
            top.update(added_keys.get(v.id, ()))
            continue
        shape = _value_shape(v, module, mod_defs, depth)
        if "model" in shape:
            raise_kind("model")
            top.update(shape["fields"] or [])
        elif "list_model" in shape:
            raise_kind("list_model")
            top.update(shape["fields"] or [])
        elif "list_dict" in shape or "list" in shape:
            raise_kind("list")
            top.update(shape.get("list_dict") or [])
        elif "helper" in shape:
            raise_kind(shape.get("kind", "helper"))
            top.update(shape.get("top") or [])
            nested.update({k: v2 for k, v2 in shape.items() if k in ("kind", "fields")})
        elif shape.get("call") in ("JSONResponse", "Response", "StreamingResponse", "FileResponse", "RedirectResponse"):
            raise_kind("response")
        else:
            raise_kind("unknown")

    return {"kind": kind, "top": sorted(top), "nested": {k: v for k, v in nested.items() if v}}


def _walk_routes(routes) -> list[APIRoute]:
    """FastAPI 0.139 惰性 include_router：app.routes 是 _IncludedRouter 包装，
    真实 APIRoute 藏在 original_router.routes / effective_candidates 里（递归展开去重）。"""
    found: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            found.append(r)
        elif type(r).__name__ == "_IncludedRouter":
            orig = getattr(r, "original_router", None)
            if orig is not None:
                found += _walk_routes(list(orig.routes))
            try:
                found += _walk_routes(list(r.effective_candidates()))
            except TypeError:  # effective_candidates 是方法
                found += _walk_routes(list(r.effective_candidates()))
        elif hasattr(r, "routes"):
            found += _walk_routes(list(r.routes))
    return found


def main() -> None:
    out: dict[str, dict] = {}
    all_routes: list[APIRoute] = []
    with TestClient(app):  # lifespan 上下文（插件端点挂载）
        seen: set = set()
        for route in _walk_routes(app.routes):
            key0 = (tuple(sorted(route.methods)), route.path)
            if key0 in seen:
                continue
            seen.add(key0)
            all_routes.append(route)
        for route in all_routes:
            fn = route.endpoint
            path = route.path
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                key = f"{method} {path}"
                try:
                    module = importlib.import_module(fn.__module__)
                    _, mod_defs = _module_tree(module)
                    fn_src = textwrap.dedent(inspect.getsource(fn))
                    fn_node = ast.parse(fn_src)
                    fn_def = fn_node.body[0]
                    shape = _func_shape(fn_def, module, mod_defs)
                except Exception as e:  # noqa: BLE001
                    shape = {"kind": "error", "error": f"{type(e).__name__}: {e}", "top": []}
                out[key] = {
                    "module": getattr(fn, "__module__", "?"),
                    "func": getattr(fn, "__name__", "?"),
                    **shape,
                }
    # response_model 显式声明的路由以模型字段为准（AST 提不到 dict 键）
    for r in all_routes:
        if r.response_model is not None:
            try:
                flds = list(r.response_model.model_fields.keys())
            except Exception:  # noqa: BLE001
                continue
            for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
                k = f"{method} {r.path}"
                if k in out and out[k]["kind"] in ("unknown", "none", "error"):
                    out[k].update({"kind": "response_model", "top": flds})

    out_path = Path(__file__).parent / "backend_contract.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    kinds: dict[str, int] = {}
    for v in out.values():
        kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
    print(f"routes extracted: {len(out)}  kinds={kinds}")


if __name__ == "__main__":
    main()
