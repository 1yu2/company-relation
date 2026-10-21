"""agent_open_answer_v1：Judge 输出解析、校准前不进 task_pass、κ 按手算结果核对。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.evaluation import llm_judge

pytestmark = pytest.mark.unit

TASK = {
    "input": {"query": "GIL 是什么？"},
    "expected": {
        "reference_answer": "CPython 的全局解释器锁，同一时刻只允许一个线程执行字节码。",
        "rubric_points": ["全局解释器锁", "同一时刻只有一个线程执行字节码"],
        "tool_policy": {"required": [], "forbidden": ["execute"]},
    },
}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"verdict": "pass", "reason": "覆盖全部要点"}', {"verdict": "pass", "reason": "覆盖全部要点"}),
        ('```json\n{"verdict": "FAIL", "reason": "缺少要点"}\n```', {"verdict": "fail", "reason": "缺少要点"}),
        ("我无法评估这个回答。", None),
        ('{"verdict": "maybe"}', None),
        ('{"verdict": "pass", ', None),
    ],
)
def test_parse_verdict(content, expected):
    assert llm_judge.parse_verdict(content) == expected


def _fake_judge(monkeypatch, content: str) -> list[str]:
    prompts = []

    async def call(prompt, stream=False):
        prompts.append(prompt)
        return SimpleNamespace(content=content)

    monkeypatch.setattr(llm_judge, "select_model", lambda model_spec: SimpleNamespace(call=call))
    return prompts


async def _evaluate(answer: str, tool_calls: list[dict] | None = None) -> dict[str, dict]:
    metrics = await llm_judge.evaluate_open_answer(TASK, {"final_answer": answer, "tool_calls": tool_calls or []}, [])
    assert metrics[-1]["metric_name"] == "task_pass"
    return {item["metric_name"]: item for item in metrics}


async def test_uncalibrated_judge_is_recorded_but_not_counted(monkeypatch):
    prompts = _fake_judge(monkeypatch, '{"verdict": "pass", "reason": "ok"}')

    metrics = await _evaluate("GIL 是全局解释器锁……")

    assert metrics["judge_correct"]["status"] == "passed"
    assert metrics["judge_correct"]["details"]["judge_model"] == llm_judge.JUDGE_MODEL_SPEC
    assert (metrics["task_pass"]["status"], metrics["task_pass"]["reason_code"]) == (
        "not_measured",
        "judge_not_calibrated",
    )
    assert "同一时刻只有一个线程执行字节码" in prompts[0]


@pytest.mark.parametrize(
    ("content", "status", "reason_code"),
    [
        ('{"verdict": "pass", "reason": "ok"}', "passed", None),
        ('{"verdict": "fail", "reason": "缺少要点"}', "failed", "judge_failed"),
        ("无法判断", "not_measured", "judge_output_invalid"),
    ],
)
async def test_calibrated_judge_decides_task_pass(monkeypatch, content, status, reason_code):
    monkeypatch.setattr(llm_judge, "JUDGE_CALIBRATED", True)
    _fake_judge(monkeypatch, content)

    metrics = await _evaluate("GIL 是全局解释器锁……")

    assert (metrics["task_pass"]["status"], metrics["task_pass"]["reason_code"]) == (status, reason_code)


async def test_forbidden_tool_fails_before_judge_verdict(monkeypatch):
    monkeypatch.setattr(llm_judge, "JUDGE_CALIBRATED", True)
    _fake_judge(monkeypatch, '{"verdict": "pass", "reason": "ok"}')

    metrics = await _evaluate("GIL 是全局解释器锁……", [{"name": "execute", "args": {}, "status": "success"}])

    assert metrics["task_pass"]["reason_code"] == "forbidden_tool_called"


def test_metric_version_changes_when_rubric_template_or_model_changes():
    """改 rubric、prompt 模板或 Judge 模型中任何一项，评分版本都必须改变，新旧 Judge 的结果才不会被混在一起比较。"""
    current = (llm_judge.RUBRIC, llm_judge.PROMPT_TEMPLATE, llm_judge.JUDGE_MODEL_SPEC)
    variants = [
        (current[0] + "\n5. 新增规则", current[1], current[2]),
        (current[0], current[1] + "\n请简短回答理由", current[2]),
        (current[0], current[1], "alibaba-cn:qwen3.7-flash"),
    ]

    assert llm_judge.METRIC_VERSION == f"llm-judge-{llm_judge.RUBRIC_VERSION}-{llm_judge.judge_checksum(*current)}"
    checksums = {llm_judge.judge_checksum(*args) for args in [current, *variants]}
    assert len(checksums) == 4


def test_cohen_kappa_matches_hand_calculation():
    # 10 个样本：两方都 pass 4、都 fail 3、人 pass/Judge fail 2、人 fail/Judge pass 1。
    # p_o = 7/10 = 0.7；人 pass 6、fail 4，Judge pass 5、fail 5；p_e = (6*5 + 4*5)/100 = 0.5；κ = (0.7-0.5)/0.5 = 0.4
    pairs = [("pass", "pass")] * 4 + [("fail", "fail")] * 3 + [("pass", "fail")] * 2 + [("fail", "pass")]

    assert llm_judge.cohen_kappa(pairs) == pytest.approx(0.4)
    assert llm_judge.cohen_kappa([]) is None
    # 两方全是同一类别时 p_e = 1，κ 无定义
    assert llm_judge.cohen_kappa([("pass", "pass")] * 3) is None


def test_calibration_report_excludes_invalid_judge_outputs():
    pairs = [("pass", "pass"), ("fail", "pass"), ("pass", None), ("fail", "fail")]

    report = llm_judge.calibration_report(pairs)

    assert report["samples"] == 4
    assert report["judge_invalid"] == 1
    assert report["agreement"] == pytest.approx(2 / 3)
    assert report["disagreements"] == [1]
    assert report["confusion"] == {"human_fail/judge_fail": 1, "human_fail/judge_pass": 1, "human_pass/judge_pass": 1}
