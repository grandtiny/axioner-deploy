# 远程 AI 部署系统设计

**日期**: 2026-05-04
**状态**: Draft (待 review)
**作者**: Claude (架构) + Axioner (用户决策)
**实现者**: Codex (后端/脚本)

---

## 1. 背景与目标

### 1.1 背景

用户拥有一台云服务器 `38.12.23.241`（Ubuntu 24.04 / 2C2G / 20GB），上面已部署一个 `paste.axioner.top` 服务（Node 写的剪贴板，跑在 Docker 里，Nginx 反代，Certbot 管 HTTPS）。用户在本地 Windows 用 Claude Code + Superpowers 工作流。

### 1.2 目标

让本地 Claude Code 通过 SSH 远程操作这台服务器，按标准化流程部署新项目，达到 **L3-L4 自主度**：

- AI 自动跑完已知部署流程（git clone、docker compose、nginx 配、certbot）
- 遇到冲突（端口、目录、DNS、内存等）暂停问用户
- 不让 AI 触碰敏感数据（如 `.env` 真实密钥由用户填）

### 1.3 非目标

- ❌ 不上 Kubernetes / 集群（2G 机器扛不住，私人项目用不上）
- ❌ 不做 CI/CD pipeline（push 不触发部署，由用户主动让 AI 部署）
- ❌ 不重构现有 paste 服务（保留运行）
- ❌ 不做监控告警系统（小项目，过度工程）
- ❌ 不在服务器上跑额外的 AI agent（AI 留在本地，服务器只跑应用）

---

## 2. 现状（2026-05-04 侦察结果）

### 2.1 服务器基线

| 维度 | 值 | 评价 |
|------|----|----|
| OS | Ubuntu 24.04.1 LTS (Noble) | 主流，软件支持齐全 |
| 内核 | 6.8.0-48 | 新 |
| CPU | 2 核 | 够用 |
| 内存 | 1.9 GB（当前 used 484 MB / avail 1.4 GB） | 紧但够 |
| Swap | **0 字节** | ⚠️ 风险点 |
| 磁盘 | 20 GB（5.1 GB used / 15 GB free） | 充裕 |
| 防火墙 | UFW inactive；云后台已限端口 | 由云后台兜底 |
| Uptime | 20 天 | 稳定 |

### 2.2 已部署服务

```
Nginx 1.24.0
  ├─ 0.0.0.0:80    → 301 https
  └─ 0.0.0.0:443   → /etc/nginx/sites-enabled/paste.axioner.top
                    → proxy_pass http://127.0.0.1:3101

Docker 29.4.1
  └─ paste-clipboard-1   127.0.0.1:3101
                         (Node 18, 32 MB 内存占用)

Certbot   /etc/letsencrypt/live/paste.axioner.top/

Cron      certbot 自动续期
```

### 2.3 已观察到的项目模式（来自 `/opt/paste/`）

```
/opt/paste/                       ← git init 但无 commit、无 remote（瑕疵）
└── paste/                        ← 实际应用代码（嵌套了一层）
    ├── Dockerfile                ← FROM node:18-alpine + npm ci
    ├── docker-compose.yml        ← build:.; 127.0.0.1 端口绑定; .env; restart:unless-stopped
    ├── .env.example              ← 进 git
    ├── .env                      ← 不进 git
    ├── .dockerignore
    ├── .claude/settings.local.json   ← 已配 Bash allowlist
    ├── docs/superpowers/{specs,plans}/   ← 已用 superpowers
    ├── server/{index.js, auth.js, storage.js}
    ├── public/{index.html, app.js, style.css}
    ├── data/clipboard.json
    └── logs/
```

**Nginx site 模板（来自 paste.axioner.top）**：
- 80 端口由 Certbot 加的 301 → https
- 443 反代 `http://127.0.0.1:<port>`
- 含 WebSocket 升级头（`Upgrade` / `Connection`，paste 用 socket.io）
- `proxy_read_timeout` / `proxy_send_timeout` 86400（长连接）
- SSL 证书 + 参数全由 Certbot 管

### 2.4 现状瑕疵（需要在新规范里修正）

1. `/opt/paste/.git` 没有 remote 也没有 commit，git 只是空架子——未来项目走 GitHub/Gitee
2. 应用代码套了两层 `/opt/paste/paste/`——新规范展平为 `/opt/<name>/`
3. 没有 swap、没有标准化 deploy 脚本

---

