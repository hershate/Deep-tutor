"""iter 375 R18 手工脚本：全路由授权矩阵提取。

对 lifespan 内递归展开的每条 APIRoute：
- auth 级别：遍历 route.dependant.dependencies（含 router 级继承）匹配
  require_admin / require_auth / get_current_user(_id) 的可调用对象——
  PUBLIC（无任何认证依赖）/ USER / ADMIN。
- 所有权证据：handler 源码静态特征（user.id / get_owned_ / resolve_owner /
  admin.id），缺失者列入人工分派清单。
- 汇率限流在册（limiter.check 出现）作附带信息。

输出 auth_matrix.json + 控制台分级汇总；PUBLIC/无证据 USER 项逐条人工核。
"""

from __future__ import annotations

import inspect
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import deps as deps_mod  # noqa: E402
from app.main import app  # noqa: E402

_AUTH_FNS = {
    id(deps_mod.require_admin): "ADMIN",
    id(deps_mod.require_auth): "USER",
    id(deps_mod.get_current_user): "USER",
    id(deps_mod.get_current_user_id): "USER",
}


def _walk_routes(routes) -> list[APIRoute]:
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
            except TypeError:
                found += _walk_routes(list(r.effective_candidates()))
        elif hasattr(r, "routes"):
            found += _walk_routes(list(r.routes))
    return found


def _route_auth(route: APIRoute) -> str:
    levels: set[str] = set()
    for dep in route.dependant.dependencies:
        lvl = _AUTH_FNS.get(id(dep.call))
        if lvl:
            levels.add(lvl)
    # 主体参数里的 Depends(get_current_user)（dependant 自身的 call_params）
    try:
        for param in route.dependant.call_params.values():
            d = getattr(param.default, "dependency", None)
            if d is not None and id(d) in _AUTH_FNS:
                levels.add(_AUTH_FNS[id(d)])
    except AttributeError:
        pass
    if "ADMIN" in levels:
        return "ADMIN"
    if "USER" in levels:
        return "USER"
    return "PUBLIC"


def main() -> None:
    out: dict[str, dict] = {}
    with TestClient(app):
        seen: set = set()
        routes = []
        for r in _walk_routes(app.routes):
            key0 = (tuple(sorted(r.methods)), r.path)
            if key0 in seen:
                continue
            seen.add(key0)
            routes.append(r)
        for route in routes:
            fn = route.endpoint
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                key = f"{method} {route.path}"
                try:
                    src = textwrap.dedent(inspect.getsource(fn))
                except Exception:  # noqa: BLE001
                    src = ""
                ownership = bool(
                    "user.id" in src
                    or "user_" in src
                    or "get_owned_" in src
                    or "resolve_owner" in src
                    or "admin.id" in src
                )
                out[key] = {
                    "module": getattr(fn, "__module__", "?"),
                    "func": getattr(fn, "__name__", "?"),
                    "auth": _route_auth(route),
                    "ownership_evidence": ownership,
                    "rate_limited": "limiter.check" in src,
                }
    (Path(__file__).parent / "auth_matrix.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    by: dict[str, int] = {}
    for v in out.values():
        by[v["auth"]] = by.get(v["auth"], 0) + 1
    print(f"routes: {len(out)}  auth 分布: {by}")
    print("--- PUBLIC：")
    for k, v in out.items():
        if v["auth"] == "PUBLIC":
            print(f"  {k}  ({v['module'].split('.')[-1]}.{v['func']})")
    print("--- 无所有权证据的 USER 路由（人工分派）：")
    for k, v in out.items():
        if v["auth"] == "USER" and not v["ownership_evidence"]:
            print(f"  {k}  ({v['module'].split('.')[-1]}.{v['func']})  rate_limited={v['rate_limited']}")
    print("--- ADMIN 路由：")
    for k, v in out.items():
        if v["auth"] == "ADMIN":
            print(f"  {k}  ({v['module'].split('.')[-1]}.{v['func']})")


if __name__ == "__main__":
    main()
