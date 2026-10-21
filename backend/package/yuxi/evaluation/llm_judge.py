"""`agent_open_answer_v1` 协议：开放式回答由版本化 rubric + LLM Judge 判定，人工校准达标前不进入 task_pass。

版本：评分版本号包含 rubric 文本和 Judge 模型的校验和，改任何一项版本号都会变，
实验对比会因为“评分口径不同”被拒绝，不会把新旧 Judge 的结果混在一起比较。
校准：用人工标注的校准集计算 Judge 与人工的一致率和 Cohen's κ（calibration_report）。
JUDGE_CALIBRATED 为 False 时，judge_correct 照常记录并展示，但 task_pass 记为 not_measured（judge_not_calibrated）；
是否达标、阈值多少由人决定，确认后再把开关改为 True。
Judge 输出无法解析（格式错误、拒答）时记为 not_measured，不默认判为通过或失败；调用模型失败或超过 JUDGE_TIMEOUT_SECONDS
直接抛出，按评测侧错误（runner_error）处理，不评分，也不计入 Agent 失败。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from typing import Any

from yuxi.evaluation.agent_final_answer import evaluate_agent_final_answer
from yuxi.models import select_model

RUBRIC_VERSION = "open-answer-v1"
JUDGE_MODEL_SPEC = "deepseek:deepseek-v4-flash"
JUDGE_CALIBRATED = False
# 实验的 timeout_seconds 只约束被测 Agent；评分阶段单独限时，避免慢 Judge 长时间占住并发槽和数据库连接。
JUDGE_TIMEOUT_SECONDS = 60
RUBRIC = """你是一名严格的评测员。请根据参考答案和评分要点，判断回答是否正确回答了问题。
判定规则：
1. 回答必须覆盖全部评分要点；缺少任何一个要点即为不通过。
2. 回答中出现与参考答案或要点矛盾的事实，即为不通过。
3. 只看事实是否正确完整，不看措辞、格式和长度。
4. 回答说“不知道”或拒绝回答，即为不通过。"""
PROMPT_TEMPLATE = """{rubric}

问题：
{question}

参考答案：
{reference}

评分要点：
{points}

待评回答：
{answer}

