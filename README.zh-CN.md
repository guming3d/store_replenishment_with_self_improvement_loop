# Agentic 门店补货与归因系统

**语言：** [English](README.md) · **中文（当前）**

这是一个端到端门店补货方案：

- 确定性 `(s, S)` 补货引擎生成可审计的 SKU 建议量。
- Microsoft Agent Framework 负责补货 Agent 编排。
- Harness 归因协调器调用受限的季节/节假日和替代关系诊断能力。
- 确定性反事实回放、Shapley 分配和守恒校验负责因果算量。
- 日销量回流形成决策结果台账，用实际需求同时评判系统建议量和人工修改量。
- 知识条目按门店/SKU 维度沉淀，并依据实测准确率逐步获得权重。
- React UI 支持补货数量调整、归因审核和最终提交。
- SQLite 用于本地开发，Azure PostgreSQL 用于多副本部署。

## 界面速览

下面按系统实际运行的顺序，走完闭环的一整轮。截图来自运行中的应用，数据为预置演示数据。

### 1. 引擎给出建议，并公开推导过程

每一行都带着生成它的 `(s, S)` 输入——需求预测、安全库存、提前期、现有库存与在途库存——
店长面对的是看得见的算术，而不是一个黑盒。

![补货建议](docs/screenshots/zh/suggestions.png)

### 2. 改数必须给出理由

改数量永远被允许，但从来不是免费的。店长必须从封闭词表里选择原因类型，并写清具体情况。
这段文字被**当作主张记录，而不是当作证据采信**——它是系统随后要评判的输入，不是系统信任的事实。

![调整数量并填写原因](docs/screenshots/zh/suggestions-adjust-modal.png)

### 3. 每次调整自动生成归因 Case

只要还有 Case 未闭环，整个 Run 就无法提交。再次调整会让旧 Case 失效（`SUPERSEDED`），
旧的审批同时作废，审批永远不会和它批准过的数字脱节。

![归因 Case 队列](docs/screenshots/zh/attribution-cases.png)

### 4. Case 台账：改了什么，相对什么基准

反事实精确关掉模型点名的那几个原因再重算，得到 `bare_baseline_qty`，
季节和节假日这才第一次有了可被度量的对照面。

![归因 Case 详情](docs/screenshots/zh/attribution-detail.png)

### 5. 用证据评判店长的主张

系统在问一个店长自己回答不了的问题：*你点名的那个因子，取任何值能复现你实际下的量吗？*
确定性代码反解引擎给出答案。这里的 `UNCALIBRATED` 表示方向判断是对的，但数量无法由它复现——
这正是"一个理由"和"一个解释"之间的差别。

![店长主张裁定与证据](docs/screenshots/zh/attribution-evidence.png)

### 6. 带符号的分摊与守恒校验

各项贡献必须加回到实际差异。残差是展示出来的，而不是被抹掉的：
它代表店长的分歧中引擎自身假设解释不了的那一部分，而那正是知识候选存在的意义。

![Shapley 分摊与守恒恒等式](docs/screenshots/zh/attribution-allocation.png)

### 7. 扣住整个 Run 的队列

每一个未闭环的 Case 都会阻塞它所属 Run 的提交。注意页面顶部的提示：**"忽略"不等于"批准"**——
忽略只会把 Case 置为已取消，并让那个 Run 在归因重跑之前永远无法提交。
系统里没有任何一个按钮可以让这个要求消失。

![审核队列](docs/screenshots/zh/review-queue.png)

### 8. 被采纳的知识进入下一次补货

条目的权重只能靠实际销量兑现来赚取。这里没有任何一条是靠审核人的信心晋级的，
每一份权重都是实测结果。

![知识条目](docs/screenshots/zh/knowledge.png)

### 9. 运维视图

Worker 健康度、队列深度、Case 状态分布与租约占用——让这个闭环可以被运营，而不只是被演示。
租约过期是"Worker 中途死掉"被看见的方式，否则队列只会无声地卡住。

