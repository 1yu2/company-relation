# 平台评测数据集

这里存放可以导入平台评测（`/evaluation`）的数据集文件。每个文件包含三部分：

- `dataset`：数据集名称、`source_kind` 和说明；
- `source`：题目来源、答案核对方式、复核状态，写入版本的 `source`；
- `tasks`：任务列表。每题包含 `task_key`、`protocol`（决定用哪个 evaluator 评分）、`input`、`expected`，以及可选的 `tags`。

| 文件 | 协议 | 题数 | 复核状态 |
| --- | --- | --- | --- |
| `agent_python_tasks_v1.json` | `agent_final_answer_v1` | 16 | 人工确认（2026-10-20） |
| `agent_fault_tasks_v1.json` | `agent_fault_recovery_v1` | 8 | 待人工确认（故障注入：工具报错后恢复、超时、数据源不可用） |

## 导入步骤

数据集版本一旦创建就不可修改；修改题目时，要在同一个数据集下创建新版本，旧版本保留，供历史实验对比。以下接口都需要管理员 token，示例中用 `$TOKEN` 占位。

```bash
BASE=http://localhost:5050/api/platform-evaluations
FILE=scripts/eval/datasets/agent_python_tasks_v1.json

# 1. 创建数据集（已存在则跳过，直接用已有的 dataset_id）
DATASET_ID=$(jq '.dataset' "$FILE" | curl -s -X POST "$BASE/datasets" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d @- | jq -r '.data.dataset_id')

# 2. 创建草稿版本
VERSION_ID=$(jq '{source, tasks}' "$FILE" | curl -s -X POST "$BASE/datasets/$DATASET_ID/versions" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d @- | jq -r '.data.version_id')

# 3. 发布（只有已发布的版本才能创建实验）
curl -s -X POST "$BASE/versions/$VERSION_ID/publish" -H "Authorization: Bearer $TOKEN"
```

发布后，在 `/evaluation` 页面或通过 `POST $BASE/experiments` 创建实验。真实 Agent 评测使用 `"runner": "agent_run"` 并指定 `agent_slug`。
