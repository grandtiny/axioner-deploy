# 远程 AI 部署系统 - 实施计划

> **For agentic workers:** 本计划由 Claude (架构) 生成，由 Codex (后端) 执行。Claude 负责审核每阶段产出，不直接写代码。步骤用 `- [ ]` 跟踪进度。
>
> **任务委派**：通过 `/ask codex "..."` 把任务交给 Codex；Codex 不可用则降级 Gemini。

**Goal**：让本地 Claude Code 通过 SSH 把 GitHub/Gitee 上的项目按标准化流程部署到 `38.12.23.241`，达到 L3-L4 自主度，遇冲突暂停问用户。

**Architecture**：本地 Python 脚本（`preflight.py` 做只读预检 → `deploy.py` 做执行）通过 `paramiko` SSH 控制服务器；项目模板（Dockerfile / docker-compose / nginx / CLAUDE.md）放本仓库，AI 部署新项目时拷贝并填占位符。

**Tech Stack**：
- Python 3.13 + paramiko 4.0（SSH 客户端）
- bash（服务器侧加固脚本）
- PowerShell（Windows 本地一次性配置）
- Docker / Docker Compose（应用运行时）
- Nginx + Certbot（反代 + HTTPS）

**关键设计依据**：见 [`../specs/2026-05-04-remote-ai-deploy-design.md`](../specs/2026-05-04-remote-ai-deploy-design.md)

---

## 文件结构（File Structure）

整个仓库实施完成后的结构：

```
axioner-deploy/
├── docs/superpowers/
│   ├── specs/2026-05-04-remote-ai-deploy-design.md     ← 已存在
│   └── plans/2026-05-04-remote-ai-deploy-implementation.md  ← 本文件
├── scripts/
│   ├── lib/
│   │   ├── __init__.py
│   │   ├── ssh_client.py          ← paramiko 包装；读 ~/.ssh/config 拿连接信息
│   │   ├── server_state.py        ← 收集服务器现状（mem/disk/ports/opt/sites/docker）
│   │   ├── conflict_detector.py   ← 给定 plan + state 返回冲突清单
│   │   └── plan_io.py             ← 部署 plan 的 JSON 序列化（preflight 输出 → deploy 输入）
│   ├── preflight.py               ← CLI: Phase 1 预检
│   ├── deploy.py                  ← CLI: Phase 3 执行 + Phase 4 报告
│   ├── server-bootstrap.sh        ← 服务器一次性加固（swap）
│   └── local-bootstrap.ps1        ← Windows 本地一次性配置（生成 ssh key + config）
├── templates/
│   ├── project/                   ← 新项目骨架（Codex 部署时复制+填占位符）
│   │   ├── Dockerfile.node        ← Node 类项目
│   │   ├── Dockerfile.python      ← Python 类项目
│   │   ├── docker-compose.yml
│   │   ├── .env.example
│   │   ├── .dockerignore
│   │   ├── .gitignore
│   │   ├── CLAUDE.md              ← 教 AI 怎么部署到 axioner
│   │   └── README.md
│   └── nginx/
│       └── site.conf              ← Nginx site 模板（占位符版）
├── README.md                      ← 仓库总入口
├── CLAUDE.md                      ← 教 AI 怎么用本仓库的脚本
├── .gitignore
└── pyproject.toml                 ← 依赖锁定 paramiko
```

**模块边界 / 接口约定**：

| 模块 | 输入 | 输出 | 依赖 |
|------|------|------|------|
| `ssh_client.SSHClient(alias)` | ~/.ssh/config 别名 | run(cmd) → (exit_code, stdout, stderr) | paramiko |
| `server_state.collect(client)` | SSHClient 实例 | ServerState dataclass | ssh_client |
| `conflict_detector.detect(plan, state)` | DeployPlan + ServerState | list[Conflict] | server_state |
| `preflight.py main` | argv: --repo --subdomain [--port] | stdout: 人类可读报告；--json: machine-readable plan | 上面三个 |
| `deploy.py main` | argv: --plan-file (preflight --json 输出) | stdout: 执行日志 + Phase 4 报告 | ssh_client + plan_io |

**接口数据结构**（关键）：

```python
# scripts/lib/plan_io.py
@dataclass
class DeployPlan:
    project_name: str
    repo_url: str
    subdomain: str          # e.g. "bar.axioner.top"
    port: int               # e.g. 3102
    target_dir: str         # e.g. "/opt/bar"
    container_name: str
    confirmed_by_user: bool

@dataclass
class ServerState:
    mem_avail_mb: int
    disk_avail_gb: int
    used_ports: list[int]
    project_dirs: list[str]      # /opt 下已有项目
    nginx_sites: list[str]       # sites-enabled 下的子域
    containers: list[str]        # docker ps 名字
    swap_mb: int

@dataclass
class Conflict:
    kind: Literal["dns", "port", "dir", "memory", "disk", "nginx", "env", "private_repo", "certbot"]
    severity: Literal["block", "warn"]
    message: str
    suggested_action: str
```

---

## Chunk 1：Phase 1 底座（服务器加固 + 本地 SSH 配置）

**目标**：在不动现有 paste 服务的前提下，完成：
- 服务器加 1G swap
- 本地有 ed25519 SSH key
- 本地 `ssh axioner` 免密登录服务器

**完成判定**：本地命令行 `ssh axioner whoami` 返回 `root` 且不询问密码；服务器上 `swapon --show` 显示 1G swap。

---

### Task 1: 创建仓库基线文件（.gitignore / pyproject / README 雏形）

