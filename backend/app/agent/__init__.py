"""Agent 编排核心（plan/01 §三 两层执行模型、plan/10）。

骨架阶段为占位；核心循环（工具注册 + 调用修复 + 成本计量 + 预算守卫）于 step 3 落地：
- app/agent/registry.py   工具注册表
- app/agent/loop.py       工具调用循环（含调用修复）
- app/agent/cost.py       成本计量
- app/agent/budget.py     预算守卫
- app/agent/orchestrator.py  外层确定性编排器
"""
