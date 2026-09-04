# Scaffold Compiler V1 实施方案

> 状态：宏观架构已确认，微观实施计划待执行  
> 工作目录：`D:\Code\Xzt-vital`  
> 更新时间：2026-09-04  
> 当前实现：Phase 1 已完成；配置契约、显式状态机与原子状态记录已建立，尚未初始化 Git，尚无运行时依赖

## 0. 给后续执行模型的规则

这份文档是 V1 的单一事实源。切换到较低推理等级后，严格遵循以下规则：

1. 每次只执行“任务状态表”中第一个 `NOT_STARTED` 任务，除非用户明确指定其他任务。
2. 开始任务前，先阅读本文件及准备修改的现有文件；不得重新发明架构。
3. 核心逻辑必须按照计划中的“失败测试 → 最小实现”任务对推进。
4. 每项任务完成后运行其验收命令，并把状态、测试结果和新增债务更新到本文件。
5. 当前任务的测试未通过时，不得进入下一任务。
6. 不得扩大 V1 技术栈、选项或命令面；不得顺手加入认证、插件或长期升级能力。
7. 不得使用模糊删除、Shell 字符串拼接、全局软件安装或管理员权限。
8. 遇到计划中的失效假设、跨平台行为无法证明、或需要改变 ADR 时，停止实现并切回高推理等级评审。
9. 不提交 Git、不发布、不推送远端，除非用户另行明确要求。
10. 任务交付只报告：完成内容、修改文件、测试结果、遗留风险、下一任务编号。

允许的任务状态只有：`NOT_STARTED`、`IN_PROGRESS`、`BLOCKED`、`DONE`。

## 1. 产品定义

Scaffold Compiler 是一个一次性脚手架编译器：

```text
受约束的项目配置
+ 内置且版本固定的声明式蓝图
+ 可重复的组合与验证规则
= 经过验证、完全独立的 FastAPI 项目
```

“通用”指编译协议、蓝图契约、安全边界和工程质量标准可复用；V1 不以支持任意语言、框架和工具为目标。

### 1.1 用户目标

用户从一次性发布包启动生成器，选择有限的项目能力，检查候选项目，通过验证后显式 Finalize。最终项目不包含、引用或依赖生成器、蓝图、临时环境或生成状态。

### 1.2 V1 输入

用户只提供：

- 项目显示名称；
- Python 包名，默认由项目名称安全推导，也允许显式填写；
- 最终目标目录；
- `database = none | postgres`；
- `container = none | docker`。

默认黄金配置为 `postgres + docker`。其他工程选型全部固定，不作为交互选项。

### 1.3 V1 固定能力

- 一个在发布时冻结的 Python 小版本与兼容范围；
- `uv` 依赖管理与锁文件；
- FastAPI 单服务；
- Pydantic Settings 类型化配置；
- 应用工厂、lifespan、固定 `/api/v1` 路由入口；
- 存活与就绪检查；
- 结构化日志、请求 ID、稳定异常边界；
- OpenAPI 基本元数据；
- pytest、HTTPX、Ruff 和 mypy；
- 固定 GitHub Actions 质量流水线；
- `.gitignore`、`.editorconfig`、`.env.example` 和最小完整 README；
- 安全默认值：不提交秘密、CORS 默认关闭、生产响应不泄露内部堆栈。

### 1.4 V1 明确不做

- Finalize 后的 `add`、`remove`、`upgrade`、`doctor`；
- 向已有目录或非空目录注入项目；
- 第三方、远程或运行时下载的蓝图；
- 任意可执行蓝图钩子；
- Python/FastAPI 之外的技术栈；
- SQLite、MySQL、多 ORM、同步/异步自由选择；
- JWT、OAuth、用户、角色、权限或任何示例业务域；
- Redis、消息队列、后台任务平台、WebSocket、GraphQL；
- Kubernetes、Helm、云厂商模板、多服务与前端；
- AI 修改项目或自动迁移既有代码；
- 自动初始化远程仓库、提交或推送；
- 清除系统级缓存或不属于本次运行的文件。

## 2. 资产与交付模型

运行时始终区分三类资产：

```text
S：生成器来源
   ├─ 开发模式：长期维护的源码仓库，永远不得自删除
   └─ 发布模式：带一次性标记的可丢弃发布包

W：本次运行专属工作区，与目标处于同一文件系统
   ├─ candidate       候选项目
   ├─ validation-env  临时依赖和测试环境
   ├─ journal         状态及事务记录，DONE 前最后删除
   └─ diagnostics     脱敏诊断信息

T：最终项目目录，提交前必须不存在
```

标准流向：

```text
S → W/candidate → 验证并冻结摘要 → 原子发布到 T → 回收 W
                                                   └→ 发布包 S 可安全回收
```

“自清理”的正式含义是**干净交付**：T 从未包含生成设施。发布包的删除属于提交后的垃圾回收，不属于项目发布事务。

开发模式和发布模式必须通过不可混淆的发布标记区分。任何包含 `.git`、缺少有效发布标记、或路径归属不明确的来源目录都不得自动删除。

`journal` 是 W 的控制记录，不得与其他工作文件一起提前清理。清理顺序必须是其他已拥有资产 → 来源发布包（若允许）→ 诊断信息 → 将状态写为 DONE → 最后删除 journal 与空 W。只要任一清理动作失败，journal 必须保留以支持重试。

### 2.1 一次性发布包的所有权证明与回收协议

自动回收 S 只适用于构建产物，不适用于源码目录。发布包固定为少量不可变文件，例如启动 zipapp、蓝图归档、许可证和 `capsule_manifest.json`。清单至少包含：schema 版本、随机 `capsule_id`、编译器版本、每个相对文件路径、文件类型、大小和 SHA-256，以及预期目录集合。

该清单是**精确所有权枚举**，不是抵抗同一系统用户恶意伪造的密码学身份证明。V1 的威胁模型不承诺防御一个已经能够修改并执行本地生成器代码的同权限攻击者；它必须防止正常使用、路径误传、额外用户文件和损坏发布包导致的误删。

回收必须遵循：

1. S 从当前真实入口文件的位置推导，绝不接受命令行传入“待删除目录”。
2. S、W、T 互不包含；任一路径相同或互为祖先/后代都拒绝回收。
3. S 内存在 `.git`、符号链接、Windows 重解析点、清单之外的条目、文件大小/摘要不符或清单格式异常时，整次 S 回收停止，不删除其中任何文件。
4. 校验完全通过后，只逐项删除清单列出的普通文件，再自底向上删除清单列出的空目录；禁止递归删除和通配符。
5. `capsule_manifest.json` 最后删除；任何一步失败都保留 W/journal 并进入 `CLEANUP_PENDING`。
6. 开发模式没有发布清单，且通常存在 `.git`，因此天然不满足自动回收条件。
7. 构建发布包时禁用外置字节码缓存，避免运行后产生清单外文件。

