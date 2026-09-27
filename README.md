# 博物馆藏品来源与返还审查

标准库实现、SQLite 持久化的独立项目。它管理藏品、历史流转事件、来源引用、证据、权利主张和审查阶段，并提供面向公众、主张人、审查员和工作人员的分层视图。

## 运行

```bash
python3 app.py --init --seed
python3 app.py
```

访问 <http://127.0.0.1:8103>。数据库默认是 `provenance.db`。测试命令：

```bash
python3 -m unittest -v
```

演示身份通过 `X-User-Id` 传入：`staff`、`reviewer1`、`claimant1`、`public`。

## 主要接口

- `POST /api/objects`、`GET /api/objects`、`GET /api/objects/{id}`：藏品登记与分层查看。
- `POST /api/objects/{id}/update`：更新藏品并创建完整快照。
- `POST /api/sources`、`GET /api/sources`、`POST /api/objects/{id}/events`：来源与流转事件。
- `POST /api/objects/{id}/events/{event_id}/source`：为既有事件补挂来源（材料补齐）。
- `POST /api/objects/{id}/evidence`：上传证据，服务端计算 SHA-256。
- `POST /api/objects/{id}/claims`：提交权利主张。
- `POST /api/claims/{id}/transition`：按 `submitted → under_review → negotiating → resolved_return/rejected` 流转。
- `POST /api/objects/{id}/visa/sign`、`GET /api/objects/{id}/visa`：来源签证签署与状态查询。
- `GET /api/objects/{id}/history` 与 `/history/{version}`：版本历史及历史快照。

公众看不到持有人和内部事件；主张人只能查看自己的主张；阶段不能跳跃或从终态重新打开；每次对象变化都会保存 JSON 快照和审计记录。

## 来源签证

审查员确认"每个公开流转事件都有来源和至少一份内部证据"后，按当前藏品版本签署签证。签证记录**签署时版本号**与**证据摘要**（对藏品登记信息、全部事件及来源、全部证据分别取 SHA-256，并附公开事件数与内部证据哈希清单）。

- 签署时材料不齐会被拒绝（`visa_requirements_unmet`，响应带缺口清单）；补齐来源或证据后由审查员重签，旧签证自动标记 `superseded`。
- 签署后藏品、事件/来源或证据任一变化都会使摘要失配，签证即时失效，`GET /visa` 返回具体失效原因（哪一类材料变了）与当前材料缺口。
- 主张进入 `negotiating` 或 `resolved_return` 时强制校验：未签证报 `visa_required`，已失效报 `visa_invalid`，均被拦截。
- 主张阶段流转本身不改变来源摘要，因此有效签证在审查推进期间保持有效。
- 主张人只能看到签证是否有效；公众不可见签证信息。

演示页（`http://127.0.0.1:8103`）可切换四种角色查看签证状态与失效原因，并按角色补齐材料、重签、流转主张；"一键演示复审流程"会自动走完：签署被拒 → 补来源/证据 → 签证 → 协商 → 变更导致失效 → 拦截 → 复审重签 → 完成返还。`--seed` 会额外写入一件材料不全的演示藏品（`M-2026-1`）和一条待审主张。