**Files**:
- Create: `C:\Code\axioner-deploy\.gitignore`
- Create: `C:\Code\axioner-deploy\pyproject.toml`
- Create: `C:\Code\axioner-deploy\README.md`

- [ ] **Step 1.1**: 创建 `.gitignore`，内容覆盖 Python / IDE / OS：

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/
*.egg-info/
.pytest_cache/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
desktop.ini

# 临时
*.log
*.tmp
.env
```

- [ ] **Step 1.2**: 创建 `pyproject.toml`，锁定 Python 依赖：

```toml
[project]
name = "axioner-deploy"
version = "0.1.0"
description = "Remote AI deployment system for axioner.top"
requires-python = ">=3.11"
dependencies = [
    "paramiko>=4.0,<5.0",
]

[project.optional-dependencies]
dev = [
    "ruff>=0.5",
]

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 1.3**: 创建 `README.md`，简单介绍：

```markdown
# axioner-deploy

远程 AI 部署系统。本地 Claude Code 通过 SSH 把项目部署到 `38.12.23.241`。

设计文档：[`docs/superpowers/specs/2026-05-04-remote-ai-deploy-design.md`](docs/superpowers/specs/2026-05-04-remote-ai-deploy-design.md)

实施计划：[`docs/superpowers/plans/2026-05-04-remote-ai-deploy-implementation.md`](docs/superpowers/plans/2026-05-04-remote-ai-deploy-implementation.md)

## 用法（实施完毕后）

新项目部署：
1. 在你的项目目录里 `claude` 启动 Claude Code
2. 说："帮我部署到 <子域>.axioner.top"
3. 看 Phase 2 plan，确认后等结果

详见 [`CLAUDE.md`](CLAUDE.md)（实施完后会创建）
```

- [ ] **Step 1.4**: commit

```bash
cd /c/Code/axioner-deploy
git add .gitignore pyproject.toml README.md
git commit -m "chore: 添加仓库基线文件（gitignore + pyproject + README）"
```

---

### Task 2: 编写本地 bootstrap PowerShell 脚本

**Files**:
- Create: `C:\Code\axioner-deploy\scripts\local-bootstrap.ps1`

**作用**：一次性给 Windows 用户配好：
1. 生成 ed25519 SSH key（如不存在）
2. 写 `~/.ssh/config` 增加 `axioner` 别名
3. 用一次性密码把公钥推到服务器 `authorized_keys`
4. 测试免密登录

- [ ] **Step 2.1**: 创建 `scripts\local-bootstrap.ps1`：

脚本职责（伪代码骨架）：

```powershell
# 参数：服务器 IP、用户名、初次密码（仅首次用，之后改 key auth）
param(
    [string]$ServerHost = "38.12.23.241",
    [string]$ServerUser = "root",
    [string]$Alias = "axioner",
    [Parameter(Mandatory=$true)][string]$BootstrapPassword
)

$sshDir = "$env:USERPROFILE\.ssh"
$keyPath = "$sshDir\axioner_ed25519"

# 1. 确保 ~/.ssh 存在
if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory $sshDir -Force }

# 2. 生成 ed25519 key（如不存在）
if (-not (Test-Path $keyPath)) {
    ssh-keygen -t ed25519 -f $keyPath -N '""' -C "axioner-deploy@$env:COMPUTERNAME"
}

# 3. 追加到 ~/.ssh/config（如未配过）
$configPath = "$sshDir\config"
$configBlock = @"

Host $Alias
    HostName $ServerHost
    User $ServerUser
    IdentityFile $keyPath
    ServerAliveInterval 60
"@
if (-not (Test-Path $configPath) -or -not (Select-String -Path $configPath -Pattern "Host $Alias" -Quiet)) {
    Add-Content -Path $configPath -Value $configBlock
}

# 4. 把公钥推上去（用 Python + paramiko 因为 Windows 没原生 ssh-copy-id）
$pubKey = Get-Content "$keyPath.pub" -Raw
python -c @"
import paramiko, sys
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('$ServerHost', username='$ServerUser', password='$BootstrapPassword', allow_agent=False, look_for_keys=False)
pub = '''$pubKey'''.strip()
cmd = f'mkdir -p ~/.ssh && chmod 700 ~/.ssh && grep -qxF "{pub}" ~/.ssh/authorized_keys 2>/dev/null || echo "{pub}" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo OK'
_, out, _ = client.exec_command(cmd)
print(out.read().decode())
client.close()
"@

# 5. 测试免密登录
Write-Host "Testing key auth..."
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new $Alias whoami
if ($LASTEXITCODE -ne 0) { Write-Error "Key auth failed"; exit 1 }
Write-Host "✓ ssh $Alias works without password"
```

**实现注意**（给 Codex 的提示）：
- 注意 PowerShell 的字符串引用规则，`-N '""'` 是给 ssh-keygen 一个空 passphrase
- 公钥内容里有空格和等号，传到 Python 时要小心引号
- 如果 ~/.ssh/config 已存在但没有 axioner 别名，应**追加**而非覆盖
- `ssh -o BatchMode=yes` 阻止任何交互式密码 prompt，确保真的是 key auth

- [ ] **Step 2.2**: 验证（手动）

打开 PowerShell（管理员不必），跑：

```powershell
cd C:\Code\axioner-deploy
.\scripts\local-bootstrap.ps1 -BootstrapPassword 'uikwAKQD3468'
```

期望输出末尾：
```
✓ ssh axioner works without password
```

期望副作用：
- `C:\Users\Axioner\.ssh\axioner_ed25519` 和 `.pub` 存在
- `C:\Users\Axioner\.ssh\config` 含 `Host axioner` 块
- 服务器 `~/.ssh/authorized_keys` 末尾增一行公钥