由于 Windows 不能可靠删除仍在运行的来源，S 回收由一个删除范围之外的监督进程完成：

- 使用位于 S/W/T 之外的系统 Python；若解释器位于待删除范围内，则只保留 `CLEANUP_PENDING`；
- 通过 `python -c` 启动经过固定测试的最小监督逻辑，不落地另一个待清理脚本；
- 使用参数数组且禁止 Shell；工作目录设置为 T 的父目录；
- 子进程通过父进程专属管道的 EOF 判断主生成器已退出，避免仅凭 PID；
- 主生成器在退出前关闭文件、目录、日志和子进程句柄；
- 监督进程只读取 W/journal 中已经冻结的路径和清单摘要，完成或失败后向同一终端输出结果。

## 3. 生命周期状态机

```text
NEW
→ PLANNED
→ MATERIALIZING
→ MATERIALIZED
→ VERIFYING
→ VERIFIED
→ AWAITING_CONFIRMATION
→ COMMITTING
→ COMMITTED
→ CLEANUP_PENDING
→ DONE
```

附加失败状态：

- `FAILED_RETRYABLE`：失败点明确，状态可安全重试；
- `AMBIGUOUS`：磁盘状态与日志无法唯一对应，禁止自动删除或覆盖；
- `CANCELLED`：用户在提交前取消，T 保持不存在。

状态规则：

1. 配置变化会使计划、候选和验证凭证全部失效。
2. 验证凭证绑定配置摘要、蓝图摘要、计划摘要和候选文件摘要。
3. 用户修改候选文件后必须重新验证；V1 不合并这些修改。
4. `COMMITTED` 之后以向前恢复为主，清理失败不能把项目标记为生成失败。
5. `DONE` 是终态；Finalized 项目不存在重新进入生成流程的入口。

### 3.1 可执行迁移契约

每次有文件系统副作用的迁移都必须遵循固定顺序：先把“即将执行的状态”写入 journal 并 `flush + fsync`，再执行副作用，最后写入结果状态并再次 `flush + fsync`。写意图失败则不得执行副作用；副作用后写结果失败则由恢复流程检查磁盘事实。

| 当前状态 | 事件 | 守卫条件 | 允许副作用 | 下一状态或失败状态 |
|---|---|---|---|---|
| 无 | `create_session` | 配置路径可规范化；可建立目标锁和新 W | 创建 `run_id`、W、journal、锁 | `NEW`；失败则不建立会话 |
| `NEW` | `plan` | 配置合法；内置目录摘要有效 | 只写计划和摘要，不写 candidate/T | `PLANNED`；失败为 `FAILED_RETRYABLE(stage=plan)` |
| `PLANNED` | `materialize` | 计划仍匹配配置；candidate 路径不存在 | 先记 `MATERIALIZING`，再创建全新 candidate | `MATERIALIZING` |
| `MATERIALIZING` | `materialize_ok` | 文件所有权、占位符与树摘要完成 | 写候选清单和摘要 | `MATERIALIZED` |
| `MATERIALIZING` | `materialize_error` | 已捕获明确错误 | 关闭句柄；隔离或精确清理本次 candidate | `FAILED_RETRYABLE(stage=materialize)` |
| `MATERIALIZED` | `verify` | 候选摘要等于装配摘要 | 创建/复用 validation-env，启动受控子进程 | `VERIFYING` |
| `VERIFYING` | `verify_ok` | 必需检查全部 PASS；验证后摘要未变 | 写验证报告和绑定摘要的凭证 | `VERIFIED` |
| `VERIFYING` | `verify_error` | 子进程已终止，报告可持久化 | 保留 candidate 和脱敏报告 | `FAILED_RETRYABLE(stage=verify)` |
| `FAILED_RETRYABLE` | `retry` | `failed_stage` 明确且磁盘事实与 journal 一致 | 只重做对应阶段；物化重试必须使用新 candidate | 返回该阶段的安全前态 |
| `MATERIALIZED`/`VERIFIED`/`AWAITING_CONFIRMATION` | `candidate_changed` | 当前摘要与记录不一致 | 删除验证凭证，不自动删除用户修改 | `MATERIALIZED`，要求重新验证 |
| `NEW`/`PLANNED`/`MATERIALIZED`/`VERIFIED`/`FAILED_RETRYABLE` | `reconfigure` | 尚未 COMMITTING；旧 candidate 未改动，或用户显式确认丢弃已改候选 | 精确回收旧候选；写入新配置摘要 | `NEW` |
| `VERIFIED` | `request_finalize` | 验证凭证与当前配置、计划、candidate 摘要一致 | 只展示不可逆确认和目标路径 | `AWAITING_CONFIRMATION` |
| `AWAITING_CONFIRMATION` | `confirm_finalize` | 用户显式确认；目标不存在；同卷；原生 no-replace 可用；提交快照已密封且与验证摘要一致 | 先持久化 `COMMITTING`，再执行一次原生 no-replace 目录移动 | `COMMITTING` |
| `COMMITTING` | `publish_observed` | T 存在、提交快照消失、T 摘要匹配凭证 | 写提交事实，不运行会修改 T 的工具 | `COMMITTED` |
| `COMMITTING` | `recover` | T 不存在、提交快照存在且摘要匹配 | 不自动重试移动；保留快照并再次请求确认 | `AWAITING_CONFIRMATION` |
| `COMMITTING` | `recover` | T 存在、提交快照消失且 T 摘要匹配 | 补记提交成功 | `COMMITTED` |
| `COMMITTING` | `recover` | T/candidate 同时存在、均不存在、或任一摘要异常 | 不移动、不删除 | `AMBIGUOUS` |
| `COMMITTED` | `start_cleanup` | T 摘要仍匹配提交记录 | 先记 `CLEANUP_PENDING`；只清理 manifest 枚举的已拥有资产 | `CLEANUP_PENDING` |
| `CLEANUP_PENDING` | `cleanup_ok` | S/W 除 journal 外均已按规则处理 | 写 DONE；最后删除 journal 和空 W | `DONE` |
| `CLEANUP_PENDING` | `cleanup_error` | T 未被改变，失败路径已记录 | 保留 journal，停止当前清理 | 保持 `CLEANUP_PENDING` |
| 任一 `COMMITTING` 前状态 | `cancel` | 尚未发生目标提交 | 停止子进程；精确回收未修改的 owned candidate；保留失败诊断 | `CANCELLED` |
| 任一非终态 | `recover_stale_lock` | 用户显式请求；PID、进程启动信息和 nonce 共同证明原持有者已结束 | 先检查 journal 与磁盘事实，再释放锁 | 状态不变；事实不唯一则 `AMBIGUOUS` |
| `AMBIGUOUS` | 任意自动事件 | 无 | 禁止移动、覆盖和删除 | 保持 `AMBIGUOUS`，交由人工证据审查 |
| `DONE`/`CANCELLED` | 任意生成事件 | 无 | 无 | 拒绝；新生成必须创建新 `run_id` |

