# 已部署项目清单

> 这是 `38.12.23.241` 上的运行清单，对**应用层**而非 axioner-deploy 工具本身。每次新部署或大改后更新这里，CLAUDE.md / README.md 不重复维护。

更新于：2026-05-05

## 在线项目

| 子域 | 端口 | 镜像/容器 | 仓库 | 备注 |
|------|------|---------|------|------|
| `paste.axioner.top` | 3101 | `paste-clipboard` / `paste-clipboard-1` | 本地（无 remote） | Node 剪贴板服务，含 socket.io |
| `netease.axioner.top` | 5000 | `netease-netease-url` / `netease-netease-url-1` | https://github.com/grandtiny/Netease_url（fork 自 Suxiaoqinx/Netease_url） | Flask + aiohttp 网易云解析 API；fork 改了 Dockerfile base 到 python:3.10-alpine；服务器 `/opt/netease/cookie.txt` 是真实 cookie，**未进 git** |

## 端口分配

```text
22, 80, 443    系统 / Nginx
3101           paste（占位符版默认）
5000           netease（项目硬编码）
3102+          下一个新项目可用（preflight 自动从这里选）
```

## 服务器一次性历史变更

| 时间 | 变更 | 来源 |
|------|------|------|
| 2026-05-04 | 加 1G swap (`/swapfile`) + `/etc/fstab` 持久 | `scripts/server-bootstrap.sh` |
| 2026-05-04 | 启用 `PubkeyAuthentication=yes` + 上传公钥 | `scripts/server-bootstrap.sh` + `scripts/local-bootstrap.ps1` |
| 2026-05-05 | 部署 netease（含 docker-compose.override.yml 强制 127.0.0.1 + cookie 卷挂载） | `scripts/deploy.py`（修复后） |

## 维护小动作

```bash
# 看所有应用容器
ssh axioner "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"

# 看资源占用
ssh axioner "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'"

# 看 nginx 子域映射
ssh axioner "ls /etc/nginx/sites-enabled/"

# 看证书有效期
ssh axioner "certbot certificates 2>/dev/null | grep -E 'Certificate Name|Expiry'"
```

## 项目级 CLAUDE.md（每个 /opt/<name>/CLAUDE.md）

每个项目自己的部署 runbook 在 `/opt/<name>/CLAUDE.md`。axioner-deploy `templates/project/CLAUDE.md` 是模板。已部署的项目如果没这个文件，下次维护时顺手补一份是好事。