只输出一个 JSON 对象，不要输出其他文字：{{"verdict": "pass" 或 "fail", "reason": "一句话理由"}}"""


def judge_checksum(rubric: str, template: str, model_spec: str) -> str:
    """rubric、prompt 模板、Judge 模型三者共同决定评分口径，任何一项变化都会改变校验和。"""
    return hashlib.sha256(f"{rubric}\n{template}\n{model_spec}".encode()).hexdigest()[:8]


_CHECKSUM = judge_checksum(RUBRIC, PROMPT_TEMPLATE, JUDGE_MODEL_SPEC)
METRIC_VERSION = f"llm-judge-{RUBRIC_VERSION}-{_CHECKSUM}"
_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


class JudgeTimeoutError(RuntimeError):
    """Judge 超时属于评测侧错误：这题不评分，不计入 Agent 失败。"""


async def evaluate_open_answer(
    task: dict[str, Any], actual: dict[str, Any], trace_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """计算单题指标，最后一项是 task_pass。"""
    expected = task.get("expected") or {}
    base = {item["metric_name"]: item for item in evaluate_agent_final_answer(task, actual, trace_events)}
    prompt = build_prompt(
        question=str((task.get("input") or {}).get("query") or ""),
        reference=str(expected.get("reference_answer") or ""),
        points=list(expected.get("rubric_points") or []),
        answer=str(actual.get("final_answer") or ""),
    )
    try:
        response = await asyncio.wait_for(
            select_model(JUDGE_MODEL_SPEC).call(prompt, stream=False), timeout=JUDGE_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        # 不能原样抛出 TimeoutError：实验服务会把它当成被测 Agent 超时，计入失败。
        raise JudgeTimeoutError(f"Judge 在 {JUDGE_TIMEOUT_SECONDS} 秒内没有返回") from exc
    verdict = parse_verdict(response.content)
    details = {"judge_model": JUDGE_MODEL_SPEC, "rubric_version": RUBRIC_VERSION, "prompt_checksum": _CHECKSUM}
    if verdict is None:
        judge = _metric("judge_correct", None, "not_measured", "judge_output_invalid")
    else:
        passed = verdict["verdict"] == "pass"
        status, reason_code = ("passed", None) if passed else ("failed", "judge_failed")
        judge = _metric("judge_correct", float(passed), status, reason_code)
    judge["details"] = details | {"judge_reason": verdict["reason"] if verdict else None, "raw": response.content[:500]}

    result = [_restamp(base["tool_safety"]), _restamp(base["tool_selection"]), judge]
    trajectory = [
        _restamp(base[name]) for name in ("tool_call_budget", "loop_detected", "duplicate_call_rate", "tool_error_rate")
    ]
    failure = next((item for item in result[:2] if item["status"] == "failed"), None)
    if failure:
        task_pass = _metric("task_pass", 0.0, "failed", failure["reason_code"])
    elif not JUDGE_CALIBRATED:
        task_pass = _metric("task_pass", None, "not_measured", "judge_not_calibrated")
    elif judge["status"] == "not_measured":
        task_pass = _metric("task_pass", None, "not_measured", judge["reason_code"])
    else:
        task_pass = _metric("task_pass", judge["value"], judge["status"], judge["reason_code"])
    return result + trajectory + [task_pass]


def build_prompt(*, question: str, reference: str, points: list[str], answer: str) -> str:
    return PROMPT_TEMPLATE.format(
        rubric=RUBRIC,
        question=question,
        reference=reference or "（无）",
        points="\n".join(f"- {point}" for point in points) or "（无）",
        answer=answer or "（空）",
    )


def parse_verdict(content: str) -> dict[str, str] | None:
    """解析 Judge 输出；不是 {"verdict": "pass"|"fail"} 的 JSON 对象时返回 None。"""
    match = _JSON_OBJECT.search(content or "")
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    verdict = str(data.get("verdict") or "").strip().lower() if isinstance(data, dict) else ""
    if verdict not in {"pass", "fail"}:
        return None
    return {"verdict": verdict, "reason": str(data.get("reason") or "")}


def calibration_report(pairs: list[tuple[str, str | None]]) -> dict[str, Any]:
    """比较人工标注与 Judge 判定（"pass"/"fail"）；Judge 输出无效的样本单独计数，不进入一致率和 κ。"""
    judged = [(human, judge) for human, judge in pairs if judge is not None]
    confusion = Counter(f"human_{human}/judge_{judge}" for human, judge in judged)
    agreement = sum(human == judge for human, judge in judged) / len(judged) if judged else None
    return {
        "samples": len(pairs),
        "judge_invalid": len(pairs) - len(judged),
        "agreement": agreement,
        "kappa": cohen_kappa(judged),
        "confusion": dict(sorted(confusion.items())),
        "disagreements": [index for index, (human, judge) in enumerate(pairs) if judge is not None and human != judge],
    }


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """κ = (p_o - p_e) / (1 - p_e)；p_e 为两方各自类别比例乘积之和。样本为空或 p_e = 1 时不可计算。"""
    if not pairs:
        return None
    total = len(pairs)
    observed = sum(human == judge for human, judge in pairs) / total
    human_counts = Counter(human for human, _ in pairs)
    judge_counts = Counter(judge for _, judge in pairs)
    expected = sum(human_counts[label] * judge_counts[label] for label in human_counts) / total**2
    return None if expected == 1 else (observed - expected) / (1 - expected)


def _restamp(metric: dict[str, Any]) -> dict[str, Any]:
    return metric | {"metric_version": METRIC_VERSION}


def _metric(name: str, value: float | None, status: str, reason_code: str | None) -> dict[str, Any]:
    return {
        "metric_name": name,
        "metric_version": METRIC_VERSION,
        "value": value,
        "status": status,
        "reason_code": reason_code,
    }