“返回该阶段的安全前态”固定映射为：`plan → NEW`、`materialize → PLANNED`、`verify → MATERIALIZED`、`finalize_precheck → VERIFIED`。不得由实现者自行选择回退状态。

`confirm_finalize` 中的 candidate 不是用户检查过的可变目录本身，而是根据其已验证文件清单复制得到的隐藏提交快照。复制前后都要复核源摘要；提交快照随后被密封为只读并再次计算摘要。相同系统用户蓄意解除权限并在最终移动窗口篡改文件不在 V1 安全保证内，但移动后仍必须立即复核 T；不一致时进入 `AMBIGUOUS`，不得声称成功或自动清理。

### 3.2 无覆盖提交协议

“先检查不存在，再普通重命名”存在竞态，禁止使用。V1 只在以下两个经过真实 CI 验证的平台提供 Finalize：

| 平台 | 原生提交原语 | 成功条件 | 安全失败 |
|---|---|---|---|
| Windows | 通过标准库 `ctypes` 调用 `MoveFileExW`，flags 固定为 `0` | 同卷移动成功且目标此前不存在 | `ERROR_FILE_EXISTS`/`ERROR_ALREADY_EXISTS` 或其他错误；绝不启用 replace/copy 标志 |
| Linux | 通过标准库 `ctypes` 调用 `renameat2(..., RENAME_NOREPLACE)` | 同一 `st_dev` 且内核/文件系统支持 no-replace | `EEXIST` 安全拒绝；`ENOSYS`/`EINVAL` 表示平台不支持并拒绝 Finalize |

提交适配器还必须满足：

1. W 创建在 T 的父目录下，因此提交快照与 T 天然同卷；不得接受用户提供的任意 W。
2. Windows 使用卷身份、Linux 使用 `st_dev` 再次验证同卷，不能只比较盘符或字符串路径。
3. T 的父路径必须真实存在，且从受信根到父目录不存在符号链接或 Windows 重解析点。
4. 目标竞争者在预检后创建 T 时，no-replace 原语必须返回失败并保留提交快照；不得删除、合并或替换竞争者的 T。
5. 原生 API 不可用时拒绝 Finalize；V1 不提供复制回退，也不声称支持 macOS 或其他 POSIX 系统。
6. 提交前关闭 candidate、提交快照、验证环境的全部句柄和子进程；在支持的平台上刷新文件及父目录元数据。
7. 目录移动成功后立即以只读方式复核文件清单和摘要；此检查只检测异常，不再修改项目。
8. 所谓原子性仅指受支持本地文件系统上的目录可见性和 no-replace 语义，不承诺抵抗介质损坏或所有断电情形。

## 4. 不变量与风险边界

| 编号 | 必须始终成立的不变量 | 破坏后的后果 |
|---|---|---|
| I-01 | 只发布与验证凭证同摘要的密封提交快照；移动后摘要异常必须进入 AMBIGUOUS | 验证结论失效或错误声称成功 |
| I-02 | 提交前目标目录不存在且未被部分写入 | 用户得到半成品或原文件被覆盖 |
| I-03 | 删除仅作用于带当前 `run_id` 且经所有权验证的路径 | 误删用户数据 |
| I-04 | 成品不引用 S、W 或生成时绝对路径 | 清理后项目损坏 |
| I-05 | 相同配置和蓝图版本产生相同计划 | 生成不可重复 |
| I-06 | 普通输出文件只有一个所有者 | 蓝图相互覆盖 |
| I-07 | 同一目标同时只有一个活动会话 | 并发生成相互破坏 |
| I-08 | `COMMITTED` 后清理失败不得触碰 T | 正常项目被垃圾回收误删 |
| I-09 | 日志、状态和收据不包含秘密 | 凭据泄露 |
| I-10 | Finalize 是不可逆产品边界 | 项目重新耦合生成器 |
| I-11 | T 不包含时间戳、`run_id`、随机值等生成期易变数据 | 同一输入无法重复生成一致成品 |

### 七维风险处理

| 维度 | V1 处理方式 |
|---|---|
| 不变量 | 通过路径归属、单文件单写者、摘要绑定和提交前复核执行 |
| 原子性 | 原子边界只覆盖同盘本地目录发布；网络、缓存、Docker 不宣称原子 |
| 并发 | 目标级独占锁绑定 PID、进程启动信息和随机 `run_id`；陈旧锁只允许显式恢复 |
| 副作用 | 所有外部命令使用参数数组、超时和进程组清理；不做系统级安装或真实数据库变更 |
| 信任边界 | 只接受内置蓝图；拒绝路径穿越、符号链接、重解析点和任意钩子 |
| 可观测性 | JSON Lines 记录状态变化、摘要、文件路径、命令退出码、耗时和恢复动作，并做脱敏与长度限制 |
| 性能 | 依赖安装、数据库测试和镜像构建是主要耗时；V1 不并行渲染、不做增量生成 |

## 5. 宏观模块

| 模块 | 唯一职责 | 明确不负责 |
|---|---|---|
| 会话与领域内核 | 配置规范化、状态机、运行身份、锁和摘要凭证 | 不渲染模板、不执行命令 |
| 蓝图目录 | 加载并校验随版本发布的官方蓝图声明 | 不下载远程蓝图、不执行钩子 |
| 组合规划器 | 计算能力闭包、冲突、顺序、集中贡献和文件所有权 | 不访问网络、不写候选项目 |
| 候选装配器 | 根据已冻结计划在 W 中生成全新候选 | 不写 T、不修复无效计划 |
| 候选验证器 | 执行结构、质量、测试、数据库和容器验证 | 不改变用户选择、不自动修代码 |
| Finalize 事务器 | 复核摘要、发布 T、恢复中断并回收已拥有资产 | 不重新规划、不管理最终项目 |

依赖方向固定：

```text
会话与领域内核
      ↑
蓝图目录 → 组合规划器 → 候选装配器 → 候选验证器 → Finalize 事务器
```