![管理概览](docs/screenshots/zh/admin-overview.png)

## 核心业务约束

只要用户将 `final_qty` 修改为不同于 `chosen_qty` 的值，就必须完成匹配当前数量版本的归因并由人工审核通过，整个 Run 才能提交。

```text
生成补货建议
  -> 修改数量并填写原因
  -> 自动创建归因 Case
  -> Agent/Harness 诊断
  -> 确定性反事实与 Shapley 归因
  -> 人工审核
  -> 全部修改项 HUMAN_APPROVED
  -> 整个 Run 原子提交并锁定
```

系统没有跳过归因或强制提交的旁路：

- 再次修改数量会将旧 Case 标记为 `SUPERSEDED`，旧审批立即失效。
- `partial` 报告不能直接批准，必须先人工补充归因。
- Agent 连续失败后可进行结构化人工归因，但仍然必须审核。
- 最终提交是全有或全无；成功后 Run 和关联 Case 只读。
- 审批与知识沉淀相互独立：审批只解锁提交，知识必须逐条裁定（采纳 / 修订后采纳 / 驳回）。

## 归因产出：从"分摊数量"到"知识候选"

分摊（`allocations`）解释的是这一次的数量差异如何在各原因之间划分；它无法回答"下次系统应该怎么假设"。

原先的分摊还有一个结构性缺陷：反事实以引擎自己的重算结果为基准，而季节、节假日这类系数在该基准里
已经生效，把它再注入一次不会改变任何数量，分摊天然为零——季节和节假日永远分不到任何数量。
现在反事实改为**关掉模型点名的那几个原因**再重算，得到 `bare_baseline_qty`：

```text
Σ signed_contribution_qty + unexplained_signed_gap = override_qty − conservation_anchor_qty
```

`conservation_anchor_qty` 对反事实报告等于 `bare_baseline_qty`，对人工撰写的报告回落到
`recommended_qty`（人工填写的原因本来就是直接对着差异写的）。没有被点名的假设保持引擎原值：
它们是这次决策的输入，不是被拆解的对象。

因此残差是正常且诚实的结果：它表示引擎的假设解释了**建议量**中属于它们的部分，而店长的分歧
仍未被这些假设解释——那正是知识候选（而不是分摊）要回答的问题。

因此报告在分摊之外还输出 `knowledge_candidates`：**Agent 只说条件和适用范围，不出数字**；
确定性代码反解引擎，搜索 `kind` 指定的那个参数取什么值才能复现店长实际下的量。
候选按定义就是"引擎的假设需要改变多少"，degeneracy 被结构性地消除。

```text
calibration_status: EXACT | APPROXIMATE | UNREACHABLE | ALREADY_CORRECT | BLOCKED
acceptable = calibration_status ∈ {EXACT, APPROXIMATE}
```

- **`UNREACHABLE` 是有用的结论，不是失败。** 例如店长把 48 改成 10，但整箱起订下限是 18——
  任何需求系数都到不了 10，说明这次调整根本不是需求判断。此时 `proposed_value` 置空、
  只保留 `boundary_value`，审核人员无法采纳一个引擎已证明无效的数字，报告同时打上
  `NO_CALIBRATABLE_CANDIDATE` 风险标记。
- **`magnitude_plausible: false` 只提示不拦截。** 精确算术会算出 4.6 倍的季节系数；
  是否成立由人判断，一次 `WRONG_MAGNITUDE` 驳回正是闭环在起作用。

### 审核记录驳回，而不只是记录同意

审核从"批准 / 打回"升级为**逐候选裁定**，驳回原因取自封闭词表
（`WRONG_CAUSE` / `NOT_THE_DRIVER` / `WRONG_SCOPE` / `WRONG_MAGNITUDE` / `ONE_OFF_EVENT` /
`INSUFFICIENT_EVIDENCE` / `ALREADY_KNOWN` / `OTHER`），只有词表封闭，不同审核人的统计才可比。