- [ ] **Step 2.3**: commit

```bash
cd /c/Code/axioner-deploy
git add scripts/local-bootstrap.ps1
git commit -m "feat: 添加 Windows 本地 SSH 配置脚本（local-bootstrap.ps1）"
```

---

### Task 3: 编写服务器 bootstrap 脚本（加 swap）

**Files**:
- Create: `C:\Code\axioner-deploy\scripts\server-bootstrap.sh`

**作用**：在服务器上一次性配 swap。**幂等**——重复跑不出错、不重复加 swap。

- [ ] **Step 3.1**: 创建 `scripts/server-bootstrap.sh`：

```bash
#!/usr/bin/env bash
# server-bootstrap.sh
# 在服务器上一次性配置：1G swap
# 幂等：重复运行不会出错，也不会重复加 swap

set -euo pipefail

SWAP_FILE="/swapfile"
SWAP_SIZE_MB=1024

echo "=== axioner-deploy server bootstrap ==="
echo

# --- 1. swap ---
echo "[1/1] 检查 swap..."
current_swap_mb=$(free -m | awk '/^Swap:/ {print $2}')
if [ "$current_swap_mb" -ge "$SWAP_SIZE_MB" ]; then
    echo "  ✓ 已有 ${current_swap_mb} MB swap，跳过"
else
    if [ -f "$SWAP_FILE" ]; then
        echo "  发现旧 $SWAP_FILE，先关闭并删除"
        swapoff "$SWAP_FILE" 2>/dev/null || true
        rm -f "$SWAP_FILE"
    fi
    echo "  创建 ${SWAP_SIZE_MB}M swap 文件..."
    fallocate -l "${SWAP_SIZE_MB}M" "$SWAP_FILE"
    chmod 600 "$SWAP_FILE"
    mkswap "$SWAP_FILE"
    swapon "$SWAP_FILE"

    # 持久化到 fstab（去重）
    if ! grep -qE "^${SWAP_FILE}\s" /etc/fstab; then
        echo "${SWAP_FILE} none swap sw 0 0" >> /etc/fstab
    fi
    echo "  ✓ swap 已启用并持久化"
fi

echo
echo "=== bootstrap 完成 ==="
swapon --show
free -h
```

**关键点**：
- `set -euo pipefail`：任何命令失败立即退出
- 幂等检查：先看现有 swap 是否够，够就跳过
- /etc/fstab 查重写：grep `-E` 精确匹配开头，避免重复

- [ ] **Step 3.2**: 验证（手动）

```bash
# 把脚本传上去并执行
scp /c/Code/axioner-deploy/scripts/server-bootstrap.sh axioner:/tmp/
ssh axioner 'bash /tmp/server-bootstrap.sh'
```

期望末尾输出：
```
NAME      TYPE  SIZE USED PRIO
/swapfile file 1024M   0B   -2
              total        used        free      shared  buff/cache   available
Mem:           1.9Gi       ...
Swap:          1.0Gi          0B       1.0Gi
```

再跑一次验证幂等：
```bash
ssh axioner 'bash /tmp/server-bootstrap.sh'
# 期望: 跳过 swap 创建（"已有 1024 MB swap，跳过"）
```

- [ ] **Step 3.3**: commit

```bash
cd /c/Code/axioner-deploy
git add scripts/server-bootstrap.sh
git commit -m "feat: 添加服务器 bootstrap 脚本（加 1G swap，幂等）"
```

---

### Task 4: Phase 1 端到端验收

**Files**: 无新文件，仅验证。

- [ ] **Step 4.1**: 本地免密 ssh

```bash
ssh axioner whoami
# 期望: root
# 期望: 不询问密码
```

- [ ] **Step 4.2**: 服务器有 swap

```bash
ssh axioner 'swapon --show && free -h'
# 期望: 显示 /swapfile 1.0G
```

- [ ] **Step 4.3**: 现有 paste 服务未受影响（回归）

```bash
curl -sI https://paste.axioner.top
# 期望: HTTP/2 200 (或 401 if auth required) ─ 不能是 5xx 或连接失败
ssh axioner 'docker ps --filter name=paste-clipboard --format "{{.Status}}"'
# 期望: Up 包含
```

- [ ] **Step 4.4**: 所有验收通过后，标记 Phase 1 完成

```bash
cd /c/Code/axioner-deploy
# 写一个 phase 1 完成标记（可选）
echo "Phase 1 completed: $(date -Iseconds)" > docs/superpowers/plans/PHASE1_DONE.txt
git add docs/superpowers/plans/PHASE1_DONE.txt
git commit -m "chore: Phase 1 (服务器底座) 验收通过"
```

---

## Chunk 2：Phase 2A 共享库（lib/）

**目标**：实现 `scripts/lib/` 下的三个模块，作为 preflight 和 deploy 的依赖。**不**直接对外提供 CLI——仅供其他脚本 import。

**完成判定**：能用 `python -c "from scripts.lib.ssh_client import SSHClient; ..."` 跑通基本调用，无报错。

---

### Task 5: scripts/lib/ssh_client.py

**Files**:
- Create: `C:\Code\axioner-deploy\scripts\__init__.py`（空）
- Create: `C:\Code\axioner-deploy\scripts\lib\__init__.py`（空）
- Create: `C:\Code\axioner-deploy\scripts\lib\ssh_client.py`

**职责**：包装 paramiko，从 ~/.ssh/config 读连接信息，提供 `run(cmd)` 简洁接口。

**接口契约**：