构建顺序：领域契约 → 规划器 → 装配器 → FastAPI 蓝图 → 验证器 → Finalize → 发布包。

## 6. 蓝图模型

蓝图是声明式、只读、版本固定的数据包。清单格式使用 JSON，以避免生成器启动时依赖第三方解析库。

每个蓝图声明：

- 身份、版本、应用阶段；
- 提供的能力；
- 依赖能力；
- 冲突能力；
- 有类型且有约束的输入变量；
- 独占输出文件；
- 对集中输出文件的结构化贡献；
- 模板和静态文件；
- 声明式验证要求。

禁止字段：Shell 片段、任意命令、Python 导入路径、下载地址、删除通配符。

应用阶段固定为：

```text
project-quality
→ python-runtime
→ fastapi-http-api
→ postgres-persistence（可选）
→ docker-delivery（可选）
→ postgres-docker-integration（条件适配）
→ final-project-assembly
```

组合规则：

1. 能力依赖图必须是有向无环图。
2. 普通文件只能由一个蓝图独占。
3. `pyproject.toml`、README、CI、环境变量清单等共享产物由最终装配阶段一次生成。
4. 蓝图不得按顺序对共享文件做字符串替换或补丁。
5. PostgreSQL 是纵向能力包：依赖、配置、连接生命周期、迁移、就绪检查、测试和文档共同启用。
6. Docker 是纵向交付包；Compose 只在 Docker 与 PostgreSQL 同时启用时由条件适配蓝图提供。
7. 所有冲突、循环、缺失能力和文件碰撞必须在写文件前失败。

## 7. 技术约束

### 7.1 生成器

- 实现语言：Python；
- 最低生成器运行时：Python 3.11；
- 运行时依赖：仅 Python 标准库；
- 开发与测试工具可以使用 pytest、Ruff 和 mypy；
- 命令行解析使用 `argparse`；
- 清单和状态使用 JSON；
- 事务日志使用 JSON Lines；
- 文件摘要使用 SHA-256；
- 模板语法只支持严格变量替换，不支持条件、循环和代码执行；
- 条件变化由蓝图选择解决，而不是由模板逻辑解决；
- 外部进程禁止 `shell=True`，必须有超时、输出上限和进程清理。

### 7.2 生成项目

- Python 目标小版本、FastAPI 及其依赖的准确版本在 Phase 0 通过兼容性检查后冻结；
- 依赖必须形成并提交 `uv.lock`；
- 不生成 `.venv`、缓存、测试数据库数据或生成器运行日志；
- 包结构固定为简单模块化结构，不提供 Clean/DDD/六边形架构选择；
- 数据库模式固定为 SQLAlchemy 2.x 异步风格、一个异步 PostgreSQL 驱动和 Alembic；
- 数据库测试不得用 SQLite 冒充 PostgreSQL；
- CORS 只有在显式配置允许来源时才启用。

## 8. 建议仓库结构

```text
Xzt-vital/
├─ pyproject.toml
├─ README.md
├─ scaffold_compiler_implementation_plan.md
├─ src/
│  └─ scaffold_compiler/
│     ├─ __init__.py
│     ├─ __main__.py
│     ├─ command_line.py
│     ├─ domain_models.py
│     ├─ project_configuration.py
│     ├─ session_state_store.py
│     ├─ target_workspace_lock.py
│     ├─ blueprint_catalog.py
│     ├─ blueprint_plan_compiler.py
│     ├─ candidate_project_assembler.py
│     ├─ candidate_project_validator.py
│     ├─ controlled_process_runner.py
│     ├─ finalization_transaction.py
│     ├─ finalization_recovery.py
│     ├─ owned_path_cleanup.py
│     └─ path_safety.py
├─ blueprints/
│  ├─ project_quality/
│  ├─ python_runtime/
│  ├─ fastapi_http_api/
│  ├─ postgres_persistence/
│  ├─ docker_delivery/
│  ├─ postgres_docker_integration/
│  └─ final_project_assembly/
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  ├─ fault_injection/
│  ├─ golden_projects/
│  └─ acceptance/
└─ scripts/
   └─ build_disposable_capsule.py
```

不得创建单独名为 `utils`、`helpers`、`manager`、`common`、`base`、`service` 或 `handler` 的源码文件。

### 8.1 最终项目固定结构

蓝图实现不得自行发明另一套布局。最终项目以如下结构为准；标有“可选”的路径只在相应能力启用时出现：

```text
<target>/
├─ pyproject.toml
├─ uv.lock
├─ README.md
├─ .python-version
├─ .env.example
├─ .editorconfig
├─ .gitignore
├─ .github/
│  └─ workflows/
│     └─ quality.yml
├─ src/
│  └─ <package_name>/
│     ├─ __init__.py
│     ├─ asgi.py
│     ├─ application.py
│     ├─ api/
│     │  ├─ __init__.py
│     │  ├─ api_router.py
│     │  └─ health_routes.py
│     ├─ configuration/
│     │  ├─ __init__.py
│     │  └─ application_settings.py
│     ├─ errors/
│     │  ├─ __init__.py
│     │  ├─ application_error.py
│     │  └─ http_exception_mapping.py
│     ├─ observability/
│     │  ├─ __init__.py
│     │  ├─ logging_configuration.py
│     │  └─ request_context_middleware.py
│     └─ persistence/                       # 仅 PostgreSQL
│        ├─ __init__.py
│        ├─ database_engine.py
│        ├─ database_session.py
│        └─ database_readiness.py
├─ tests/
│  ├─ unit/
│  └─ integration/
├─ migrations/                              # 仅 PostgreSQL
│  ├─ env.py
│  └─ versions/
├─ alembic.ini                              # 仅 PostgreSQL
├─ Dockerfile                               # 仅 Docker
├─ .dockerignore                            # 仅 Docker
└─ compose.yaml                             # 仅 PostgreSQL + Docker
```

固定行为：

- ASGI 入口为 `<package_name>.asgi:application`；
- `application.py` 只负责构造和装配 FastAPI 应用；
- API 业务入口固定为 `/api/v1`；
- 存活与就绪端点固定为 `/health/live` 和 `/health/ready`；
- 不生成示例业务实体或伪造 CRUD；
- 非 PostgreSQL 组合不得出现 `persistence`、Alembic 或数据库依赖；
- 非 Docker 组合不得出现 Docker/Compose 产物；
- T 中不得写入生成时间、`run_id`、W 路径或机器用户名；
- README 和 CI 使用同一组明确命令，不另外引入任务运行器。

## 9. V1 支持矩阵