驳回单独建表而不是给知识条目加一个 `REJECTED` 状态：驳回没有取值、没有可解析的范围，
放进引擎读取的那张表里迟早会被误用。候选原样存档，日后修改提示词可以直接对着它答错的那批 Case 重放。

| 接口 | 说明 |
|---|---|
| `GET /api/attribution/knowledge/rejections` | 被驳回的知识候选，可按门店/SKU/原因筛选 |
| `GET /api/attribution/knowledge/feedback` | 归因 Agent 的成绩单：采纳率，以及按原因分组的驳回统计 |

## 学习闭环：从归因到准确率

归因只解释系统建议量和人工修改量之间的差异，它本身不判断谁更接近门店真实需要。因此仅靠归因和审批，系统沉淀的是人工偏好而不是正确性。日销量回流补齐了这一环：

```text
提交锁定 -> 为每条决策开启评判窗口(含未修改行)
  -> 日销量回流(POS)
  -> 窗口闭合后计算事后最优量
  -> 判定 ENGINE_BETTER / HUMAN_BETTER / TIE
  -> 更新相关知识条目的后验置信度
  -> 置信度足够时知识才开始影响引擎
```

设计要点：

- **未修改行同样进入台账。** 只采样分歧会让系统永远学不到“人工认可的建议本身也可能是错的”。
- **评判窗口取决策日次日起、长度为提前期 + 覆盖天数**，且只读取冻结快照，不读当前配置，避免用今天的参数评判过去的决策。
- **缺货损失计入需求。** 空货架是未满足的需求而不是低需求，否则较小的数量总会显得更正确。
- **窗口未闭合就是 `PENDING`，不是平局**，只有 `COMPLETE` 的行才计入准确率看板和知识晋升。
- **箱规差异视为平局。** 半个箱规以内的差距双方都无法控制，不构成谁更优。
- **知识发布后权重为 0。** 权重来自命中率的 Wilson 置信下界，`CANDIDATE -> SHADOW -> ACTIVE -> RETIRED` 全部由实测结果驱动；证据转向时权重自动回落并退休，不依赖人工发现。

### 知识如何真正影响下一次补货

闭环的最后一段：`ACTIVE` 的知识条目在每次补货时被解析成引擎输入。
在此之前 `engine.py` 里没有任何一处提到知识——条目可以被采纳、被晋升，却对未来的建议毫无影响。

```text
engine.KNOWLEDGE_TARGETS = factor_overrides.season | factor_overrides.holiday
                         | target_daily_demand_delta | params.fill_rate | params.shelf_max
```

- **无法落地的指令必须报错，不能忽略。** 静默跳过的知识和"没有效果的知识"在外部完全无法区分。
  `SUBSTITUTION_RATE` 指向的是种子输入而非引擎参数，因此归入 `unsupported` 明确返回。
- **调用方显式传入的参数永远优先。** 反事实重算会钉住某个系数，这个探针必须活下来。
- **每次运行都产出 `knowledge.resolve` 追溯步骤**（无论是否命中），步骤编号因此不会在两次运行之间漂移。
- **快照冻结 `knowledge_applied`，重算只读快照。** 事后才被采纳的条目不允许改写它所归因的那个基线，
  否则每条候选的反解都会随知识库增长而漂移。

新增接口：

| 接口 | 说明 |
|---|---|
| `POST /api/attribution/outcomes/daily-sales` | 回流日销量（可含缺货损失），幂等并触发重算 |
| `GET /api/attribution/outcomes` | 决策结果台账，可按门店/SKU/状态/判定筛选 |
| `GET /api/attribution/accuracy` | 准确率看板：引擎与人工的 MAE/MAPE、胜率、缺货与超储 |
| `GET /api/attribution/knowledge` | 知识条目，可按门店/SKU/状态筛选 |
| `GET /api/attribution/knowledge/resolve` | 某门店 × SKU 当前实际生效的知识及其权重 |

## 主要模块