```python
class SSHClient:
    """SSH 客户端。从 ~/.ssh/config 读取 alias 对应的 host/user/key。
    
    用法:
        with SSHClient("axioner") as client:
            rc, out, err = client.run("uname -a")
    """
    
    def __init__(self, alias: str): ...
    def __enter__(self) -> "SSHClient": ...
    def __exit__(self, *args): ...
    
    def run(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        """执行命令。返回 (exit_code, stdout, stderr)。"""
        ...
    
    def put_file(self, local_path: str, remote_path: str) -> None: ...
    def close(self) -> None: ...

def get_default_client() -> SSHClient:
    """返回默认 alias='axioner' 的客户端。"""
    return SSHClient("axioner")
```

**实现要点**（给 Codex）：
- 用 `paramiko.SSHConfig()` 解析 `~/.ssh/config`
- 解析后取 `hostname`, `user`, `identityfile`, `port`
- `paramiko.SSHClient.set_missing_host_key_policy(paramiko.AutoAddPolicy())`
- `look_for_keys=False, allow_agent=False`，强制使用 config 里指定的 key
- `run()` 用 `exec_command()`，从 channel 拿 exit_code（`recv_exit_status()`）
- 错误处理：连接失败抛 `ConnectionError`，命令超时抛 `TimeoutError`

- [ ] **Step 5.1**: 创建空 `__init__.py`：

```bash
mkdir -p scripts/lib
touch scripts/__init__.py scripts/lib/__init__.py
```

- [ ] **Step 5.2**: 实现 `scripts/lib/ssh_client.py`（按上面契约）

- [ ] **Step 5.3**: 验证（命令行）

```bash
cd /c/Code/axioner-deploy
python -c "
from scripts.lib.ssh_client import get_default_client
with get_default_client() as c:
    rc, out, err = c.run('uname -a')
    assert rc == 0, f'rc={rc} err={err}'
    assert 'Linux' in out
    print('OK:', out.strip())
"
# 期望: OK: Linux mg... 6.8.0-48-generic ...
```

- [ ] **Step 5.4**: commit

```bash
git add scripts/__init__.py scripts/lib/__init__.py scripts/lib/ssh_client.py
git commit -m "feat(lib): 添加 SSHClient（paramiko 包装，读 ~/.ssh/config）"
```

---

### Task 6: scripts/lib/server_state.py

**Files**:
- Create: `C:\Code\axioner-deploy\scripts\lib\server_state.py`

**职责**：通过 SSH 收集服务器现状，返回 `ServerState` dataclass。

**接口契约**：

```python
from dataclasses import dataclass, field
from .ssh_client import SSHClient

@dataclass
class ServerState:
    mem_total_mb: int
    mem_avail_mb: int
    disk_total_gb: int
    disk_avail_gb: int
    swap_total_mb: int
    used_ports: list[int]              # 来自 ss -tlnp，去重排序
    project_dirs: list[str]            # /opt 下的目录名（不含 ./）
    nginx_sites: list[str]             # /etc/nginx/sites-enabled 下的文件名
    docker_containers: list[dict]      # [{"name": "...", "status": "Up", "image": "..."}]

def collect(client: SSHClient) -> ServerState:
    """通过单次 SSH 会话执行多条命令，组装 ServerState。"""
```

**实现要点**：
- 一次 SSH 跑一段聚合 shell，避免多次连接
- 用 `||` 兜底空结果（比如 `/opt` 为空时）
- 端口提取：`ss -tlnp | awk '/LISTEN/ {split($4,a,":"); print a[length(a)]}' | sort -u`
- nginx_sites：`ls /etc/nginx/sites-enabled/ 2>/dev/null`
- docker：`docker ps -a --format '{{json .}}'` 然后逐行 json.loads

- [ ] **Step 6.1**: 实现

- [ ] **Step 6.2**: 验证

```bash
cd /c/Code/axioner-deploy
python -c "
from scripts.lib.ssh_client import get_default_client
from scripts.lib.server_state import collect
with get_default_client() as c:
    s = collect(c)
    print(f'mem_avail={s.mem_avail_mb}MB, disk_avail={s.disk_avail_gb}GB')
    print(f'used_ports={s.used_ports}')
    print(f'projects={s.project_dirs}')
    print(f'nginx_sites={s.nginx_sites}')
    print(f'containers={[c[\"name\"] for c in s.docker_containers]}')
    assert s.mem_avail_mb > 0
    assert 22 in s.used_ports and 80 in s.used_ports and 443 in s.used_ports
    assert 'paste' in s.project_dirs
    assert 'paste.axioner.top' in s.nginx_sites
"
# 期望: 全部断言通过 + 打印当前状态
```

- [ ] **Step 6.3**: commit

```bash
git add scripts/lib/server_state.py
git commit -m "feat(lib): 添加 server_state.collect() 收集服务器现状"
```

---

### Task 7: scripts/lib/conflict_detector.py + plan_io.py

**Files**:
- Create: `C:\Code\axioner-deploy\scripts\lib\plan_io.py`
- Create: `C:\Code\axioner-deploy\scripts\lib\conflict_detector.py`

**职责**：
- `plan_io.py`：定义 `DeployPlan`、`Conflict` dataclass + JSON 序列化/反序列化
- `conflict_detector.py`：给定 `DeployPlan` + `ServerState`，返回 `list[Conflict]`

**plan_io 接口**：

