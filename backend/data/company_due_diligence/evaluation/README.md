# 企业尽调 Golden 数据集

本目录只保存脱敏、冻结、可审阅的 JSON 数据集。每条样本必须声明：

- `review_status=human_verified`：已由业务人员独立核对，可进入严格发布门禁；
- `review_status=source_derived_unreviewed`：由来源或程序派生，只用于开发回归，不能形成生产准确率结论。

数据集必须包含版本、业务时点、来源类别和稳定 checksum。广东企业 Golden 使用 `guangdong_enterprise`，历史 30 家公开 AkShare 基线使用 `akshare_public_baseline`，两者不得合并汇总。

人工复核流程：选择样本 → 对照只读来源记录与业务日期 → 核对规范化事实/关系/路径/证据来源 → 填写非敏感复核人标识 `reviewed_by`、复核时间 `reviewed_at` 和复核说明 `reviewer_note` → 将状态改为 `human_verified` → 重新计算并填写 `declared_checksum`。

`human_verified` 样本缺少上述三项复核留痕时会被模型拒绝；`source_derived_unreviewed` 样本禁止填写 `reviewed_by` 或 `reviewed_at`，避免程序派生数据伪装成人工 Golden。

复核完成后在 API 容器内冻结文件：

```bash
uv run --no-sync python scripts/company_due_diligence/freeze_quality_dataset.py \
  data/company_due_diligence/evaluation/<dataset>.json --write
```

脚本只输出数据集 ID、版本、checksum、样本数和人工复核率，不输出样本事实或来源内容；不加 `--write` 时只校验现有 `declared_checksum`。