## 3. 设计

### 3.1 架构

```
本地 Windows                              服务器 38.12.23.241
C:\Users\Axioner\                         Ubuntu 24.04 / 2C2G
                                          
┌──────────────────────────┐              ┌────────────────────────┐
│ Claude Code (主控)       │              │  Nginx 1.24            │
│ ─ API key 留本地         │              │   ├─ paste.axioner.top │
│ ─ ssh axioner            │   SSH key    │   ├─ <new>.axioner.top │
│   (key auth)             │ ──────────→  │   └─ ...               │
│                          │              │     ↓ proxy_pass       │
│ 调度:                    │              │   127.0.0.1:31xx       │
│ ─ /ask codex (后端)      │              │                        │
│ ─ /ask gemini (前端)     │              │  Docker (各项目独立)   │
└──────────────────────────┘              │   /opt/<name>/         │
        ↑                                 │                        │
        │ git clone                       │  Certbot HTTPS         │
        │ (从 GitHub/Gitee)               └────────────────────────┘
        └─────────────────────┐                    ↑
                              ↓                    │
                     ┌────────────────┐            │
                     │ GitHub / Gitee │ ───────────┘
                     │ (代码托管)     │
                     └────────────────┘

DNS: axioner.top（用户在某 DNS 服务商手动管理，AI 仅提示）
```

### 3.2 服务器一次性变更

唯一要做的服务器修改：

| 项目 | 命令（参考） | 理由 |
|------|------|------|
| 加 1G swap | `fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab` | 防运行期 OOM；100MB 磁盘代价 |
| 上传 SSH 公钥 | 本地生成 ed25519 → 公钥写入 `/root/.ssh/authorized_keys` | AI 走 key 而不是密码，更安全 |

**显式拒绝以下变更**（这些是常见加固但本场景不需要）：

- ❌ UFW / iptables：云后台已限端口出入
- ❌ fail2ban：同上
- ❌ 创建 deploy 用户 + sudo 白名单：私人小项目，root 简单
- ❌ 改 SSH 端口：云后台限制了，本身就不用纠结
- ❌ 安装监控（Prometheus / Netdata 等）：YAGNI

### 3.3 项目模板

每个新项目仓库（在用户本地 + GitHub/Gitee）的标准结构：

```
<project>/                              ← git 仓库根目录
├── Dockerfile                          ← 必须
├── docker-compose.yml                  ← 必须
├── .env.example                        ← 必须，进 git
├── .env                                ← 不进 git
├── .dockerignore                       ← 必须
├── .gitignore                          ← 必须
├── README.md
├── CLAUDE.md                           ← 部署 runbook（教 AI 部署到 axioner）
├── data/                               ← 持久化卷（按需）
├── logs/                               ← 日志卷（按需）
└── (项目源码)
```

**Dockerfile 模板（Node 类项目）**：

```dockerfile
FROM node:<ver>-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
EXPOSE <port>
CMD ["node", "server/index.js"]
```

**docker-compose.yml 模板**：

```yaml
version: '3.8'
services:
  app:
    build: .
    ports:
      - "127.0.0.1:<port>:<port>"      # 仅 localhost 监听，nginx 反代
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - NODE_ENV=production
      - PORT=<port>
      # 其余 env 走 .env
    env_file:
      - .env
    restart: unless-stopped
```

**Nginx site 模板** `/etc/nginx/sites-available/<sub>.axioner.top`：

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name <sub>.axioner.top;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name <sub>.axioner.top;

    # 由 certbot --nginx 自动注入 ssl_certificate / ssl_certificate_key
    # 由 certbot 自动 include /etc/letsencrypt/options-ssl-nginx.conf

    location / {
        proxy_pass http://127.0.0.1:<port>;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }
}
```

**端口分配规则**：

- 已用：3101（paste-clipboard）
- 新项目：从 3102 开始往后递增
- AI 部署前必须 `ss -tlnp | grep :<port>` 验证
- 仅 `127.0.0.1` 监听，公网不直接暴露

**目录约定**：

- 应用代码：`/opt/<name>/`（**不再嵌套**，与 paste 现状不同）
- 不修改：`/var/www/`、`/srv/`、`/home/`、`/root/`（除 `/root/.ssh/`）

### 3.4 部署协议（4-phase）

**触发**：用户在本地 Claude Code 说一句类似的话：

> "帮我部署 https://github.com/foo/bar 到 bar.axioner.top"

或：

> "更新 paste 服务到最新"

#### Phase 1: Preflight（自动，只读）

AI 自动执行的检查，**不改动任何状态**：

```
服务器侧（通过 ssh axioner）：
├─ free -h                                    内存余量
├─ df -h /                                    磁盘余量
├─ ss -tlnp                                   已用端口
├─ ls /opt/                                   已部署项目
├─ ls /etc/nginx/sites-enabled/               已用子域
├─ docker ps -a                               已有容器
└─ swapon --show                              swap 状态