```python
from dataclasses import dataclass, asdict
from typing import Literal
import json

@dataclass
class DeployPlan:
    project_name: str
    repo_url: str
    subdomain: str          # full: "bar.axioner.top"
    port: int
    target_dir: str
    container_name: str
    confirmed_by_user: bool = False

@dataclass
class Conflict:
    kind: Literal["dns", "port", "dir", "memory", "disk", "nginx", "env", "private_repo", "certbot"]
    severity: Literal["block", "warn"]
    message: str
    suggested_action: str

def plan_to_json(plan: DeployPlan) -> str: ...
def plan_from_json(s: str) -> DeployPlan: ...
def write_plan(plan: DeployPlan, path: str) -> None: ...
def read_plan(path: str) -> DeployPlan: ...
```

**conflict_detector 接口**：

```python
from .plan_io import DeployPlan, Conflict
from .server_state import ServerState

def detect(plan: DeployPlan, state: ServerState, dns_resolves_to: str | None = None) -> list[Conflict]:
    """检查所有 9 类冲突（见 spec §3.6）。
    
    Args:
        plan: 部署计划
        state: 服务器现状
        dns_resolves_to: DNS 解析结果（None 表示未解析）
    
    Returns:
        冲突列表。空列表 = 无冲突可继续。
    """
```

**9 类冲突规则**：

| 类别 | 触发条件 | severity |
|------|---------|----------|
| `dns` | dns_resolves_to != "38.12.23.241" | block |
| `port` | plan.port in state.used_ports | block |
| `dir` | plan.project_name in state.project_dirs | block |
| `memory` | state.mem_avail_mb < 200 | warn |
| `disk` | state.disk_avail_gb < 1 | block |
| `nginx` | plan.subdomain in state.nginx_sites | block |
| `env` | （由 deploy.py 处理；conflict_detector 不管） | — |
| `private_repo` | （由 git clone 失败时探测；conflict_detector 不管） | — |
| `certbot` | （由 deploy.py 处理） | — |

注意 `env`/`private_repo`/`certbot` 只在 Phase 3 执行中暴露，不属于 preflight 范围。

- [ ] **Step 7.1**: 实现 `plan_io.py`

- [ ] **Step 7.2**: 实现 `conflict_detector.py`，6 类冲突规则

- [ ] **Step 7.3**: 验证

```bash
cd /c/Code/axioner-deploy
python -c "
from scripts.lib.plan_io import DeployPlan
from scripts.lib.server_state import ServerState
from scripts.lib.conflict_detector import detect

# 模拟一个会撞 paste 的 plan
plan = DeployPlan(
    project_name='paste',  # 撞 dir
    repo_url='https://github.com/foo/bar',
    subdomain='paste.axioner.top',  # 撞 nginx
    port=3101,  # 撞 port
    target_dir='/opt/paste',
    container_name='paste-app-1',
)
state = ServerState(
    mem_total_mb=1900, mem_avail_mb=1400,
    disk_total_gb=20, disk_avail_gb=15,
    swap_total_mb=1024,
    used_ports=[22, 80, 443, 3101],
    project_dirs=['paste'],
    nginx_sites=['paste.axioner.top'],
    docker_containers=[],
)
conflicts = detect(plan, state, dns_resolves_to='38.12.23.241')
kinds = sorted({c.kind for c in conflicts})
print('detected:', kinds)
assert kinds == ['dir', 'nginx', 'port'], f'expected [dir, nginx, port], got {kinds}'
print('OK')
"
# 期望: detected: ['dir', 'nginx', 'port']\nOK
```

- [ ] **Step 7.4**: commit

```bash
git add scripts/lib/plan_io.py scripts/lib/conflict_detector.py
git commit -m "feat(lib): 添加 DeployPlan/Conflict 数据结构 + 冲突检测"
```

---

## Chunk 3：Phase 2B 主流程脚本（preflight + deploy）

**目标**：实现两个 CLI 脚本，串起 lib/ 三个模块，分别对应 spec §3.4 的 Phase 1（预检）和 Phase 3-4（执行+报告）。

**完成判定**：
- `python scripts/preflight.py --repo <url> --subdomain <sub>` 输出格式化 plan + 冲突列表
- `python scripts/deploy.py --plan-file <file>` 在无冲突情况下能完整跑通一次部署

---

### Task 8: scripts/preflight.py

**Files**:
- Create: `C:\Code\axioner-deploy\scripts\preflight.py`

**作用**：接受用户的"我要部署 X 到 Y"意图，调研服务器 + DNS + 仓库，输出一份 `DeployPlan` + 冲突清单。

**CLI 设计**：

```bash
python scripts/preflight.py \
    --repo https://github.com/foo/bar \
    --subdomain bar.axioner.top \
    [--port 3102]                    # 可选；省略时自动选下一个空闲端口
    [--project-name bar]             # 可选；省略时从 repo 名推断
    [--json plan.json]               # 可选；输出机器可读 plan 到文件，给 deploy.py 用
```

**人类可读输出格式**（仿 spec §3.4 Phase 2 plan 块）：

```
═══ 部署预检报告 ═══

拟部署:    bar
仓库:      https://github.com/foo/bar
域名:      bar.axioner.top
  DNS:     ⚠️  未解析 → 你先去 DNS 后台加 A 记录指向 38.12.23.241
端口:      3102 (空闲)
目录:      /opt/bar/
内存余量:  1.4 GB / 1.9 GB ✓
磁盘余量:  15 GB / 20 GB ✓
Swap:      1 GB ✓

冲突清单:
  ✗ dns / block: bar.axioner.top 未解析到 38.12.23.241
  ✓ port: 3102 空闲
  ✓ dir: /opt/bar 不存在
  ✓ nginx: bar.axioner.top site config 不存在
  ✓ memory: 1400 MB 可用
  ✓ disk: 15 GB 可用

是否继续？(下一步: python scripts/deploy.py --plan-file plan.json)
```