| 配置 | PostgreSQL | Docker 输出 | 必须验证 |
|---|---:|---:|---|
| M-01 | 否 | 否 | 安装、格式、lint、类型、单测、启动、health、OpenAPI |
| M-02 | 否 | 是 | M-01 + 镜像构建、容器启动、健康检查 |
| M-03 | 是 | 否 | M-01 + 真实 PostgreSQL、迁移、会话回滚、readiness |
| M-04 | 是 | 是 | M-03 + Compose 启动和服务健康依赖 |

当选择 PostgreSQL 或 Docker 时，完整本地验证需要可用的 Docker 环境；生成器不得自动安装或启动系统级 Docker 服务。缺少必需环境时结果必须是 `SKIPPED` 或 `FAIL`，不得伪装为完整 `PASS`。V1 默认不允许在必需检查为 `SKIPPED` 时 Finalize。

仓库 CI 必须对四种组合全部执行端到端生成。仅测试默认组合不算完成。

## 10. 关键 ADR

### ADR-001：一次性编译器，而非长期项目管理平台

- Decision：Finalize 后最终项目与生成器完全解除关系。
- Context：持续管理要求理解任意用户修改、历史版本和迁移冲突。
- Options：一次性生成；长期托管；两者并存。
- Chosen：一次性生成。
- Tradeoff：边界清晰、可靠性高；旧项目不会自动获得蓝图改进。
- Rollback：若未来出现真实升级需求，将其设计为独立产品，不侵入 V1 项目。

### ADR-002：干净交付，而非原地自删除

- Decision：在同盘隔离工作区构建候选，再发布到一个此前不存在的目标目录。
- Context：运行中的生成器、工作目录和虚拟环境在 Windows 上可能被锁定。
- Options：原地生成后删除；目录交换；独立候选发布。
- Chosen：独立候选发布。
- Tradeoff：需要额外磁盘空间和目标目录参数；发布路径显著更安全。
- Rollback：如果业务必须原地转换，应返回架构阶段重新设计，不能局部加入递归删除。

### ADR-003：生成器运行时只依赖标准库

- Decision：启动、规划、渲染、状态与 Finalize 不依赖第三方 Python 包。
- Context：生成器不能先要求用户安装一套随后又要删除的依赖。
- Options：标准库；临时虚拟环境；单文件原生二进制。
- Chosen：标准库。
- Tradeoff：需要自行实现受限模板、校验和跨平台锁；发行与启动简单。
- Rollback：若实现复杂度不可接受，可改为签名的独立二进制，但需新 ADR。

### ADR-004：封闭且声明式的官方蓝图

- Decision：V1 不加载远程蓝图，不执行蓝图钩子。
- Context：任意蓝图会扩大权限、依赖和兼容性边界。
- Options：开放插件；受限插件；封闭目录。
- Chosen：封闭目录。
- Tradeoff：扩展性较低；安全与测试矩阵可控。
- Rollback：收集真实扩展需求后，另行设计签名、权限和兼容协议。

### ADR-005：能力图与单文件单写者

- Decision：先解析能力 DAG；共享文件由集中装配阶段一次生成。
- Context：模板覆盖和字符串补丁无法可靠组合。
- Options：覆盖；文本补丁；结构化集中装配。
- Chosen：结构化集中装配。
- Tradeoff：蓝图契约更严格；结果确定且可提前检测冲突。
- Rollback：无法结构化组合的能力应合并为一个纵向包，而不是开放覆盖。

### ADR-006：只支持四种可穷举配置

- Decision：V1 只有数据库和容器两个二值维度。
- Context：每增加一个二值开关，组合数量最多翻倍。
- Options：任意组合；大量预设；单预设加两个选项。
- Chosen：单预设加两个选项。
- Tradeoff：选择有限；所有声明支持的结果均可真实测试。
- Rollback：新组合只有进入完整 CI 矩阵后才能成为正式能力。

### ADR-007：发布与垃圾回收分离

- Decision：`COMMITTED` 表示项目成功；来源与工作区清理是可重试后置动作。
- Context：清理可能因文件占用、杀毒软件或权限暂时失败。
- Options：清理失败则回滚项目；忽略清理；分离状态。
- Chosen：分离状态。
- Tradeoff：短时间可能残留工作区；不会为追求整洁而破坏成品。
- Rollback：只能重试清理当前运行明确拥有的路径，不得回滚 T。

## 11. 微观实施计划

### 任务状态表

| 任务 | 状态 | 验收记录 |
|---|---|---|
| 0.1 仓库与质量基线 | DONE | Ruff format/check、mypy、pytest 全部通过；2 tests passed |
| 0.2 冻结运行版本与依赖 | DONE | uv.lock 已生成；Ruff、mypy、pytest 通过；6 tests passed |
| 1.1 配置契约失败测试 | DONE | 10 项配置契约测试先因模块缺失而失败 |
| 1.2 配置契约最小实现 | DONE | Ruff、mypy、pytest 通过；17 tests passed |
| 1.3 状态机失败测试 | DONE | 14 项状态与持久化测试先因模块缺失而失败 |
| 1.4 状态机最小实现 | DONE | Ruff、mypy、pytest 通过；32 tests passed |
| 2.1 蓝图目录与规划失败测试 | DONE | 10 项目录与规划测试先因模块缺失而失败 |
| 2.2 蓝图目录与规划最小实现 | DONE | 内置 7 蓝图与四组合规划通过；43 tests passed |
| 3.1 安全路径与严格渲染失败测试 | DONE | 10 项跨平台路径与非执行模板测试先因模块缺失而失败 |
| 3.2 安全路径与严格渲染最小实现 | DONE | 真实临时目录、链接与重解析点边界通过 |
| 3.3 候选装配与摘要失败测试 | DONE | 5 项装配、故障清理、修改失效与确定性测试先失败 |
| 3.4 候选装配与摘要最小实现 | DONE | Ruff、mypy、pytest 通过；58 tests passed |
| 4.1 四种黄金项目验收规格 | DONE | 四组合独立文件树、依赖、禁止项与接口契约；61 tests passed |
| 4.2 集中项目文件装配 | DONE | 四组贡献确定装配，TOML/README/CI/env 验收通过；66 tests passed |
| 4.3 FastAPI 固定核心蓝图 | DONE | Python 3.14 锁定安装、Ruff、mypy、3 tests、Uvicorn health 全通过 |
| 4.4 PostgreSQL 纵向蓝图 | DONE | Python 3.14 锁定安装、Ruff、mypy、4 tests；隔离 PostgreSQL 迁移、SQL 与 HTTP readiness 全通过 |
| 4.5 Docker 与组合适配蓝图 | IN_PROGRESS | M-02/M-04 冻结安装、Ruff、mypy、tests 与 Compose config 通过；镜像/容器验收等待 Docker 守护进程 |
| 5.1 验证器与进程控制失败测试 | DONE | 超时树终止、非零退出、输出限制、跨边界脱敏、静态扫描、状态与凭证绑定均覆盖 |
| 5.2 验证器与进程控制最小实现 | DONE | 受控无 Shell 进程、阶段化报告、静态安全扫描与不可伪造摘要绑定；80 tests passed |
| 6.1 Finalize 与恢复失败测试 | NOT_STARTED | — |
| 6.2 Finalize 与恢复最小实现 | NOT_STARTED | — |
| 6.3 故障注入与幂等修正 | NOT_STARTED | — |
| 7.1 CLI 行为失败测试 | NOT_STARTED | — |
| 7.2 CLI 最小实现 | NOT_STARTED | — |
| 7.3 一次性发布包失败测试 | NOT_STARTED | — |
| 7.4 一次性发布包与安全回收 | NOT_STARTED | — |
| 8.1 四组合端到端矩阵 | NOT_STARTED | — |
| 8.2 安全与双平台验收 | NOT_STARTED | — |
| 8.3 文档与发布候选验收 | NOT_STARTED | — |

