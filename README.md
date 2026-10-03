# TabLedger

**隐私优先的个人账单整理工具：把支付宝 / 微信 / 工商银行 / 中国银行账单整理成可直接导入一木记账的文件。** 当前版本 v0.1.0。

上传账单 → 自动识别来源 → 提取与余额链校验 → 跨源对账去重 → 退款回链 → 规则自动分类（可选低 token AI 兜底）→ 人工审核少量疑点 → 下载一木记账 `.xls`。

> AI 永远不参与金额、去重、余额校验等事实判断；分类永远先用本地规则，AI 只处理规则无法确定的一小部分，且只发送脱敏后的商户名与商品名。

## Features

- **四来源解析**：支付宝 CSV（GB18030）、微信 XLSX、工商银行 PDF、中国银行 PDF（含水印字体过滤与余额链校验）
- **跨源对账**：同卡同额、提现扣费净额、1 对多子集和、复合付款金额差、内部搬运延迟入账；每组匹配都记录类型/置信度/理由，可审计、可推翻
- **退款处理**：支付宝订单号前缀回链、微信退款评分回链、银行直连退款自动配对与人工覆盖；支持部分退款、全额退款、跨期退款
- **确定性分类**：词典规则（长键优先、方向感知）+ 用户规则沉淀；AI 仅兜底（批量、缓存、脱敏、可完全关闭）
- **一木导出**：纯 Python BIFF8 写出 9 列账单，每次导出后严格回读校验
- **应用内登录**：Argon2id、服务端可撤销会话、CSRF 双重提交、登录限速指数退避
- **单容器部署**：FastAPI + SQLite（WAL）+ 内置前端静态文件

## Docker Quick Start

```bash
mkdir -p data
# 生成一个本地 .env（不要提交到 Git）
printf 'TAB_BOOTSTRAP_PASSWORD=%s\n' "$(openssl rand -hex 16)" >> .env

docker run -d \
  --name tabledger \
  -p 127.0.0.1:8765:8000 \
  -v "$PWD/data:/data" \
  --env-file .env \
  ghcr.io/paidethon/tabledger:latest
```

然后用 Caddy（宿主机已有）反代：

```caddy
tab.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

首次启动会读取 `TAB_BOOTSTRAP_ADMIN` / `TAB_BOOTSTRAP_PASSWORD` 创建管理员（Argon2id 入库后不再依赖明文），WebUI 会提示修改初始密码。

| 环境变量 | 说明 |
|---|---|
| `TAB_BOOTSTRAP_ADMIN` | 初始管理员用户名（默认 `admin`） |
| `TAB_BOOTSTRAP_PASSWORD` | 初始密码，仅首次启动使用，不会入库 |
| `TAB_SECRET_KEY` | 可选；API Key 加密密钥，不设则自动生成到 `/data/secrets/app.key` |
| `TAB_ALLOWED_HOSTS` | 可选；域名白名单（逗号分隔，默认 `*`） |
| `TAB_COOKIE_NAME` | 会话 Cookie 名（默认 `__Host-tab_session`，HTTPS 下使用） |

## Supported Bills

| 来源 | 格式 | 说明 |
|---|---|---|
| 支付宝 | `.zip`（加密）/ `.csv` | GB18030；声明合计与明细自动核对 |
| 微信支付 | `.zip`（加密）/ `.xlsx` | 声明合计必须完全一致 |
| 工商银行 | `.pdf`（加密/明文） | 水印字体过滤 + 余额链校验 |
| 中国银行 | `.pdf`（加密/明文） | 最新在前自动反转 + 余额链校验 |

ZIP 密码自动尝试（与旧版一致的便利）；密码只在处理过程的内存中存在。

## AI Privacy

- 默认关闭；关闭时解析/对账/去重/规则分类/人工分类/导出全部可用
- 只对规则无法覆盖的**唯一商户键**批量调用（700 笔账单通常只有 1~2 次请求）
- 发送字段仅限：脱敏后的商户名、商品名、收支方向；金额/时间/卡号/账号/订单号/余额永不出境
- 结果必须通过本地分类体系校验；命中缓存的键 0 token
- 支持 OpenAI / DeepSeek / Anthropic / Gemini / OpenRouter / 任意 OpenAI 兼容端点（LiteLLM）

## Yimu Export

输出与一木官方模板相同的 9 列（日期、收支类型、金额、类别、子类、所属账本、收支账户、备注、标签），BIFF8 `.xls`，每次导出后自动回读校验行数/金额/日期/格式。提供账单文件与核对包（导入行 + 待分类清单 CSV）。

## Development

```bash
# 后端
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/unit tests/integration

# 前端
cd web && pnpm install && pnpm build

# E2E（启动一次性实例 + Chromium 全流程）
bash scripts/run_e2e_server.sh
```

技术栈：FastAPI、SQLAlchemy 2、Alembic、Argon2、pdfplumber/pikepdf、LiteLLM、React 18、Vite、TanStack Query、Tailwind v4。

## Security

- 见 [SECURITY.md](SECURITY.md) 与 [PRIVACY.md](PRIVACY.md)
- 上传安全：服务端随机文件名、类型嗅探、zip 炸弹/嵌套压缩包/路径穿越拦截、页数与条数上限
- 安全响应头（CSP、nosniff、Referrer-Policy、frame-ancestors）、TrustedHost、反代只信任本机 Caddy
- 报告漏洞请参考 SECURITY.md，请勿公开提交安全问题

## License

MIT — 见 [LICENSE](LICENSE)。