**实现要点**：
- DNS 解析：用 `socket.gethostbyname(hostname)`；失败时设 `dns_resolves_to=None`
- 端口自动选择：从 3102 开始递增，直到找到 `not in state.used_ports`
- repo 名推断：从 URL 末尾去 `.git` 取 basename
- container_name 命名：`{project_name}-app-1`（与 docker-compose 默认 `<service>-1` 命名一致）
- 写 `--json` 文件时，把 plan + state + conflicts 都序列化进去（deploy.py 重读时不必再连一次 SSH）
- **不需要确认**——preflight 是只读的，确认在 deploy.py 入口

- [ ] **Step 8.1**: 实现 preflight.py

- [ ] **Step 8.2**: 验证（不带冲突）

先确认 `bar` 不存在：

```bash
ssh axioner 'ls /opt/'
# 应该只有 paste 和 containerd
```

跑：

```bash
cd /c/Code/axioner-deploy
python scripts/preflight.py \
    --repo https://github.com/example/bar \
    --subdomain bar.axioner.top \
    --json /tmp/bar-plan.json
# 期望: 报告里 dns 是 ✗ block（未解析），其他都 ✓
# 期望: /tmp/bar-plan.json 存在
```

- [ ] **Step 8.3**: 验证（带冲突）

```bash
python scripts/preflight.py \
    --repo https://github.com/example/paste \
    --subdomain paste.axioner.top
# 期望: dir / nginx / port 都报冲突
```

- [ ] **Step 8.4**: commit

```bash
git add scripts/preflight.py
git commit -m "feat: 添加 preflight.py（Phase 1 预检 CLI）"
```

---

### Task 9: scripts/deploy.py

**Files**:
- Create: `C:\Code\axioner-deploy\scripts\deploy.py`

**作用**：读 preflight 输出的 plan.json，按 spec §3.4 Phase 3 步骤执行部署；中间任一步失败立即停下；末尾输出 Phase 4 报告。

**CLI 设计**：

```bash
python scripts/deploy.py \
    --plan-file /tmp/bar-plan.json \
    [--yes]                # 跳过最终人工确认 prompt
    [--templates-dir ./templates]
```

**Phase 3 步骤（按 spec §3.4）**：

| # | 动作 | 失败处理 |
|---|------|---------|
| 1 | 在服务器 `mkdir /opt/<name>` | 已存在 → 抛错（应在 preflight 拦截，这里是兜底） |
| 2 | `git clone <repo> /opt/<name>` | 私有仓库失败 → 报错让用户配 deploy key |
| 3 | 检查 `.env.example` 存在；提示用户填 `.env` | `.env.example` 不存在 → 警告，不阻塞 |
| 4 | `docker compose build && docker compose up -d` | build/up 失败 → 抛错，附 stderr |
| 5 | 写 nginx site config（从 `templates/nginx/site.conf` 占位符替换） | 略 |
| 6 | `nginx -t`，通过则 `systemctl reload nginx` | nginx -t 失败 → 抛错并 rollback nginx config |
| 7 | `certbot --nginx -d <subdomain> --non-interactive --agree-tos -m <email>` | certbot 失败 → 抛错（多半是 DNS 问题） |
| 8 | `curl -sIf https://<subdomain>` | 非 2xx/3xx → 警告（不阻塞，可能是应用还在启动） |

**Phase 4 报告**（按 spec §3.4 Phase 4）：

```
═══ 部署完成 ═══
✓ https://bar.axioner.top
  容器: bar-app-1 (Up 12 seconds, 56 MB / 1.9 G)
  Nginx: bar.axioner.top → 127.0.0.1:3102
  证书: 有效期至 2026-08-02
  健康检查: HTTP/2 200

后续 TODO:
  [ ] 填 /opt/bar/.env 中的密钥占位符
  [ ] 跑业务功能验证
```

**实现要点**：
- 每步开头打印 `[N/8] step description...`
- 每步用 `client.run()` 拿 (rc, out, err)；rc != 0 立即抛 `DeployStepError`
- 模板替换：用 `str.format(**plan.__dict__)` 或 `.replace('{port}', str(port))`
- nginx config 写到 `/etc/nginx/sites-available/<sub>`，再 ln 到 sites-enabled
- 失败时**不自动 rollback 容器**（避免破坏更多）；只 rollback nginx config（`rm` 错误的 site 文件）
- certbot email：用 `--register-unsafely-without-email`（私人项目）或读环境变量 `AXIONER_CERT_EMAIL`

**Codex 实现时的注意**：

```python
class DeployStepError(Exception):
    def __init__(self, step_name: str, exit_code: int, stderr: str):
        self.step = step_name
        ...

# 主流程伪代码
def run_deploy(plan_path: str, templates_dir: str, auto_yes: bool = False):
    plan, state = read_plan_with_state(plan_path)
    if not plan.confirmed_by_user and not auto_yes:
        ans = input("是否继续？[y/N]: ")
        if ans.lower() not in ("y", "yes"): return
    
    with get_default_client() as ssh:
        try:
            mkdir(ssh, plan)
            git_clone(ssh, plan)
            check_env_example(ssh, plan)  # 警告但不抛
            docker_up(ssh, plan)
            write_nginx_config(ssh, plan, templates_dir)
            nginx_reload(ssh, plan)  # 失败会回滚 nginx
            certbot(ssh, plan)
            health_check(ssh, plan)
        except DeployStepError as e:
            print(f"\n✗ {e.step} 失败 (rc={e.exit_code}):\n{e.stderr}")
            print(f"\n已完成步骤可用 ssh axioner 检查；未完成步骤未执行。")
            sys.exit(1)
        
        print_phase4_report(ssh, plan)
```