| 路径 | 说明 |
|---|---|
| `forecasting_cache/` | 预计算的门店 × SKU 预测输入 |
| `backend/engine.py` | 确定性 `(s, S)` 补货引擎 |
| `backend/agent_runtime.py` | 原补货 Agent Framework 编排 |
| `backend/attribution/` | Case/Run 状态机、Harness、反事实归因、Worker、持久化 |
| `backend/attribution/outcomes.py` | 决策结果评判：窗口、事后最优量、判定与准确率聚合（纯函数） |
| `backend/attribution/knowledge.py` | 知识置信度：Wilson 下界、权重、状态流转、范围匹配，以及解析成引擎指令 `engine_directives`（纯函数） |
| `backend/api/main.py` | FastAPI 补货、归因、审核和提交 API |
| `backend/migrations/` | SQLite/PostgreSQL Alembic 迁移 |
| `frontend/` | React + Ant Design 补货和归因审核 UI |
| `infra/` | PostgreSQL、托管身份、迁移 Job 和 Container Apps |
| `CONTRACT.md` | 完整 API 与状态机契约 |

## 本地运行

### 前置条件

- Python 3.11 或更高版本
- Node.js 18 或更高版本
- npm
- 可选：Azure CLI，用于连接 Microsoft Foundry

### 1. 安装后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

默认使用 `sqlite+aiosqlite:///./attribution.db`。本地启动时会自动创建所需表。

如需启用真实 Harness Agent Loop，编辑 `backend\.env`：

```dotenv
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_MODEL_DEPLOYMENT=<model-deployment-name>
ATTRIBUTION_WORKER_ENABLED=true
ATTRIBUTION_WORKER_CONCURRENCY=4
# Development only: capture each model/tool turn input and output in Agent Trace.
ATTRIBUTION_DEBUG_RAW_IO=true
```

本地使用 `AzureCliCredential`：

```powershell
az login
```

未配置 Foundry 时，确定性补货仍可正常使用。归因 Worker 会明确记录 Agent 不可用并重试；达到重试上限后 Case 进入 `FAILED`，用户必须使用结构化人工归因完成审核，不能绕过。

### 2. 安装前端

```powershell
cd ..\frontend
npm install
```

### 3. 启动前后端

在仓库根目录执行：

```powershell
.\start-local.ps1
```

也可以分别启动：

```powershell
# 终端 1
cd backend
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000

# 终端 2
cd frontend
npm run dev
```

- 前端：http://localhost:3000
- 后端：http://localhost:8000
- 本地默认用户名：`dmall`
- 本地默认密码：`dmalltest`
- 本地默认管理员用户名：`dmall-admin`
- 本地默认管理员密码：`dmalladmin`

共享环境应通过 `REPLENISH_DEMO_USERNAME`、`REPLENISH_DEMO_PASSWORD` 和 `REPLENISH_AUTH_SECRET` 配置凭据，不要使用本地默认值。管理员账号通过 `REPLENISH_ADMIN_USERNAME`、`REPLENISH_ADMIN_PASSWORD` 配置；两者留空则不注册管理员账号，管理控制台不可用。

### 管理控制台

以管理员账号登录后，左侧导航会额外出现“管理控制台”分组：

| 页面 | 用途 |
| --- | --- |
| 运行总览 | 归因 worker 健康度、队列积压、任务状态分布、worker 租约 |
| 归因批次 | 按补货批次查看归因进度与状态分布 |
| 审核队列 | 全量待审核 / 要求修订 / 失败任务，支持批量移除 |
| 诊断与知识 | 已注册的诊断 Agent 声明，以及生效中的归因知识条目 |

`/api/admin/` 下的所有接口由服务端按角色校验，普通采购账号访问返回 403。

**关于“移除待审核任务”**：移除等价于把归因任务置为“已取消”，只清空审核队列，不代表归因通过。补货运行的提交闸门只接受“已批准”，因此移除一个原本阻塞提交的任务，会让该补货运行无法提交，需要重新发起归因。审核队列的“是否阻塞补货提交”列和确认弹窗会在操作前提示这一后果；若目的是解除阻塞，应到任务详情页人工批准。

