# axioner-deploy

> 远程 AI 部署系统：让本地 Claude Code（或 Codex / Gemini CLI）通过 SSH 把 GitHub/Gitee 上的项目按标准化流程部署到 `38.12.23.241`（axioner.top 主服务器）。

## 这是什么

一台 2C2G 的云服务器，跑若干私人小项目（Node/Python web、API、机器人、脚本等）。每个项目：

- 在 `<name>.axioner.top` 子域提供服务
- 跑在独立的 Docker 容器里，数据卷 `/opt/<name>/data,logs`
- Nginx 反代 + Certbot 自动 HTTPS
- 仅 `127.0.0.1:31xx` 监听，nginx 是唯一对外入口

本仓库提供：

1. **一次性 bootstrap 脚本**：本地 SSH key 配置 + 服务器加固（swap + PubkeyAuth）
2. **预检 CLI** `preflight.py`：扫服务器现状 + DNS + 仓库结构，输出 plan + 冲突清单
3. **部署 CLI** `deploy.py`：执行 8-step 部署流程，遇 block 冲突暂停
4. **项目模板** `templates/project/`：Dockerfile / docker-compose.yml / .env.example / CLAUDE.md
5. **Nginx 模板** `templates/nginx/site.conf`

## 目录结构

```
axioner-deploy/
├── docs/superpowers/
│   ├── specs/2026-05-04-remote-ai-deploy-design.md         设计文档
│   └── plans/2026-05-04-remote-ai-deploy-implementation.md 实施计划
├── scripts/
│   ├── lib/
│   │   ├── ssh_client.py        paramiko 包装，读 ~/.ssh/config
│   │   ├── server_state.py      收集服务器现状
│   │   ├── conflict_detector.py 6 类冲突检测
│   │   └── plan_io.py           DeployPlan 数据结构 + JSON IO
│   ├── preflight.py             Phase 1 预检 CLI
│   ├── deploy.py                Phase 3 + 4 执行 CLI
│   ├── server-bootstrap.sh      服务器加固（swap + PubkeyAuth）
│   ├── local-bootstrap.ps1      Windows 本地一次性配置
│   └── _install_pubkey.py       PowerShell 调用的 paramiko helper
├── templates/
│   ├── project/                 新项目骨架（8 个文件）
│   └── nginx/site.conf          子域反代模板
├── pyproject.toml               依赖：paramiko 4.x
├── README.md                    本文件
└── CLAUDE.md                    AI 怎么用本仓库
```

## 一次性配置

只需在初次接入或换电脑/换服务器时跑：

### 本地（Windows / PowerShell）

```powershell
cd C:\Code\axioner-deploy
pip install -e .                            # 装 paramiko 依赖
.\scripts\local-bootstrap.ps1 -BootstrapPassword '<服务器 root 密码>'
# 完成后：
#   ~/.ssh/axioner_ed25519(.pub) 已生成
#   ~/.ssh/config 已加 Host axioner 别名
#   服务器 ~/.ssh/authorized_keys 已含本机公钥
#   ssh axioner 免密成功
```

### 服务器（一次性，root）

```bash
scp scripts/server-bootstrap.sh axioner:/tmp/
ssh axioner 'bash /tmp/server-bootstrap.sh'
# 完成后：
#   /swapfile 1G 已 active 且持久化
#   sshd PubkeyAuthentication=yes
```

## 日常使用

每次部署新项目：

```bash
# 1. 预检
python scripts/preflight.py \
    --repo https://github.com/axioner/<repo> \
    --subdomain <sub>.axioner.top \
    --json snap.json

# 2. 看输出。若 DNS 未解析 → 去 DNS 后台加 A 记录指向 38.12.23.241

# 3. 部署
python scripts/deploy.py --plan-file snap.json
# 中途会暂停让你 ssh 上去填 /opt/<repo>/.env 真实密钥
```

更新已部署项目（不需要 deploy.py）：

```bash
ssh axioner "cd /opt/<name> && git pull && docker compose build && docker compose up -d"
```

## 部署真实第三方项目时常见的事

> 这一节是从首次部署一个真实仓库（`Suxiaoqinx/Netease_url`）时发现的踩坑总结。

### 1. 项目自带的 `docker-compose.yml` 端口未必合规

很多项目写 `ports: ["5000:5000"]`，等同 `0.0.0.0:5000:5000`，绕过了我们"仅本机绑定"的约定。

**deploy.py 已自动处理**：在 git clone 后会读项目自带的 `docker-compose.yml`，提取 service 名 + 实际端口，并生成一份 `docker-compose.override.yml`：