- [ ] **Step 9.1**: 实现 deploy.py

- [ ] **Step 9.2**: 单元验证（不实际部署，先看输入解析）

```bash
python scripts/deploy.py --plan-file /tmp/bar-plan.json --dry-run
# (建议加个 --dry-run 选项，只打印步骤不执行)
# 期望: 打印 8 个步骤的预览，不连 SSH
```

- [ ] **Step 9.3**: 端到端验证延后到 Chunk 5（需要先有模板）

- [ ] **Step 9.4**: commit

```bash
git add scripts/deploy.py
git commit -m "feat: 添加 deploy.py（Phase 3 执行 + Phase 4 报告）"
```

---

## Chunk 4：模板和文档

**目标**：把 spec §3.3 的项目模板和 nginx 模板落成实际文件；写本仓库的 README/CLAUDE.md。

**完成判定**：用 `templates/project/*` 创建一个新项目目录后，结构与 spec §3.3 一致；deploy.py 能正常用模板生成 nginx config。

---

### Task 10: 项目模板（templates/project/）

**Files**:
- Create: `templates/project/Dockerfile.node`
- Create: `templates/project/Dockerfile.python`
- Create: `templates/project/docker-compose.yml`
- Create: `templates/project/.env.example`
- Create: `templates/project/.dockerignore`
- Create: `templates/project/.gitignore`
- Create: `templates/project/README.md`
- Create: `templates/project/CLAUDE.md`

- [ ] **Step 10.1**: `Dockerfile.node`（基于 paste 现有）

```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
EXPOSE {{PORT}}
CMD ["node", "server/index.js"]
```

- [ ] **Step 10.2**: `Dockerfile.python`

```dockerfile
FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE {{PORT}}
CMD ["python", "main.py"]
```

- [ ] **Step 10.3**: `docker-compose.yml`

```yaml
version: '3.8'
services:
  app:
    build: .
    container_name: {{PROJECT_NAME}}-app-1
    ports:
      - "127.0.0.1:{{PORT}}:{{PORT}}"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    env_file:
      - .env
    environment:
      - NODE_ENV=production
      - PORT={{PORT}}
    restart: unless-stopped
```

- [ ] **Step 10.4**: `.env.example`

```bash
# 应用配置（按需修改）
PORT={{PORT}}
NODE_ENV=production

# 应用密钥（必填）
# SECRET_KEY=
# JWT_SECRET=
```

- [ ] **Step 10.5**: `.dockerignore`

```
node_modules
.git
.gitignore
.env
*.log
data/*.json
logs/*.log
docs/
*.md
.claude/
```

- [ ] **Step 10.6**: `.gitignore`

```
node_modules/
__pycache__/
.env
*.log
data/
logs/
.DS_Store
Thumbs.db
```

- [ ] **Step 10.7**: `templates/project/README.md`（极简）

```markdown
# {{PROJECT_NAME}}

部署到 https://{{SUBDOMAIN}}（端口 {{PORT}}）

## 本地开发

\`\`\`bash
cp .env.example .env
# 编辑 .env 填入密钥
docker compose up
\`\`\`

## 部署

见 `CLAUDE.md`
```

- [ ] **Step 10.8**: `templates/project/CLAUDE.md`（部署 runbook，给本项目内的 Claude Code 看）

```markdown
# {{PROJECT_NAME}} 部署 runbook

## 服务器信息
- SSH alias: `axioner`
- 部署目录: `/opt/{{PROJECT_NAME}}/`
- 域名: `https://{{SUBDOMAIN}}`
- 端口: `{{PORT}}`（仅 127.0.0.1 监听，nginx 反代）

## 部署 / 更新

新部署：
\`\`\`bash
python C:\Code\axioner-deploy\scripts\preflight.py \
    --repo <repo url> \
    --subdomain {{SUBDOMAIN}} \
    --port {{PORT}} \
    --json plan.json
# 看输出，无 block 冲突后：
python C:\Code\axioner-deploy\scripts\deploy.py --plan-file plan.json
\`\`\`

更新已部署项目：
\`\`\`bash
ssh axioner "cd /opt/{{PROJECT_NAME}} && git pull && docker compose build && docker compose up -d"
\`\`\`

## 危险操作（必须人工确认）
- `docker compose down -v`（会删数据卷）
- `rm -rf /opt/{{PROJECT_NAME}}`
- `certbot delete --cert-name {{SUBDOMAIN}}`

## 不允许 AI 自动做的事
- 修改 `.env` 中的密钥
- 删除 `data/` 目录
- 改 nginx 全局配置（`/etc/nginx/nginx.conf`）
```

- [ ] **Step 10.9**: commit

```bash
git add templates/project/
git commit -m "feat(templates): 添加项目骨架模板（Dockerfile/compose/.env/CLAUDE.md）"
```

---

### Task 11: nginx 模板

**Files**:
- Create: `C:\Code\axioner-deploy\templates\nginx\site.conf`

- [ ] **Step 11.1**: 创建模板（基于 paste.axioner.top 现有 config）