本地侧 / DNS：
├─ 仓库结构: 有 Dockerfile? docker-compose.yml? .env.example?
└─ DNS 查询: nslookup <sub>.axioner.top → 38.12.23.241?
```

#### Phase 2: Plan（输出给用户，等确认）

输出一份明确的部署计划：

```
拟部署:    bar
仓库:      https://github.com/foo/bar
域名:      bar.axioner.top
  DNS:     ⚠️  未解析 → 你先去 DNS 后台加 A 记录指向 38.12.23.241
端口:      3102 (空闲；3101 = paste)
目录:      /opt/bar/  (不存在，可创建)
资源预估:  ≈80 MB 内存 (Node 类)
当前余量:  内存 1.4 G 可用 / 磁盘 15 G 可用 ✓

执行步骤:
  1. ssh + git clone
  2. ⚠️  你填 .env (cp .env.example .env，敏感数据 AI 不碰)
  3. docker compose build && docker compose up -d
  4. 写 nginx site config (基于 paste 模板，替换占位符)
  5. nginx -t && systemctl reload nginx
  6. certbot --nginx -d bar.axioner.top
  7. curl https://bar.axioner.top → 健康检查

冲突清单:
  ✗ DNS 未解析 → 阻塞 (Phase 3 step 6 会失败)
  ✓ 端口 3102 空闲
  ✓ 目录 /opt/bar 不存在

继续？(yes / 修改 / 取消)
```

#### Phase 3: Execute（用户确认后自动执行）

按步骤跑，**任何步骤失败立即停下**：

- 失败 → 立即报告（错误日志、已完成步骤、未完成步骤）
- 失败 → 默认不回滚（避免破坏更多状态），等用户决策

#### Phase 4: Report

```
✓ 部署成功: https://bar.axioner.top
  ├─ 容器: bar-app-1 (Up, 56 MB / 1.9 G)
  ├─ Nginx: bar.axioner.top → 127.0.0.1:3102
  ├─ 证书: 有效期至 2026-08-02
  └─ 健康检查: 200 OK

后续 TODO (留给你):
  ├─ [ ] 填 /opt/bar/.env 中的 SECRET_KEY (当前是 .env.example 的占位符)
  └─ [ ] 验证业务功能
```

### 3.5 自主度等级（L3-L4）

```
L1: AI 给命令，用户手动执行                     [本系统不采用]
L2: AI 直接执行，每条命令前问用户               [太啰嗦]
L3: AI 按部署模板自动跑完整流程，结束后报告     ← 本系统的常态
L4: AI 接到模糊需求自己想办法搞定              ← 本系统在简单情况下
冲突态: AI 暂停问用户                          ← 本系统的关键安全保险
```

具体地：

- **L4 时机**：项目结构标准（有 Dockerfile + docker-compose）、域名已解析、端口无冲突 → AI 自己跑完
- **L3 时机**：默认情况，Phase 2 输出 plan 等用户确认后再 Phase 3
- **冲突态**：见下表

### 3.6 冲突清单（必须暂停问用户）

| 冲突 | 来源 | AI 应做 |
|------|------|------|
| DNS 未解析到 38.12.23.241 | Phase 1 nslookup | 暂停，提示用户去 DNS 后台加 A 记录，等用户回复 "已加" 后重试 nslookup |
| 端口已被其他容器占用 | Phase 1 ss -tlnp | 提议下一个空闲端口，等用户确认或指定 |
| 目录 `/opt/<name>` 已存在 | Phase 1 ls /opt | 暂停，问"更新现有 / 重命名 / 取消" |
| 内存可用 < 200 MB | Phase 1 free | 暂停，列出占内存的进程让用户裁决 |
| 磁盘可用 < 1 GB | Phase 1 df | 暂停，提示清理（旧镜像、日志） |
| Nginx site config 已存在 | Phase 1 ls sites-enabled | 暂停，问覆盖还是新建 |
| `.env` 需要密钥但 .env.example 中是占位符 | Phase 3 step 2 | 暂停，让用户手动填 .env |
| 仓库是私有的（git clone 失败） | Phase 3 step 1 | 暂停，提示需要在服务器配 GitHub deploy key 或换公开仓库 |
| Certbot 申请失败（DNS 没解析或 80 端口不通） | Phase 3 step 6 | 暂停，给出 Certbot 完整错误日志 |

### 3.7 安全考量

**信任边界**：

```
用户 (人) ──信任──> Claude Code (本地, 含 API key)
   │                    │
   │                    │ 信任 (SSH key auth)
   │                    ↓
   │                 服务器 root shell
   │                    │
   │                    │ 包含
   │                    ↓
   │              所有数据（含 .env 真实密钥）
   │
   └──信任──> GitHub/Gitee (仓库托管)