### 生成演示用归因数据

归因只能量化 `backend/attribution/seeds/` 里真实存在的因子。随手挑一个门店、商品和日期，多半会落到“没有可核验原因”这一支——结论本身是对的，但看不到任何分摊过程。下面的脚本按种子数据挑好了组合，一次生成四个覆盖不同归因形态的演示 Case：

```powershell
cd backend
.venv\Scripts\python.exe scripts\seed_demo_attribution.py
```

| 场景 | 说明 |
| --- | --- |
| `multi` | 冬季 + 元旦两条证据都算出数量，证据覆盖率约 94%，未解释量很小——最理想的归因输出 |
| `partial` | 节假日算出数量；季节性虽被判定适用但缺少当月因子，标记 `EVIDENCE_UNAVAILABLE_FOR_CAUSE` 而不是估一个数 |
| `single` | 只有夏季季节性一条证据成立，不会被稀释成多个似是而非的原因 |
| `none` | 店长给了理由但数据里找不到支撑，系统如实说明而不是编造原因 |

脚本走的是和真人完全一致的接口（`/api/replenish/run` → `/api/replenish/adjust`），归因由真实 Agent 执行，每个 Case 约需一分钟。加 `--no-wait` 只排队不等待，加 `--only multi` 只生成指定场景。每次运行都会新建 Case，重复执行会在列表里留下多条同门店同商品的记录。

## UI 操作流程

登录后可先打开左侧第一项“使用指南”。指南用业务语言展示完整补货流程、数量修改后的归因路径、各状态对应的操作，以及“补货建议”“归因任务”“补货参数”“运行历史”的使用场景。

### 1. 生成补货建议

1. 登录并进入“补货建议”。
2. 选择门店和决策日期。系统固定按“当天申请、次日到货、第三日上架”计算。
3. 如有需要，先维护当前库存和补货参数。
4. 选择确定性引擎或补货 Agent 编排。
5. 点击生成，查看 `chosen_qty`、库存位置、再补点和计算解释。

### 2. 修改数量并启动归因

1. 修改一个或多个 SKU 的“最终补货量”。
2. 点击“保存草稿并启动归因”。
3. 选择必填的原因码，可选填原因说明。
4. 保存后，系统为每个修改 SKU 创建独立 Case。

保存成功后可以：

- 直接打开唯一 Case；
- 或进入“归因审核”查看同一 Job 下的多个 Case。

### 3. 查看因果分析

Case 页面会自动轮询 `QUEUED` 和 `RUNNING` 状态。报告生成后重点检查：

- **概览**：结论、主要原因、风险和冲突；
- **证据**：证据来源、版本和新鲜度；
- **分配**：各原因的有符号贡献、未解释残差和守恒公式；
- **追踪**：Worker 尝试、工具执行和脱敏事件；
- **版本**：Agent 报告及人工审核历史。

归因数量始终满足：

```text
原因贡献之和 + 未解释有符号残差
  = override_qty - recommended_qty
```

### 4. 人工审核

| 操作 | 使用场景 |
|---|---|
| `APPROVE` | Agent 报告完整、非 partial，且证据与分配可接受 |
| `REQUEST_CHANGES` | 报告需要重新处理或人工补充 |
| `AMEND_AND_APPROVE` | 人工修订原因贡献和摘要后批准 |
| `MANUAL_AND_APPROVE` | Agent 最终失败后录入结构化人工归因并批准 |

人工修改原因贡献时，填写每个 cause 的：

- 原因码和领域；
- 有符号贡献量；
- 解释；
- 可选证据引用。

选择“发布为知识”时还必须指定作用域和过期时间。该选项不是提交 Gate 的必要条件。

审核抽屉在原因表之下按每条**知识候选**渲染一张卡片，展示候选主张、重算效果、触发条件和适用范围，
并提供四选一裁定：

