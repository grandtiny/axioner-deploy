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

## 设计 + 实施

- 设计文档：[`docs/superpowers/specs/2026-05-04-remote-ai-deploy-design.md`](docs/superpowers/specs/2026-05-04-remote-ai-deploy-design.md)
- 实施计划：[`docs/superpowers/plans/2026-05-04-remote-ai-deploy-implementation.md`](docs/superpowers/plans/2026-05-04-remote-ai-deploy-implementation.md)

## 安全模型

- 信任边界：用户 → 本地 Claude Code → 服务器 root shell
- API key 仅在本地，永远不上服务器
- SSH 走 ed25519 key（密码仅 bootstrap 一次）
- 应用容器仅 `127.0.0.1` 监听，公网入口只有 nginx
- AI 不修改 `.env` 真实值（仅复制 .env.example 占位符版本）
- 私人小项目 + 单运维人，故未配 deploy 用户/UFW（云后台已限端口）

## 不在范围内（参考 spec §8）

多服务器编排 / 蓝绿部署 / 监控告警 / 数据库管理 / CI/CD pipeline / 性能压测 / 镜像仓库 / 日志聚合