### Phase 0：建立可执行基线

#### Task 0.1｜仓库与质量基线 ✅

- Input：空工作区、本方案。
- Output：Python `src` 布局、测试目录、项目元数据、Ruff、类型检查和 pytest 配置。
- Risk：工具选择漂移，低级模型擅自增加运行时依赖。
- Rollback：删除本任务创建且尚未被后续任务引用的文件。
- Side effect：可能初始化本地开发环境；不得全局安装工具。
- Acceptance：空实现的格式、lint、类型检查和测试命令全部成功；运行时依赖列表为空。

#### Task 0.2｜冻结运行版本与依赖 ✅

- Input：官方 Python、FastAPI、uv、SQLAlchemy、Alembic 与异步驱动兼容信息。
- Output：版本兼容性决策、生成器开发工具锁文件，以及供 Phase 4 集中装配消费的版本常量；本任务不生成任何成品项目锁文件。
- Risk：采用过新版本导致依赖不兼容，或采用浮动范围破坏重现性。
- Rollback：在任何蓝图文件产生前修改冻结版本。
- Side effect：需要联网读取官方资料并解析依赖。
- Acceptance：生成器运行版本与成品目标版本明确；生成器开发依赖可解析并形成锁文件；四种成品的 `uv.lock` 明确推迟至 Tasks 4.3–4.5；仅使用官方文档或包元数据作依据。

### Phase 1：领域契约与状态机

#### Task 1.1｜配置契约失败测试 ✅

- Input：V1 五项输入及不变量 I-02、I-09。
- Output：覆盖合法规范化、非法包名、危险路径、未知枚举、秘密字段和确定性摘要的失败测试。
- Risk：不可信输入突破目标目录或进入日志。
- Rollback：仅删除新增测试。
- Flag：信任边界。
- Acceptance：测试因缺少实现而失败，失败原因与预期行为一致。

#### Task 1.2｜配置契约最小实现 ✅

- Input：Task 1.1 测试。
- Output：不可变配置模型、规范化与 SHA-256 摘要。
- Risk：隐式默认值导致同一输入产生不同结果。
- Rollback：回退实现，不修改已通过评审的测试语义。
- Acceptance：Task 1.1 全部通过；无第三方运行时依赖。

#### Task 1.3｜状态机失败测试 ✅

- Input：第 3 节状态图和恢复规则。
- Output：合法迁移、非法跳转、重复调用、配置失效验证和 `AMBIGUOUS` 停止行为测试。
- Risk：错误状态允许未验证发布或提交后危险回滚。
- Rollback：仅删除新增测试。
- Flag：原子性。
- Acceptance：测试因状态机尚未实现而失败。

#### Task 1.4｜状态机最小实现 ✅

- Input：Task 1.3 测试。
- Output：显式状态枚举、迁移表、持久化状态记录和原子文件替换。
- Risk：崩溃时出现日志声称成功但磁盘未提交的状态。
- Rollback：回退实现并保留测试。
- Acceptance：Task 1.3 全部通过；写入中断测试不产生半截 JSON。

### Phase 2：蓝图目录与确定性规划

#### Task 2.1｜蓝图目录与规划失败测试 ✅

- Input：第 6 节蓝图模型。
- Output：合法闭包、缺失能力、冲突、循环、未知字段、重复 ID、重复文件、阶段顺序和稳定计划摘要测试。
- Risk：组合缺陷直到文件写入后才被发现。
- Rollback：仅删除新增测试。
- Acceptance：所有关键场景存在断言且在缺少实现时失败。

#### Task 2.2｜蓝图目录与规划最小实现 ✅

- Input：Task 2.1 测试与内置 JSON 清单。
- Output：严格清单加载器、能力图解析、稳定拓扑排序、单文件所有权检查和不可变生成计划。
- Risk：文件遍历顺序或字典顺序影响结果。
- Rollback：回退实现，保留行为测试。
- Acceptance：测试全部通过；相同输入重复运行得到字节一致的计划序列化结果。

### Phase 3：安全装配

#### Task 3.1｜安全路径与严格渲染失败测试 ✅

- Input：I-03、I-04、模板限制和跨平台路径样例。
- Output：路径穿越、绝对路径、符号链接、Windows 重解析点、重复目标、未知变量、遗漏变量和模板代码执行尝试测试。
- Risk：越界写入、读取宿主文件或静默留下占位符。
- Rollback：仅删除新增测试。
- Flag：高风险信任边界。
- Acceptance：危险输入均有精确失败类型，不以通用异常糊弄。

#### Task 3.2｜安全路径与严格渲染最小实现 ✅

- Input：Task 3.1 测试。
- Output：路径包含性验证、链接拒绝、只读模板读取和严格变量渲染器。
- Risk：Windows 与 Linux 路径语义不一致。
- Rollback：回退实现；不得降低测试约束。
- Acceptance：Task 3.1 全部通过，并在当前平台运行真实临时目录测试。

#### Task 3.3｜候选装配与摘要失败测试 ✅

- Input：冻结生成计划、全新 W、Task 3.2 安全层。
- Output：全新候选、失败清理、重复运行、候选修改失效、无残留占位符及确定性清单测试。
- Risk：半成品被误认为候选完成，或重生成覆盖用户内容。
- Rollback：仅删除新增测试。
- Flag：原子性与副作用。
- Acceptance：测试覆盖写入中途故障注入并保持 T 不存在。

