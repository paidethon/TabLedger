# Architecture

TabLedger 是一个单容器、单用户的账单整理应用：FastAPI 后端 + 内置 React 前端 + SQLite。

```
┌────────────────────────── container ──────────────────────────┐
│                                                               │
│  React SPA (web/dist, uvicorn 静态托管)                        │
│        │  fetch /api/v1/*（session cookie + CSRF）             │
│        ▼                                                       │
│  FastAPI (app/main.py, app/api/*)                              │
│   ├─ auth: Argon2id + 服务端 opaque session + CSRF + 限速       │
│   ├─ jobs: 上传暂存（随机ID）→ 任务队列                          │
│   └─ settings: AI 配置（Key 加密）/ 账户映射 / 规则 / 备份        │
│        │                                                        │
│        ▼                                                        │
│  JobWorker（单线程后台，app/services/job_service.py）            │
│   1 extract    → app/core/extraction.py                        │
│   2 reconcile  → app/core/reconciliation/ledger.py             │
│   3 classify   → 规则 (app/core/classification/)               │
│                 → AI 兜底 (app/ai/，可选，缓存优先)              │
│   4 persist    → transactions + matches（可解释）               │
│        │                                                        │
│        ▼                                                        │
│  Export (app/core/exporters/yimu.py)                            │
│   纯 Python BIFF8 写出 .xls → 严格回读校验 → 通过才交付          │
│                                                               │
│  SQLite (/data/tabledger.sqlite3, WAL)                         │
└───────────────────────────────────────────────────────────────┘
```

## 核心原则

1. **确定性引擎优先**：金额、余额链、去重、退款回链、转账识别全部由 `app/core/` 的确定性 Python 代码完成。AI 永远不是事实的裁判，只是分类兜底。
2. **一切可解释**：每组跨源匹配记录 `match_type / confidence / reason / 参与方`，UI 展示并允许人工推翻（override 存库，不篡改解析器）。
3. **对账口径与旧版一致**：`app/core/reconciliation/` 是对旧流水线 LedgerBuilder 的等语义移植；本机真实数据回归（`pytest -m private`）保证记录集合、处置分布、总额完全一致。
4. **隐私分层**：原始文件（默认处理后即删）→ 结构化记录（长期）→ AI payload（脱敏最小字段），三层各自最小化。

## 模块地图

| 路径 | 职责 |
|------|------|
| `app/core/parsers/` | 支付宝 CSV / 微信 XLSX / 工行 PDF / 中行 PDF 解析；`archive.py` 处理加密 zip 与来源嗅探（zip 炸弹/穿越防护）；`xlsx.py` 用 defusedxml 防实体扩展 |
| `app/core/context.py` | `EngineConfig`：户主名、银行卡映射等个人化配置的唯一入口（仓库代码零硬编码） |
| `app/core/reconciliation/` | `predicates.py` 处置判定；`ledger.py` LedgerBuilder（跨源匹配状态机）；`refunds.py` 银行直连退款自动配对 |
| `app/core/classification/` | 规则匹配（长键优先、方向感知、非法对拒绝）；从对账结果构建 9 列导入行 |
| `app/core/exporters/biff8.py` | 纯 Python BIFF8/CFB 写出器 + 严格回读校验（表头/行数/金额/日期/格式） |
| `app/ai/` | `sanitizer.py` 脱敏与归一化；`service.py` 缓存优先、批量、结构化输出、taxonomy 校验 |
| `app/services/` | job worker（重启安全的 interrupted 状态）、导出服务、设置服务（Key 加密） |
| `app/db/` | SQLAlchemy 模型（金额=整数分）、Alembic 迁移（启动时执行，失败安全停止） |
| `web/` | React 18 + Vite + TanStack Query；登录/导入/审核/规则/设置 |

## 匹配层级（与旧版一致）

| 层 | 规则 | 置信度 |
|----|------|--------|
| 退款回链 | 支付宝订单前缀精确回链；微信按状态+金额+商户评分回链 | high |
| L1 同卡同额 | 同卡、同方向、同金额、≤5 分钟 | high |
| L2 提现净额 | 提现额 − 明确服务费 = 银行入账 | high |
| L3 子集和 | 多笔同秒订单合计 = 一笔银行扣款（唯一解） | high/medium |
| L4 复合付款 | 付款方式含 `&`，差额由余额/红包解释 | medium |
| L5 疑似 | 3h 内同卡同额 + 银行摘要暗示 | low → 人工 |
| 内部搬运 | 已判「内部资金搬运」的平台行与延迟银行行影子配对 | medium |

## 为什么这样设计

- **单进程 worker 而非 Celery**：单用户场景，SQLite 队列 + 唤醒事件足够；重启时 `running → interrupted`，永不卡死。
- **SSE 而非 WebSocket**：单向进度推送，无需额外依赖。
- **BIFF8 手写而非 openpyxl**：一木对 `.xls`（BIFF8）兼容性已验证；文本全部入 SST 字符串单元格，天然免疫公式注入；写出即回读校验。
- **EngineConfig 注入而非配置文件**：个人标识只存在于用户数据库，仓库保持零个人数据。