| 裁定 | 含义 |
|---|---|
| 暂不处理 | 不写库、不记录，候选留在报告里 |
| 采纳 | 按候选原值写入知识库（状态 `CANDIDATE`、权重 0） |
| 修订后采纳 | 审核人员改写取值、生效区间和触发条件后写入 |
| 驳回 | 不写入知识库，改为写入驳回台账，必须选择原因 |

`acceptable: false` 的候选（例如受整箱起订下限限制无法标定）只能驳回或暂不处理，
UI 会禁用采纳与修订，避免采纳一个引擎已证明无效的取值。
知识裁定同样不是提交 Gate 的必要条件；打回（`REQUEST_CHANGES`）时不接受任何裁定。

### 5. 提交补货结果

返回“补货建议”后点击“提交最终结果”：

- 如果任何修改 SKU 缺少最新的 `HUMAN_APPROVED` Case，UI 会显示阻塞项并跳转到 Case。
- 所有修改项审批完成后，Run 进入 `READY_TO_SUBMIT`。
- 提交成功后进入 `SUBMITTED_LOCKED`，数量和归因 Case 都不可再修改。

## 状态说明

Run 状态：

```text
DRAFT
  -> ATTRIBUTION_RUNNING
  -> ATTRIBUTION_REVIEW_REQUIRED
  -> READY_TO_SUBMIT
  -> SUBMITTED_LOCKED
```

Case 状态：

```text
QUEUED -> RUNNING -> NEEDS_REVIEW -> HUMAN_APPROVED
                         |              ^
                         -> CHANGES_REQUESTED
RUNNING -> FAILED -> MANUAL_AND_APPROVE
任意未提交版本 -> SUPERSEDED
可取消状态 -> CANCELLED
```

## 运行端到端集成测试

以下测试不访问外部 Foundry 服务，而是注入符合 Harness 输出 Schema 的受控诊断结果。它仍然运行真实的 API、SQLite 持久化、租约 Worker、确定性反事实回放、Shapley 分配、人工审核、知识发布和最终提交：

```powershell
cd backend
python -m pytest tests\test_attribution_api.py::test_replenishment_to_causal_analysis_human_review_and_submission -q
```

运行完整后端测试：

```powershell
python -m pytest -q
```

构建前端：

```powershell
cd ..\frontend
npm run build -- --emptyOutDir
```

## 关键配置

| 环境变量 | 默认值/用途 |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry Project Endpoint；为空时 Agent 能力不可用 |
| `FOUNDRY_MODEL_DEPLOYMENT` | Harness 与补货 Agent 使用的模型部署名 |
| `ATTRIBUTION_DATABASE_URL` | 本地默认 SQLite；Azure 使用 `postgresql+asyncpg` |
| `ATTRIBUTION_WORKER_ENABLED` | 是否启动进程内归因 Worker，默认 `true` |
| `ATTRIBUTION_WORKER_CONCURRENCY` | 每个后端副本并发 Case 数，默认 `4` |
| `ATTRIBUTION_DEBUG_RAW_IO` | 开发调试开关；默认 `false`。启用后记录每轮模型/工具输入输出 |
| `ATTRIBUTION_POSTGRES_ENTRA_AUTH` | PostgreSQL 是否使用 Entra Token |
| `ATTRIBUTION_POSTGRES_MANAGED_IDENTITY_CLIENT_ID` | PostgreSQL 用户分配托管身份 |
| `FOUNDRY_MANAGED_IDENTITY_CLIENT_ID` | 可选的 Foundry 用户分配托管身份；否则使用系统身份 |
| `ATTRIBUTION_RUN_MIGRATIONS_ON_STARTUP` | Azure Revision 启动前执行 Alembic 安全迁移 |

Harness 只暴露有类型的领域诊断工具。Shell、文件读写、Web Search、后台 Agent、Todo/Mode 和工具自动审批均被禁用；模型只判断证据是否适用，不负责任何数量或提交决策。