```nginx
# {{SUBDOMAIN}} - axioner-deploy 自动生成
server {
    listen 80;
    listen [::]:80;
    server_name {{SUBDOMAIN}};

    # certbot 会改动这块；初始时 80 端口先简单 200，
    # 等 certbot --nginx 跑完会自动改成 301 https
    location / {
        proxy_pass http://127.0.0.1:{{PORT}};
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

**注意**：先只写 80 端口块。certbot `--nginx` 跑完后会**自动注入** 443 块和 80 → 301 重定向。

- [ ] **Step 11.2**: commit

```bash
git add templates/nginx/site.conf
git commit -m "feat(templates): 添加 nginx site 模板"
```

---

### Task 12: 仓库根 README + CLAUDE.md

**Files**:
- Modify: `C:\Code\axioner-deploy\README.md`（覆盖之前的 stub）
- Create: `C:\Code\axioner-deploy\CLAUDE.md`

- [ ] **Step 12.1**: 覆盖 README.md（完整版）

包含：仓库目的 / 目录结构 / 一次性配置（local-bootstrap.ps1 + server-bootstrap.sh）/ 日常使用（preflight + deploy）/ 链接到 spec 和 plan。

- [ ] **Step 12.2**: 创建 `CLAUDE.md`（教 AI 用本仓库）

包含：
- "你在 axioner-deploy 仓库里。这个仓库是 axioner.top 的部署工具集"
- 服务器信息（IP / SSH alias / 已部署项目清单）
- AI 的工作流：preflight → 看冲突 → 用户确认 → deploy
- 每个 lib 模块的简要说明（哪个干什么）
- 危险操作清单
- "AI 永远不写 .env 的真实值"

- [ ] **Step 12.3**: commit

```bash
git add README.md CLAUDE.md
git commit -m "docs: 完善仓库 README + CLAUDE.md"
```

---

## Chunk 5：端到端验收

**目标**：用一个真实测试仓库（公开 GitHub repo）跑完整流程，验证 spec §7 验收标准里的部署相关条款。

---

### Task 13: 准备测试仓库

- [ ] **Step 13.1**: 在 GitHub 建一个公开测试仓库，比如 `axioner-test-app`
  - 内容：一个最小的 Node Hello World 服务（监听 PORT 环境变量）
  - 含 Dockerfile + docker-compose.yml + .env.example
  - 用 `templates/project/` 拷贝出来填一遍即可

- [ ] **Step 13.2**: DNS 加 `test.axioner.top` A 记录 → `38.12.23.241`，等解析生效（`nslookup test.axioner.top`）

---

### Task 14: 跑完整流程 + 收集问题

- [ ] **Step 14.1**: 正常路径

```bash
cd /c/Code/axioner-deploy
python scripts/preflight.py \
    --repo https://github.com/<you>/axioner-test-app \
    --subdomain test.axioner.top \
    --json /tmp/test-plan.json
# 期望: 全部 ✓（DNS 已解析）

python scripts/deploy.py --plan-file /tmp/test-plan.json --yes
# 期望: 8 个步骤全部成功；末尾报告含 https://test.axioner.top + 200 OK

curl -sI https://test.axioner.top
# 期望: HTTP/2 200
```

- [ ] **Step 14.2**: 故意端口冲突

手动改 plan.json 里 port 为 3101（paste 在用）：

```bash
python scripts/preflight.py \
    --repo https://github.com/<you>/axioner-test-app \
    --subdomain test2.axioner.top \
    --port 3101
# 期望: 报告里 port: ✗ block: 3101 已被占用
```

- [ ] **Step 14.3**: 故意 DNS 未解析

```bash
python scripts/preflight.py \
    --repo https://github.com/<you>/axioner-test-app \
    --subdomain noexist.axioner.top
# 期望: dns: ✗ block: noexist.axioner.top 未解析
```

- [ ] **Step 14.4**: 回归测试 paste

```bash
curl -sI https://paste.axioner.top
# 期望: HTTP/2 200（不能受影响）
```

- [ ] **Step 14.5**: 清理测试项目

```bash
ssh axioner "cd /opt/test && docker compose down -v && cd / && rm -rf /opt/test && rm /etc/nginx/sites-enabled/test.axioner.top /etc/nginx/sites-available/test.axioner.top && systemctl reload nginx && certbot delete --cert-name test.axioner.top --non-interactive"
```

- [ ] **Step 14.6**: commit 验收完成标记

```bash
echo "Phase 2 completed: $(date -Iseconds)" > docs/superpowers/plans/PHASE2_DONE.txt
git add docs/superpowers/plans/PHASE2_DONE.txt
git commit -m "chore: Phase 2 (部署脚本) 验收通过"
```

---

## 验收对照（spec §7）

| Spec 验收条款 | Plan Task |
|------|------|
| 服务器 swap 1 G | Task 4 (Step 4.2) |
| 本地 ssh axioner 免密码 | Task 4 (Step 4.1) |
| paste.axioner.top 仍正常 | Task 4 (Step 4.3) + Task 14 (Step 14.4) |
| 测试仓库部署成功 | Task 14 (Step 14.1) |
| 端口冲突 AI 暂停提议 | Task 14 (Step 14.2) |
| DNS 未解析 AI 暂停 | Task 14 (Step 14.3) |

---

## 实施时的协作方式（按 CLAUDE.md）

每个 Task 的实施流程：

1. **Claude（架构）**：从 plan 拿出当前 Task 描述
2. **Claude → Codex**：用 `/ask codex "实现 Task N: ..."` 委派
3. **Codex**：写代码、跑验证、commit
4. **Claude**：拿 `/pend codex` 看结果，review
5. **Claude → 用户**：阶段性 demo / 问询

如 Codex 不可用 → 降级 Gemini，并在 commit message 注明"降级接管"。

---

## 不在本计划范围（参考 spec §8）

- 多服务器编排
- 蓝绿/金丝雀部署
- 监控告警
- 数据库管理
- CI/CD pipeline
- 性能压测

需要时另起新 spec + plan。
