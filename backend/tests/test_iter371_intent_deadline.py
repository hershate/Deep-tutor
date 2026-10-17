"""iter 371 R8：understand_intent deadline 校验顺序回归。

原 BUG：LLM 抽取值先做过期判定（过期 -> needs_clarify=True + 澄清问题），之后才被
args 表单权威值覆盖——覆盖后不重估：场景 A（表单合法日期但 raw_text 提到过去日期）
管线被虚假暂停追问「日期已过」；场景 B（表单填过期日期）又绕过过期策略直接采纳。
修复：过期判定挪到 args 覆盖后对**最终值**统一执行。
"""

from datetime import date, timedelta

from app.tools.base import ToolInput
from app.tools.understand_intent import _validate_intent

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def _inp(args=None):
    return ToolInput(tool="understand_intent", session_id="s", args=args or {}, upstream={})


def test_args_valid_deadline_overrides_stale_llm_extraction():
    # 场景 A：LLM 从叙述抽出过期日期，但表单给了合法日期 -> 不澄清、用表单值
    data = {
        "topic": "T",
        "deadline": YESTERDAY,
        "needs_clarify": False,
        "clarify_questions": [],
    }
    out = _validate_intent(data, _inp({"deadline": TOMORROW}))
    assert out["deadline"] == TOMORROW
    assert out["needs_clarify"] is False
    assert out["clarify_questions"] == []


def test_args_expired_deadline_goes_clarify():
    # 场景 B：表单填过期日期 -> 与 LLM 路径同口径：清空 + 转澄清
    data = {"topic": "T", "deadline": TOMORROW, "needs_clarify": False, "clarify_questions": []}
    out = _validate_intent(data, _inp({"deadline": YESTERDAY}))
    assert out["deadline"] is None
    assert out["needs_clarify"] is True
    assert any("已过" in q for q in out["clarify_questions"])


def test_llm_expired_deadline_still_clarifies_without_args():
    # 无表单覆盖时行为不变：LLM 抽取过期 -> 澄清
    data = {"topic": "T", "deadline": YESTERDAY, "needs_clarify": False, "clarify_questions": []}
    out = _validate_intent(data, _inp())
    assert out["deadline"] is None
    assert out["needs_clarify"] is True


def test_other_clarify_reasons_not_cleared_by_valid_deadline():
    # LLM 因其他原因 needs_clarify=True（如缺时长）——deadline 合法不应清除该标志
    data = {
        "topic": "T",
        "deadline": YESTERDAY,
        "needs_clarify": True,
        "clarify_questions": ["你每天能学多久？"],
    }
    out = _validate_intent(data, _inp({"deadline": TOMORROW}))
    assert out["deadline"] == TOMORROW
    assert out["needs_clarify"] is True
    assert "你每天能学多久？" in out["clarify_questions"]
    # deadline 触发的过期问题不应存在
    assert not any("已过" in q for q in out["clarify_questions"])


def test_deadline_normalized_to_date_only():
    # datetime 串规范化为纯日期（避免时间部分入库）
    data = {"topic": "T", "needs_clarify": False, "clarify_questions": []}
    out = _validate_intent(data, _inp({"deadline": f"{TOMORROW}T23:59:59"}))
    assert out["deadline"] == TOMORROW
