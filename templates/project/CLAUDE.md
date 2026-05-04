# {{PROJECT_NAME}} 部署 runbook

> 这是一个由 [axioner-deploy](https://github.com/axioner/axioner-deploy) 模板生成的项目部署说明。AI（Claude/Codex/Gemini）读这份文档了解怎么把当前项目部署到 axioner.top。

## 服务器信息

| 项 | 值 |
|---|---|
| SSH alias | `axioner` |
| 服务器 IP | `38.12.23.241` |
| 部署目录 | `/opt/{{PROJECT_NAME}}/` |
| 域名 | `https://{{SUBDOMAIN}}` |
| 本地端口 | `{{PORT}}`（仅 127.0.0.1 监听，nginx 反代） |

## 部署流程

### 首次部署 / 新项目

```bash
# 1. 在仓库根（C:\Code\axioner-deploy）跑预检
python C:\Code\axioner-deploy\scripts\preflight.py \
    --repo <repo url> \
    --subdomain {{SUBDOMAIN}} \
    --port {{PORT}} \
    --json snap.json

# 2. 看输出，确认无 block 冲突（DNS、端口、目录、内存等）
#    若 DNS 未解析 → 去 DNS 后台加 A 记录指向 38.12.23.241

# 3. 执行
python C:\Code\axioner-deploy\scripts\deploy.py --plan-file snap.json
#    会暂停让你 ssh axioner 编辑 /opt/{{PROJECT_NAME}}/.env 填密钥
```

### 更新已部署项目

```bash
ssh axioner "cd /opt/{{PROJECT_NAME}} && git pull && docker compose build && docker compose up -d"
```

### 看日志

```bash
ssh axioner "cd /opt/{{PROJECT_NAME}} && docker compose logs -f --tail=100"
```

### 重启

```bash
ssh axioner "cd /opt/{{PROJECT_NAME}} && docker compose restart"
```

## 危险操作（必须人工确认才能让 AI 执行）

| 操作 | 后果 | 谁能执行 |
|------|------|---------|
| `docker compose down -v` | 删数据卷 | 仅人工 |
| `rm -rf /opt/{{PROJECT_NAME}}` | 删项目目录 | 仅人工 |
| `certbot delete --cert-name {{SUBDOMAIN}}` | 删证书 | 仅人工 |
| 修改 `.env` 真实密钥 | 改密钥 | 仅人工 |
| 修改 `/etc/nginx/nginx.conf`（全局） | 影响所有站点 | 仅人工 |

## AI 不应自动做的事

- 修改 `.env` 中的真实值（占位符替换由你手填）
- 删除 `data/` 目录或其中的 JSON
- 改全局 nginx 配置（仅可写自己 site 文件 `/etc/nginx/sites-{available,enabled}/{{SUBDOMAIN}}`）
- 重启 nginx（只能 `systemctl reload nginx`，不能 stop/start）
- `docker system prune -a`（会删别的项目的镜像）
