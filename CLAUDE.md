# axioner-deploy 给 AI 的使用说明

> 你好。你（Claude / Codex / Gemini）正在 axioner-deploy 仓库工作。这个仓库是 axioner.top 服务器（38.12.23.241）的部署工具集。读完这份文档，你就能帮用户把项目部署到那台服务器。

## 服务器现状（截至 2026-05-04）

| 项 | 值 |
|---|---|
| IP | `38.12.23.241` |
| OS | Ubuntu 24.04 LTS |
| 资源 | 2C / 1.9 GB RAM / 1 GB swap / 20 GB 磁盘 |
| SSH | `ssh axioner`（key auth，无密码） |
| 已部署项目 | `paste`（端口 3101，paste.axioner.top）|
| 反代 | Nginx 1.24 + Certbot |
| 容器运行时 | Docker 29.4 |

## 你能干什么

### A. 部署新项目（标准流程）

用户说："把 https://github.com/foo/bar 部署到 bar.axioner.top" 时：

```bash
# 1. 预检
python scripts/preflight.py \
    --repo https://github.com/foo/bar \
    --subdomain bar.axioner.top \
    --json snap.json
# 看 stdout 报告 + 退出码

# 2. 处理冲突
#    - DNS 未解析: 让用户去 DNS 后台加 A 记录指向 38.12.23.241，加完再跑 preflight
#    - 端口/目录/nginx 冲突: 换名或换端口（preflight 会建议）
#    - 内存 < 200 MB: 让用户决定是否继续（warn 不阻塞）

# 3. 部署
python scripts/deploy.py --plan-file snap.json
#    会暂停让用户填 /opt/<name>/.env（你不要碰 .env）
```

### B. 更新已部署项目

```bash
ssh axioner "cd /opt/<name> && git pull && docker compose build && docker compose up -d"
```

### C. 排查问题

```bash
# 看容器状态
ssh axioner "docker ps -a"

# 看某项目日志
ssh axioner "cd /opt/<name> && docker compose logs --tail=200"

# 看资源
ssh axioner "free -h; df -h; docker stats --no-stream"

# 看 nginx
ssh axioner "nginx -T 2>&1 | grep server_name"
ssh axioner "systemctl status nginx"

# 看证书
ssh axioner "certbot certificates"
```

## 你不能干什么

| 行为 | 为什么 |
|------|------|
| 修改 `.env` 真实值 | 密钥由人手填，AI 永不接触 |
| `docker compose down -v` | 会删数据卷 |
| `rm -rf /opt/<name>` | 删项目要人工二次确认 |
| `certbot delete` | 删证书要人工 |
| 改 `/etc/nginx/nginx.conf` | 全局配置不能动；只能写 `sites-available/<sub>` |
| `systemctl stop` 任何服务 | 只能 `reload`，不能 stop |
| `docker system prune -a` | 会删别项目镜像 |
| 改服务器密码 / 删除 authorized_keys | 你负责的是部署，不是账户管理 |

## 冲突时的协议（必须暂停问用户）

`preflight.py` 已经把这些做成自动检测了，但你也得知道每种情况怎么应对：

| 冲突 | 你应该说 |
|------|---------|
| DNS 未解析或解析错 | "请你去 DNS 后台为 X 加 A 记录指向 38.12.23.241。加完跟我说。" |
| 端口已用 | "拟用端口 3101 已被 paste 占用。建议改成 3102（preflight 会自动避开）。继续？" |
| 目录 `/opt/X` 已存在 | "更新已有？还是新建并换名？" |
| Nginx site 已存在 | "覆盖还是改子域？" |
| 内存 < 200 MB | "服务器内存紧张（剩 X MB）。要不要先停一些容器再继续？" |
| 磁盘 < 1 GB | "磁盘空间不够（剩 X GB）。建议 docker system prune 后再继续。" |
| 私有仓库 git clone 失败 | "需要在服务器配 GitHub deploy key。指引：..." |
| Certbot 失败 | "Certbot 输出：...。多半是 DNS 没生效，等几分钟再试。" |
| .env 含占位符 | "请 ssh axioner 编辑 /opt/X/.env 填入真实密钥，填好回我。" |

## 部署熟练度（spec §3.5）

- **L3** 默认：preflight 输出 plan，给用户看；用户 yes 后 deploy 自动跑 8 步
- **L4** 触发：项目结构标准（含 Dockerfile + docker-compose）、域名已解析、无冲突 → 你可以连预检报告都不用问，直接跑
- **暂停态**：任何冲突 → 立刻停下问用户

## 实施备忘

如果你修改本仓库的代码：

- 主语言：Python 3.11+ + bash + PowerShell
- 单元测试不强求（spec 里跳过了）；端到端测试见 plan Task 14
- commit message 用中文，按 `<类型>: <描述>` 格式（feat / fix / docs / refactor / chore）
- 不在 main 分支直接改；用 `feature/<task-name>` 分支
- 路径用绝对路径（`C:\Code\axioner-deploy\...` 或服务器侧 `/opt/<name>/`），减少歧义
- ANSI 颜色用得当（参考 preflight.py / deploy.py）

## 阅读顺序（如果你刚到这个仓库）

1. 本文件（CLAUDE.md）
2. `README.md`
3. `docs/superpowers/specs/2026-05-04-remote-ai-deploy-design.md`（设计决策来源）
4. `scripts/preflight.py` + `scripts/deploy.py`（核心逻辑）
5. 必要时 `scripts/lib/*.py`（细节）
