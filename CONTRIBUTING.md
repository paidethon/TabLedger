# Contributing to TabLedger

感谢关注 TabLedger！这是一个隐私优先的个人账单工具。

## 开发环境

```bash
# 后端（Python 3.12+）
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 前端（Node 22+）
cd web && pnpm install

# 生成合成测试 fixtures（完全虚构数据）
.venv/bin/python scripts/make_fixtures.py
```

## 测试

```bash
.venv/bin/pytest tests/unit tests/integration   # 常规测试（CI 运行）
cd web && pnpm build && pnpm exec tsc -b --noEmit
bash scripts/run_e2e_server.sh                  # 端到端（一次性实例）
```

`pytest -m private` 只在你本机存在真实账单与 `private/regression/config.json` 时运行；**不要**把真实数据或该配置提交到仓库。

## 提交规范

- 遵循 Conventional Commits（`feat:` / `fix:` / `test:` / `docs:` / `chore:` / `refactor:`）；
- 一个提交聚焦一件事。

## 代码约定

- 后端：`ruff check app`；金额用 `Decimal`/整数分，禁用 float；
- 确定性引擎（`app/core/`）**不得**引入 AI 依赖——AI 只允许出现在 `app/ai/` 的兜底路径；
- 不要在代码中硬编码任何个人标识（银行卡尾号、姓名等），一律走 `EngineConfig` / 用户设置；
- 前端遵循现有 Tailwind token（`--color-lumi-*`），改动请保持键盘可达与 reduced-motion 支持。

## 隐私红线（违反将拒绝合并）

- 不得提交任何真实账单、截图、个人标识或密码；
- 不得让上传文件路径依赖用户文件名；
- 不得把密码/API Key 写入日志、错误信息或遥测；
- 新增依赖需说明用途、许可证与维护状态（避免 70 个依赖的"全家桶"）。

## 提交前自检

```bash
ruff check app
python3 scripts/pii_scan.py
gitleaks detect --no-git
.venv/bin/pytest tests/unit tests/integration
```

## License

提交即表示你同意代码以 MIT 许可发布（见 LICENSE）。