前端提交数量修改时会把当前界面语言写入 Attribution Case：中文界面使用 `zh-CN`，英文界面使用 `en-US`。Coordinator、诊断 Agent、摘要、原因解释和确定性原因标签均使用该语言；机器字段（JSON key、`cause_code`、`domain`、`evidence_refs`）保持稳定。用户在另一种界面语言下重试失败 Case 时，重试结果会改用当前界面语言。

## Azure 部署

生产部署会创建：

- Azure Container Apps 前后端；
- Azure Database for PostgreSQL Flexible Server；
- PostgreSQL Entra 管理员和共享数据库托管身份；
- 手动 Alembic 迁移 Job；
- Application Insights 和 Log Analytics；
- 每个后端副本四个归因 Worker 槽位，默认两个常驻副本。

部署脚本会先运行迁移 Job，成功后再更新应用镜像：

```powershell
cd infra
.\deploy.ps1
```

Linux/macOS：

```bash
cd infra
./deploy.sh
```

详细资源、身份和迁移说明见 [`infra/README.md`](infra/README.md)。

## 常见问题

### Case 一直停留在 `QUEUED`

确认：

- `ATTRIBUTION_WORKER_ENABLED=true`；
- 后端 `/api/health` 中 `attribution_worker.running=true`；
- 数据库可连接；
- Worker 副本未缩容到零。

### Case 最终进入 `FAILED`

检查 Case 的 Attempts 和 Trace：

- Foundry Endpoint 或模型部署是否正确；
- 本地是否已执行 `az login`；
- Azure 托管身份是否拥有 Foundry 调用权限；
- 模型或工具调用是否超时。

无法恢复 Agent 时，使用 `MANUAL_AND_APPROVE` 完成结构化人工归因。系统不会允许跳过归因直接提交。

### 如何确认归因 Agent 实际执行

打开 Case 的 **Agent Trace** 页签。每个 Attempt 会显示真实的模型调用数和工具调用数，并可下载脱敏的 JSONL 原始日志。日志包含：

- `HARNESS_STARTED`；
- `MODEL_CALL_STARTED`、`MODEL_CALL_COMPLETED` 或 `MODEL_CALL_FAILED`；
- `TOOL_CALL_STARTED`、`TOOL_CALL_COMPLETED` 或 `TOOL_CALL_FAILED`；
- `HARNESS_STRUCTURED_OUTPUT`；
- `DETERMINISTIC_REPORT_COMPLETED`。

日志记录调用边界、耗时、模型、Token 使用量、工具名称和结构化结果摘要，不记录 Prompt 正文、工具参数值、凭据或模型私有思维链。功能上线前生成的历史 Attempt 只有原有的开始/完成事件，不会补造模型或工具调用数据。

开发阶段如需检查每轮原始输入输出，在 `backend\.env` 中设置：

```dotenv
ATTRIBUTION_DEBUG_RAW_IO=true
```

重启后端后，新 Attempt 会额外记录 `MODEL_RAW_INPUT`、`MODEL_RAW_OUTPUT`、`TOOL_RAW_INPUT` 和 `TOOL_RAW_OUTPUT`。这些事件可能包含门店、商品、库存和预测等业务数据，只应在受控开发环境中短期开启。凭据字段和模型私有 reasoning 内容始终会被脱敏。

### 修改数量后提交按钮仍被阻塞

检查是否：

- Case 仍处于 `RUNNING`、`NEEDS_REVIEW` 或 `CHANGES_REQUESTED`；
- 修改数量后又进行了二次修改，导致旧 Case 已 `SUPERSEDED`；
- 报告为 `partial`，尚未执行 `AMEND_AND_APPROVE`；
- 只有部分修改 SKU 已审批。

### 前端显示后端不可用

普通演示数据在本地开发中可以回退到 Mock，但归因和提交接口永远不会伪造成功。请确认后端已启动、登录 Token 有效，并检查浏览器 Network 面板中的 API 错误。

完整接口字段和错误码见 [`CONTRACT.md`](CONTRACT.md)。