#### Task 3.4｜候选装配与摘要最小实现 ✅

- Input：Task 3.3 测试。
- Output：运行专属 W、新候选装配、文件所有权清单、候选 SHA-256 树摘要和重生成替换策略。
- Risk：旧候选和新候选状态混淆。
- Rollback：删除当前 `run_id` 明确拥有的 W；不得使用通配删除。
- Acceptance：Task 3.3 全部通过；失败时不会写 T。

### Phase 4：FastAPI 官方蓝图

#### Task 4.1｜四种黄金项目验收规格 ✅

- Input：第 1.3 节固定能力及第 9 节矩阵。
- Output：四种组合的预期文件树、关键配置断言、禁止文件断言和接口行为验收测试。
- Risk：先写模板后补测试会把偶然结构固化为标准。
- Rollback：只修改验收规格，不生成蓝图实现。
- Acceptance：四种组合均有独立期望；未选能力不得产生相关文件或依赖。

#### Task 4.2｜集中项目文件装配 ✅

- Input：Task 0.2 版本常量，以及 Task 4.1 定义的结构化依赖、环境变量、命令、文档与 CI 贡献样例。
- Output：确定性集中装配器，负责生成 `pyproject.toml`、README、CI 和环境变量示例；锁文件由每个实际组合通过 `uv lock` 产生。
- Risk：共享文件被多个蓝图覆写，未选依赖泄漏。
- Rollback：回退集中装配逻辑，不允许改回字符串补丁。
- Acceptance：使用四组贡献夹具重复装配内容一致；此时只验证集中产物，不假称四个项目已经可运行。

#### Task 4.3｜FastAPI 固定核心蓝图 ✅

- Input：M-01 验收规格和 Task 4.2 集中装配器。
- Output：项目质量、Python、FastAPI 核心模板与清单，以及 M-01 的真实 `uv.lock`。
- Risk：过度抽象业务层，或生成不可运行的占位示例。
- Rollback：回退对应蓝图目录。
- Acceptance：M-01 的安装、结构与行为测试通过；不包含业务模型、BaseCRUD 或复杂 DI 容器。

#### Task 4.4｜PostgreSQL 纵向蓝图 ✅

- Input：M-03 验收规格、FastAPI 核心扩展点和集中装配器。
- Output：异步 SQLAlchemy、Alembic、会话边界、readiness、测试、文档贡献和 M-03 锁文件。
- Risk：错误事务边界、SQLite 替代测试、连接未关闭。
- Rollback：移除整个 PostgreSQL 纵向包，不修改核心蓝图语义。
- Side effect：验收使用带 `run_id` 的临时 PostgreSQL，不得操作用户真实数据库。
- Acceptance：M-03 可冻结安装；迁移可从空库执行；请求会话异常时回滚；应用退出关闭连接。

#### Task 4.5｜Docker 与组合适配蓝图

- Input：M-02、M-04 验收规格、已完成的核心与 PostgreSQL 蓝图。
- Output：非 root 镜像、健康检查、`.dockerignore`、条件 Compose 适配，以及 M-02/M-04 锁文件。
- Risk：容器以 root 运行、构建上下文泄露秘密、错误健康依赖。
- Rollback：移除 Docker 与组合适配包。
- Side effect：测试会构建临时镜像和容器，必须按 `run_id` 清理。
- Acceptance：M-02/M-04 均可冻结安装、构建并启动；容器非 root；停止后无本次运行残留容器或卷。

### Phase 5：验证与进程控制

#### Task 5.1｜验证器与进程控制失败测试 ✅

- Input：验证矩阵、I-01、I-04、I-09。
- Output：超时、非零退出、输出上限、秘密脱敏、子进程终止、占位符、绝对路径引用、PASS/FAIL/SKIPPED 语义和凭证绑定测试。
- Risk：挂死、泄密、僵尸进程或虚假通过。
- Rollback：仅删除新增测试。
- Flag：外部副作用与可观测性。
- Acceptance：故障场景均可重复触发，测试本身不遗留进程。

#### Task 5.2｜验证器与进程控制最小实现 ✅

- Input：Task 5.1 测试与四种验证配置。
- Output：受控进程执行器、静态扫描器、分阶段验证报告和绑定候选摘要的验证凭证。
- Risk：验证工具改变冻结后的候选内容。
- Rollback：回退实现；候选仍可丢弃重建。
- Acceptance：Task 5.1 全部通过；验证结束后二次摘要一致；必需检查为 SKIPPED 时不能进入 VERIFIED。

### Phase 6：Finalize 与崩溃恢复

#### Task 6.1｜Finalize 与恢复失败测试

- Input：状态机、I-01 至 I-11、同盘空目标约束。
- Output：目标竞态、跨盘、原生 no-replace 不可用、复制前/中/后候选变化、密封后篡改、重复 Finalize、重命名前后崩溃、COMMITTED 后清理失败、歧义状态和陈旧锁测试。
- Risk：这是全项目最高风险边界，错误会覆盖或删除数据。
- Rollback：仅删除测试；不得在真实用户目录演练。
- Flag：高风险原子性与删除副作用。
- Acceptance：测试仅使用受控临时目录；竞态目标从未被替换；每个故障点都有确定恢复结果；提交前后的摘要异常均不得进入成功清理。

#### Task 6.2｜Finalize 与恢复最小实现

- Input：Task 6.1 测试、已验证候选和独占锁。
- Output：隐藏提交快照、复制前后摘要复核、只读密封、Windows/Linux 原生 no-replace 适配器、事务日志、前向恢复、`CLEANUP_PENDING` 和精确所有权清理。
- Risk：把清理失败错误地当成提交失败，进而删除成品。
- Rollback：提交前删除候选；提交后只向前恢复，绝不自动删除 T。
- Acceptance：Task 6.1 全部通过；COMMITTED 后所有故障都保留完整 T。

#### Task 6.3｜故障注入与幂等修正

- Input：Task 6.2 实现。
- Output：覆盖每个持久化状态边界的故障注入测试和必要修正。
- Risk：只测试正常路径会掩盖断电窗口。
- Rollback：保留故障测试，回退有问题的实现。
- Acceptance：在每个注入点重启恢复两次均得到相同终态；AMBIGUOUS 状态不执行删除。

### Phase 7：CLI 与一次性发布包

#### Task 7.1｜CLI 行为失败测试

