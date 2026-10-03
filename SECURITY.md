# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅ |

## Reporting a Vulnerability

请通过 GitHub 的 **Private vulnerability reporting**（仓库 Security 标签页）私下报告安全问题。

**不要**通过公开 issue 报告安全问题。

请在报告中包含：

- 影响的版本与部署方式（Docker / 源码）；
- 复现步骤或概念验证；
- 影响评估。

我们会在 7 天内确认收到，并在修复发布前不公开细节。

## Security Model

TabLedger 是**单用户、自托管**的个人财务工具，安全设计围绕这一前提：

### 认证与会话

- 密码使用 Argon2id 哈希（64 MB 内存参数），数据库不存明文；
- 会话为服务端 opaque token（256-bit 随机），数据库只存 SHA-256 摘要，可撤销、有过期时间；
- 登录 Cookie：`HttpOnly`、`Secure`、`SameSite=Strict`、`__Host-` 前缀（HTTPS 部署下）；
- 修改密码会签发新会话并吊销其余全部会话；
- CSRF 采用双重提交（`SameSite=Strict` + `X-CSRF-Token` 头比对）；
- 登录限速按账户与 IP 双维度统计失败次数，指数退避（30s 起步，封顶 1h）；
- 登录失败返回统一错误信息，防用户名枚举。

### 初始管理员

首次启动从环境变量（`TAB_BOOTSTRAP_ADMIN` / `TAB_BOOTSTRAP_PASSWORD`）创建管理员，随后明文不再被使用；WebUI 强制提示修改该临时密码。密码本身绝不进入 Git、镜像或日志。

### 上传处理（金融数据）

- 文件保存在服务端生成的随机 ID 路径下，用户文件名只作弱识别信号；
- 解压/解析全程限制：zip 条目数、解压总大小、压缩比、嵌套压缩包、PDF 页数、单文件与请求总大小；
- 拒绝 zip 路径穿越（zip slip）、路径分隔符、控制字符；
- XLSX 解析使用 defusedxml（防 XML 实体扩展）并对内部流限长；
- 默认在任务完成后**立即删除**原始上传与解压明文（保留策略可在设置中调整）；
- 账单密码只在处理请求的内存中存在，永不落库、落盘、进日志。

### AI（可选，默认关闭）

- 只发送脱敏后的商户名/商品名/收支方向；金额、时间、卡号、账号、订单号、余额、姓名不会出境；
- API Key 使用应用密钥（Fernet）加密存储，绝不返回浏览器或写入日志。

### 传输与部署

- 仅支持 HTTPS 部署（文档约定由宿主机 Caddy 终结 TLS）；
- 容器非 root 运行、`no-new-privileges`、支持只读根文件系统，`/data` 为唯一可写卷；
- 安全响应头：CSP（`frame-ancestors 'none'`）、`X-Content-Type-Options: nosniff`、`Referrer-Policy: strict-origin-when-cross-origin`；
- 反向代理场景只信任配置的本机代理地址（默认 `127.0.0.1`），不盲信任意 `X-Forwarded-For`。

### 数据库

SQLite（WAL 模式，外键开启）；所有查询通过 SQLAlchemy ORM 参数化；金额一律整数分存储。

## Known Limitations

- 单用户模型：不适合多人共用一个实例；
- ZipCrypto（支付宝/微信账单 zip 的加密方式）本身是弱加密，这是上游格式限制；建议下载账单后尽快处理并删除；
- 本项目依赖的 OS 基础镜像漏洞由 Debian 安全渠道管理，请定期拉取更新的镜像。

## Scope

TabLedger 处理完整消费记录。仓库自身通过 gitleaks、自定义 PII 扫描（CI 强制）确保不含真实账单数据；测试全部基于完全虚构的合成 fixtures。