```

**关键点**：

1. **API key 只在本地**：服务器永远没有 Anthropic API key，AI 进程不在服务器上
2. **SSH key 而非密码**：用户密码登录后续仅用于紧急维护
3. **`.env` 真实值不进 git**、不让 AI 写：AI 只写 `.env.example`，真实密钥由用户手动 `cp .env.example .env && vi .env`
4. **localhost 端口绑定**：所有应用容器只在 `127.0.0.1` 监听，nginx 是唯一对外入口
5. **Certbot 走标准 ACME**：无需把私钥贴进任何配置

**已识别的风险与缓解**：

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| AI 误删 `/opt/<other>/`（非当前部署项目）| 低 | 高 | 冲突清单第 3 项；执行前的 plan 阶段会列出涉及目录 |
| AI 把 `.env` 真实值写进 git | 中 | 高 | 模板 `.dockerignore` + `.gitignore` 必含 `.env`；CLAUDE.md 明确写 "AI 永不修改 .env" |
| SSH key 泄漏 | 低 | 高 | key 只在本地 `~/.ssh/`；本地是单人 Windows |
| 部署期间老服务被替换出错 | 中 | 中 | docker compose 默认是 stop-then-up，会有几秒中断；不在范围内做零停机 |

### 3.8 常用运维操作（AI 应支持的）

除了"部署新项目"，AI 还应能处理：

- **更新现有项目**：`cd /opt/<name> && git pull && docker compose build && docker compose up -d`
- **查看日志**：`docker compose logs -f --tail=100 <service>`
- **重启项目**：`docker compose restart`
- **看资源占用**：`docker stats --no-stream`
- **删除项目**：`docker compose down -v && rm -rf /opt/<name> && rm /etc/nginx/sites-enabled/<name>.axioner.top && systemctl reload nginx`（删除前必须人工确认）
- **续证书**：cron 已在跑，AI 不需要手动操作；但要会查 `certbot certificates`

---

## 4. 实现产物清单

### 4.1 服务器一次性

- `/swapfile` (1 G)
- `/etc/fstab` 增一行 swap 配置
- `/root/.ssh/authorized_keys` 增用户公钥

### 4.2 用户本地（Windows）

- `~/.ssh/axioner_ed25519` + `.pub`
- `~/.ssh/config` 增 alias `axioner`
- `~/.ssh/known_hosts` 增服务器主机指纹

### 4.3 本仓库（C:\Users\Axioner\Code\axioner-deploy\）

```
axioner-deploy/
├── docs/superpowers/specs/
│   └── 2026-05-04-remote-ai-deploy-design.md   ← 本文件
├── docs/superpowers/plans/
│   └── 2026-05-04-remote-ai-deploy-implementation.md  ← writing-plans 阶段产生
├── scripts/
│   ├── server-bootstrap.sh                      ← 一次性服务器加固（swap + ssh key）
│   ├── local-bootstrap.ps1                      ← 一次性本地配置（生成 key + ssh config）
│   ├── preflight.py                             ← Phase 1 自动检查
│   ├── deploy.py                                ← Phase 3 执行（按 plan 跑）
│   └── lib/                                     ← 共享 SSH 客户端、配置等
├── templates/
│   ├── project/                                 ← 新项目骨架
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   ├── .env.example
│   │   ├── .dockerignore
│   │   ├── .gitignore
│   │   ├── CLAUDE.md
│   │   └── README.md
│   └── nginx/
│       └── site.conf                            ← Nginx site 模板（占位符版）
├── README.md                                    ← 本仓库的总入口
└── CLAUDE.md                                    ← 教 AI 怎么用本仓库的脚本
```

### 4.4 任务分配

| 产物 | 实现者 | 备注 |
|------|------|------|
| spec / plan 文档 | Claude (架构) | |
| `scripts/*.py`、`scripts/*.sh` | Codex (后端) | 通过 `/ask codex` 委派 |
| `templates/*` | Codex (后端) | 模板就是文件 |
| 项目自身的 README/CLAUDE.md | Claude | 文档 |

---

## 5. 决策记录（ADR）

| # | 决策 | 选择 | 拒绝的备选 | 理由 |
|---|------|------|----------|------|
| 1 | AI 位置 | 本地 | 服务器 / 混合 agent | 2G 内存吃紧；API key 留本地更安全；延迟更低 |
| 2 | 反代 | 沿用 Nginx | 切 Caddy | 用户已会 Nginx 且已配好；切换成本无收益 |
| 3 | HTTPS | 沿用 Certbot | 自签 / Cloudflare proxy | 已经配好且自动续；不动 |
| 4 | 容器化 | Docker + compose | 直接系统服务 / k8s | compose 简单；k8s 资源不够 |
| 5 | 服务器加固 | 仅加 swap | + UFW + fail2ban + deploy 用户 | 云后台已限端口；私人项目 root 即可 |
| 6 | 代码传输 | git clone (GitHub/Gitee) | rsync / scp | 标准、可追溯、易更新 |
| 7 | DNS 自动化 | 手动加 A 记录 | Cloudflare API 自动 | 减少 token 暴露面；用户只在新项目时偶尔加 |
| 8 | 自主度 | L3-L4 + 冲突暂停 | L1 全人工 / L2 步步问 / L4 全自动 | L4 太险 / L1-L2 太累 |
| 9 | 端口 | 127.0.0.1:31xx 起 | 0.0.0.0 公网监听 | 仅 nginx 对外，多一层防御 |
| 10 | swap 大小 | 1 G | 2 G / 0 G | 折中；够缓冲突发但不太占盘 |

---

## 6. 开放问题

| # | 问题 | 何时决定 |
|---|------|------|
| 1 | DNS 服务商具体是哪家 | 第一次新项目部署时让 AI 提示具体平台 |
| 2 | 私有仓库需要时如何配 GitHub deploy key | 遇到第一个私有仓库时再加章节 |
| 3 | 是否要做"健康检查端点约定"（每个应用都要 `/healthz`） | 暂不强制；后续若有 N 个项目再标准化 |
| 4 | 是否把 `paste.axioner.top` 按新规范展平（去掉嵌套） | 当前不做（避免破坏运行）；下次大改 paste 时顺带 |
| 5 | 多项目共用的全局日志方案 | YAGNI，N 上来再说 |

---

## 7. 验收标准

实施完成的判定：

- [ ] 服务器 `swapon --show` 显示 1 G swap
- [ ] 本地 `ssh axioner` 不输入密码即可登录
- [ ] `paste.axioner.top` 仍正常访问（回归测试，不被破坏）
- [ ] 给定一个测试仓库（含 Dockerfile + docker-compose.yml + .env.example）：
  - [ ] AI 跑完 Phase 1（输出预检报告）
  - [ ] AI 输出 Phase 2 plan（端口、目录、冲突等齐全）
  - [ ] 用户 yes 后 AI 跑完 Phase 3，部署成功
  - [ ] AI 输出 Phase 4 报告，含健康检查结果
- [ ] 故意制造冲突（占用拟用端口）：
  - [ ] AI 在 Phase 1 检测到冲突
  - [ ] AI 在 Phase 2 提议替代端口并暂停
  - [ ] 用户确认后 AI 用新端口继续
- [ ] 故意不解析 DNS：
  - [ ] AI 在 Phase 1 检测到
  - [ ] AI 在 Phase 2 暂停并提示用户去手动加 A 记录

---

## 8. 不在范围内（Out of Scope）

为避免范围蔓延，明确以下**不**做：

1. 多服务器编排
2. 蓝绿部署 / 金丝雀 / 零停机
3. 监控告警系统
4. 数据库管理（备份、迁移）
5. 用户管理 / 多租户
6. 镜像仓库（私有 registry）
7. 日志聚合（ELK / Loki 等）
8. CI/CD 流水线
9. 性能压测
10. 自动扩容

需要时另起新 spec。