- Input：所有已实现领域能力。
- Output：配置、计划预览、生成、验证、状态、重新生成、Finalize 确认、取消和非交互模式测试。
- Risk：CLI 绕过领域状态机或默认执行危险动作。
- Rollback：仅删除新增测试。
- Acceptance：Finalize 必须显式确认；CI 可通过配置文件非交互运行；任何错误返回稳定非零退出码。

#### Task 7.2｜CLI 最小实现

- Input：Task 7.1 测试。
- Output：基于 `argparse` 的薄适配层和人类可读摘要。
- Risk：业务规则散落进命令分支。
- Rollback：回退 CLI；领域模块保持不变。
- Acceptance：Task 7.1 全部通过；CLI 不直接删除、渲染或执行 Shell 字符串。

#### Task 7.3｜一次性发布包失败测试

- Input：开发源码、发布标记规则和安全清理约束。
- Output：可重复打包、开发仓库拒绝自删、损坏清单拒绝、额外文件拒绝、包含 `.git` 拒绝、来源路径不可外部指定、进程退出后回收和清理失败测试。
- Risk：发布逻辑误删源码仓库。
- Rollback：仅删除测试和测试包。
- Flag：不可逆副作用。
- Acceptance：清单不是精确匹配时 S 中零文件被删除；测试不得宣称能防御同权限恶意代码伪造清单。

#### Task 7.4｜一次性发布包与安全回收

- Input：Task 7.3 测试。
- Output：确定性发布包构建器、精确文件清单、基于管道 EOF 的外部监督式清理和开发模式保护。
- Risk：Windows 文件锁、当前工作目录或杀毒软件使清理暂时失败。
- Rollback：项目已提交时进入 CLEANUP_PENDING；不得触碰 T。
- Acceptance：发布包可在全新目录运行；监督解释器与工作目录位于删除范围之外；最终项目无生成器引用；开发仓库始终保留；清理失败可安全重试且无递归删除。

### Phase 8：最终质量门禁

#### Task 8.1｜四组合端到端矩阵

- Input：完整编译器和四种配置。
- Output：从发布包启动到 Finalize 的四条独立验收结果。
- Risk：单元测试通过但真实成品无法安装或启动。
- Rollback：定位到对应阶段修复，不降低验收标准。
- Side effect：依赖下载、临时数据库、镜像和容器；必须带运行标识并回收。
- Acceptance：M-01 至 M-04 全部通过，Finalized 前后项目行为一致。

#### Task 8.2｜安全与双平台验收

- Input：完整编译器、Windows 和 Linux CI 环境。
- Output：路径、锁、重命名、进程终止、清理、日志脱敏和故障恢复报告。
- Risk：只在一个平台正确。
- Rollback：阻止发布，不用平台特判掩盖失败。
- Acceptance：Windows 的 `MoveFileExW` 与 Linux 的 `renameat2(RENAME_NOREPLACE)` 场景通过；不支持原生 no-replace 的平台明确拒绝 Finalize；无越界写入或删除。

#### Task 8.3｜文档与发布候选验收

- Input：通过全部测试的发布候选。
- Output：用户快速开始、先决条件、四种选择、验证含义、Finalize 不可逆说明和故障恢复手册。
- Risk：文档承诺超过实际保证。
- Rollback：修正文档或降级产品声明，不跳过技术缺口。
- Acceptance：新用户仅依据 README 能完成一次生成；文档命令全部实际运行验证。

## 12. 每阶段门禁

| 阶段结束 | 必须满足后才能继续 |
|---|---|
| Phase 0 | 工具链可重复运行，版本已冻结 |
| Phase 1 | 配置与状态机测试全绿，危险输入被拒绝 |
| Phase 2 | 计划确定、冲突在写入前失败 |
| Phase 3 | T 始终未被装配过程写入，候选摘要稳定 |
| Phase 4 | 四种组合结构断言通过，未选能力零残留 |
| Phase 5 | 验证凭证绑定摘要，进程失败可观察且可回收 |
| Phase 6 | 崩溃注入可恢复，COMMITTED 后不触碰 T |
| Phase 7 | 开发源码不可自删，发布包可干净交付 |
| Phase 8 | 四组合、Windows/Linux、文档命令全部通过 |

## 13. 完成定义

只有同时满足以下条件，V1 才能宣布完成：

- 四种受支持配置全部从零生成并通过端到端验证；
- 相同配置与版本重复生成得到相同文件清单和非易变内容；
- 最终项目不包含或引用生成器、蓝图、W、宿主绝对路径和秘密；
- Finalize 前任何失败都不会创建 T；
- Finalize 后任何清理失败都不会删除或改变 T；
- 并发、重复命令、用户修改候选和崩溃状态均有测试；
- Windows 与 Linux 文件系统语义均经过真实 CI 验证；
- 所有外部进程均有超时、输出上限和清理；
- 运行时蓝图只能声明数据，不能执行代码；
- 文档不承诺长期升级、任意组合或绝对原子性；
- 工程审查无 P0/P1 问题，剩余风险被明确记录。

## 14. 会使本方案失效的假设

发生以下任一情况时，停止实施并重新进入架构评审：

1. 用户需要对已开发项目继续使用生成器增删或升级能力。
2. 必须支持向已有或非空目录注入文件。
3. V1 必须执行任意第三方蓝图或用户脚本。
4. 必须在同一个目录原地把源码仓库转换成成品。
5. 无法提供与目标同盘的隔离工作区，却仍要求原子可见发布。
6. 生成器不能依赖外部系统 Python，却必须在 Windows 自动回收自身。
7. 团队无法持续运行四种组合的端到端测试。
8. 产品同时要求多语言、前端、多服务或身份系统。
9. 选用 PostgreSQL/Docker 时无法提供任何真实验证环境，却仍要求声称完整验证。
10. 用户要求 Finalize 后重新生成，同时又要求成品完全不保留生成元数据。

## 15. 当前状态快照

- Done：架构方案、Phases 0–3、Tasks 4.1–4.4 和 Phase 5；M-01/M-03 已真实运行，M-02/M-04 的代码侧与 Compose 静态验收完成。
- Tests：生成器 80 tests passed；四组合均通过 Python 3.14 冻结安装、Ruff、mypy 和项目测试；M-01 Uvicorn health、M-03 隔离 PostgreSQL/Alembic/SQL/HTTP readiness、M-04 Compose config 通过。
- Next：Task 6.1——Finalize 与恢复失败测试；Task 4.5 的真实镜像/容器门等待 Docker 守护进程可用。
- Debt：Docker 镜像构建、非 root 运行、容器健康和 Compose 清理仍为必需的未通过门，当前不得声称 Task 4.5 完成。
- Rollback point：Phase 3 装配层可独立回退；最终目标目录仍无任何写入路径。