```yaml
services:
  <service>:
    ports: !override
      - "127.0.0.1:<port>:<port>"
```

`docker compose` 会自动 merge override + base，`!override` tag 让 ports 字段是替换而不是追加。

注意：你执行 `--port 5000` 时给的是**项目实际监听端口**，不是 axioner-deploy 自己分配的端口。如果项目内部硬编码了端口（比如 entrypoint 里写死 `--url http://127.0.0.1:5000`），preflight 自动选的端口（默认 3102+）没用，还是要手动 `--port` 跟项目对齐。

### 2. Cloudflare 代理（橙云）下 DNS 解析

如果你的域名挂在 Cloudflare 且开了代理，子域名 A 记录会解析到 Cloudflare 的 anycast IP（`104.16.*` / `172.64.*` 等），而不是源站 `38.12.23.241`。

**preflight 已识别**：内置 Cloudflare 公开的 IPv4 ranges 列表。命中时报告里会显示 `⚠ Cloudflare 代理 <IP>`（黄色 warn，不阻塞 deploy）。Certbot 用 HTTP-01 challenge 通常能透过代理拿证书；万一不行，临时把 Cloudflare 后台那条 A 记录的代理状态改成"灰云"（仅 DNS）→ 跑完 certbot 拿到证书 → 改回橙云。

### 3. 上游 Dockerfile 与 requirements 不兼容

实测 `Suxiaoqinx/Netease_url` 的 Dockerfile 用 `python:3.9.22-alpine3.21`，但 `requirements.txt` 里 `click==8.2.1` 等需要 Python ≥ 3.10。docker build 会失败：

```text
ERROR: Could not find a version that satisfies the requirement click==8.2.1
```

**解决**：fork 上游仓库，改 Dockerfile 第一行为 `FROM python:3.10-alpine`，从你 fork 的 repo 部署。这样以后 git pull 不会冲突。如果对 fork 不放心改坏，也可以在服务器 `/opt/<name>/` 加一份 `Dockerfile.local` + 在 override 里指向它，但维护比 fork 麻烦。

### 4. 项目把 cookie/secrets 直接 commit 进 git

`Suxiaoqinx/Netease_url` 的仓库里就有一份 `cookie.txt`（上游作者的 sample，早已失效）。所以 fork 之后服务器上拿到的也是这个 sample。

**两件事要做**：
- **挂卷而非 build-in**：在 `docker-compose.override.yml` 里加 `volumes: - ./cookie.txt:/app/cookie.txt:ro`，让宿主机 cookie 优先于镜像里的。否则 `docker compose restart` 不重 build，永远用旧的。
- **不要把真实 cookie 推回 fork**：直接在服务器 `vim /opt/<name>/cookie.txt`，或本地 paramiko `write_file` 进去。fork 里那个 sample 不删除也无所谓（它本来就失效）。

### 5. 健康检查端点的 valid/invalid 不一定阻塞功能

`Netease_url` 的 `/health` 显示 `cookie_status: invalid`，但实际 API 调用（搜索、URL 解析、lossless）能正常工作。它的 `is_cookie_valid()` 要求六个字段全有（`MUSIC_U` `MUSIC_A` `__csrf` 等），但**实际网易云接口只校验 `MUSIC_U`**。所以 health 报 invalid 不代表必须修，要看真实 API 是否能返回数据。

## 设计 + 实施

- 设计文档：[`docs/superpowers/specs/2026-05-04-remote-ai-deploy-design.md`](docs/superpowers/specs/2026-05-04-remote-ai-deploy-design.md)
- 实施计划：[`docs/superpowers/plans/2026-05-04-remote-ai-deploy-implementation.md`](docs/superpowers/plans/2026-05-04-remote-ai-deploy-implementation.md)
- **运行现状**：[`docs/deployments.md`](docs/deployments.md)（已部署项目清单 / 端口分配 / 服务器变更历史）

## 安全模型

- 信任边界：用户 → 本地 Claude Code → 服务器 root shell
- API key 仅在本地，永远不上服务器
- SSH 走 ed25519 key（密码仅 bootstrap 一次）
- 应用容器仅 `127.0.0.1` 监听，公网入口只有 nginx
- AI 不修改 `.env` 真实值（仅复制 .env.example 占位符版本）
- 私人小项目 + 单运维人，故未配 deploy 用户/UFW（云后台已限端口）

## 不在范围内（参考 spec §8）

多服务器编排 / 蓝绿部署 / 监控告警 / 数据库管理 / CI/CD pipeline / 性能压测 / 镜像仓库 / 日志聚合
