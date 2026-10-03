# Deployment

## 前置条件

- 一台有 Docker 的服务器；
- 宿主机 Caddy（或任意反代）负责 HTTPS 证书；
- 一个指向服务器的域名（示例：`tab.example.com`）。

## 1. 准备目录与密钥

```bash
mkdir -p /opt/tabledger/data && cd /opt/tabledger
```

生成必需的引导密码（**只保存在服务器本地**，不要提交任何仓库）：

```bash
printf 'TAB_BOOTSTRAP_PASSWORD=%s\n' "$(openssl rand -hex 16)" > .env
chmod 600 .env
```

可选：固定 API Key 加密密钥（不设则首次启动自动生成到 `data/secrets/app.key`）：

```bash
echo "TAB_SECRET_KEY=$(openssl rand -hex 32)" >> .env
```

## 2. 启动容器

```bash
docker run -d \
  --name tabledger \
  --restart unless-stopped \
  -p 127.0.0.1:8765:8000 \
  -v "$PWD/data:/data" \
  --env-file .env \
  ghcr.io/paidethon/tabledger:latest
```

或使用仓库自带的 `compose.yml`：

```bash
docker compose up -d
```

容器以非 root 运行，只监听 `127.0.0.1:8765`；对外由 Caddy 反代。

## 3. Caddy 反代

在宿主机 Caddyfile **追加**（不要覆盖其他站点）：

```caddy
tab.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

重载：

```bash
sudo systemctl reload caddy
```

## 4. 首次登录

1. 打开 `https://tab.example.com` → 自动跳转 `/login`；
2. 用 `admin` + `.env` 里的 `TAB_BOOTSTRAP_PASSWORD` 登录；
3. WebUI 提示「当前使用临时弱密码」→ 立即修改（改后引导密码失效，其余会话全部吊销）；
4. 设置 → 账户映射：填入你的银行卡（银行名、卡尾号、显示名称），以及用于内部转账识别的本人姓名；
5. （可选）设置 → AI 分类：填 Provider/Model/API Key，「测试连接」验证。

## 5. 升级

```bash
docker pull ghcr.io/paidethon/tabledger:latest
docker stop tabledger && docker rm tabledger
# 重新 docker run（第 2 步命令）
```

`/data` 卷内的数据库、规则、加密密钥全部保留；启动时自动执行 Alembic 迁移，迁移失败会拒绝启动（不会破坏既有数据）。

## 6. 备份

- 配置（规则/映射/设置）：WebUI 设置 → 导出配置备份；
- 全量：停容器后打包 `data/`（含 SQLite，建议用 `sqlite3 .backup` 或停机窗口）。

## 健康检查与排障

```bash
curl -s http://127.0.0.1:8765/api/v1/health   # {"ok":true,...}
docker logs tabledger                          # 结构化日志（无敏感字段）
```

常见问题：

| 现象 | 处理 |
|------|------|
| 启动即退出，日志出现 migration failed | 检查 `/data` 挂载与磁盘空间；迁移失败不会破坏数据，修复后重启 |
| 登录返回 429 | 登录限速（指数退避），等待对应秒数后再试 |
| 上传报「解压失败（密码不匹配？）」 | 核对账单密码；密码只存在于当次请求 |
| 首页 503 「前端未构建」 | 镜像不完整，重新拉取官方镜像 |
