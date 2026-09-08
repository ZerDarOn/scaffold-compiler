# Scaffold Compiler V2.1 Go CLI 配方实施方案

## 1. 目标与边界

在不改变 V2 已发布事务、安全清理和信任边界的前提下，增加第三套受信任内置配方
`go-cli`。成品是仅使用 Go 标准库的命令行项目，支持交互式配置、确定性预览、候选
装配、真实工具链验证、原子发布和胶囊自清理。

不支持第三方 Go 模板、用户脚本、动态插件、依赖下载、向已有目录写入或 Finalize 后
升级。生成模块声明 `go 1.22`，验证要求本机已有 Go 1.22 或更新版本，并固定
`GOTOOLCHAIN=local`、`GOWORK=off`，禁止验证阶段自动切换工具链或继承工作区。

## 2. 风险扫描

- Invariants：目标只由共享 Finalize 写入；候选在每个外部进程后仍与摘要一致；成品无
  生成器元数据。
- Atomicity：复用现有候选、验证凭证、no-replace 发布和清理事务，不创建 Go 专用捷径。
- Concurrency：继续由目标锁与运行 ID 隔离；Go 构建产物只能写入运行所有的验证目录。
- Side effects：仅执行受信任绝对路径的 `go`，使用固定参数数组、超时和输出上限。
- Trust boundaries：严格验证模块路径与二进制名称；蓝图仍为数据，不能声明命令。
- Observability：记录 Go 验证开始、结束、门数量和不完整数量；进程失败沿用脱敏诊断。
- Performance：四个顺序门 `gofmt / test / build / run`，无网络和无依赖安装，成本有界。

## 3. 宏观模块

1. 配方与配置：声明 Go 配方、答案规范和能力白名单。
2. 蓝图：提供 Go 模块、CLI、测试、质量配置和双平台 CI 的确定性字节。
3. 装配适配器：只把规范答案映射为模板变量。
4. 验证适配器：把固定门映射为受控 Go 进程，构建输出留在验证目录。
5. 产品入口：把问卷与 Go 可执行文件解析接入现有组合根。
6. 证据：配置、注册、计划、黄金项目、验证器、真实胶囊和文档测试。

构建顺序：配置与注册 → 蓝图与装配 → 验证 → CLI/组合根 → 真实验收 → 文档发布门。

会使方案失效的假设：Go 在支持平台无法以普通绝对可执行文件提供；标准库项目仍要求
联网；现有蓝图模板不能表达所需静态树；Go 验证必须写入候选目录。

## 4. 架构决定

ADR-GO-1
  Decision : Go 配方只生成标准库 CLI，并把所有构建缓存和二进制放在验证目录。
  Context  : 第三套配方用于证明通用架构，而不是引入新的包管理和网络信任边界。
  Options  : 标准库 CLI；带第三方 CLI 框架；仅生成不运行验证。
  Chosen   : 标准库 CLI，执行 gofmt、go test、go build 和生成二进制运行。
  Tradeoff : 能力刻意较小，但验证快速、可重复、跨平台且无需下载依赖。
  Rollback : 移除 Go 注册、蓝图和适配器；共享 V2 生命周期与已发布配方不变。

ADR-GO-2
  Decision : 模块路径和二进制名由严格答案解析器规范化，问卷没有解释权。
  Context  : Go 标识进入 go.mod、文件内容和构建输出名，是不受信任输入。
  Options  : 接受任意字符串；由模板转义；使用闭合正则并拒绝未知字段。
  Chosen   : 验证便携模块路径和小写二进制名，问卷结果再次通过正式解析器。
  Tradeoff : 拒绝少数合法但不便携的 Go 模块路径，换取跨平台确定性。
  Rollback : 在保持闭合验证的前提下扩展正则，不允许回退为自由文本。

## 5. 微任务

### Phase 1：配置与计划

#### Task 1.1｜失败测试

- Input：Go 配方声明和答案样例。
- Output：注册、默认值、非法模块路径、非法二进制名、未知字段和能力计划测试。
- Risk：配方跨越蓝图或验证白名单。
- Rollback：只删除新增测试。

#### Task 1.2｜最小实现

- Input：Task 1.1。
- Output：严格 Go 配方与答案规范化。
- Risk：破坏现有 FastAPI/CMake 配置兼容。
- Rollback：移除 Go 常量、声明和 normalizer 注册。

### Phase 2：蓝图与装配

#### Task 2.1｜黄金项目失败测试

- Input：独立期望树与内容合同。
- Output：文件、关键字节、静态安全和零生成器痕迹测试。
- Risk：测试照抄实现而失去独立性。
- Rollback：只删除 Go 黄金合同。

#### Task 2.2｜蓝图和适配器

- Input：Task 2.1。
- Output：Go 质量、模块/CLI 蓝图及受信任装配适配器。
- Risk：模板变量进入文件路径或内容时未验证。
- Rollback：移除 Go 蓝图和装配注册。

### Phase 3：真实验证

#### Task 3.1｜验证器失败测试

- Input：固定验证门和故障注入。
- Output：缺工具、失败、超时、输出溢出、错误运行结果和候选篡改测试。
- Risk：Go 命令写入候选或触发工具链下载。
- Rollback：只删除新增测试。

#### Task 3.2｜验证器与组合根

- Input：Task 3.1。
- Output：受控 Go 验证器、运行时上下文、工具发现和问卷注册。
- Risk：外部进程或环境变量越过信任边界。
- Rollback：移除验证和产品入口注册。
- Flag：执行本机 Go 进程，但仅写运行所有的验证目录。

### Phase 4：发布证据

#### Task 4.1｜真实胶囊验收

- Input：完整 Go 配方和本机/CI Go 工具链。
- Output：`init → preview → run → self-cleanup` 双平台证据。
- Risk：开发树通过但 zipapp 缺模块，或 Windows 二进制后缀不同。
- Rollback：阻止 V2.1 发布。
- Flag：真实编译和运行生成程序。

#### Task 4.2｜文档与最终审查

- Input：全部通过的证据。
- Output：用户/作者文档、状态快照和发现优先的工程审查。
- Risk：文档承诺超过实际能力。
- Rollback：不发布 V2.1，V2.0 保持稳定。

总任务：8。每一阶段都可通过删除 Go 专用注册和文件独立回退；共享事务层不修改。

## 6. 当前状态

- Done：Tasks 1.1–4.1；Go 配方、严格配置、两个蓝图、装配/验证适配器、问卷、工具发现和真实胶囊全链路均已实现。
- Tests：本地全量为 288 passed、4 skipped、245 subtests passed；Go 真实 `init → preview → gofmt/test/build/run → Finalize → self-cleanup` 通过。
- Next：Task 4.2，等待固定 Go 1.22.12 的 Windows/Ubuntu CI 后发布 `v2.1.0-rc.1`。
- Debt：生成项目的 CI 使用 Go 1.22.x；编译器仓库 CI 固定 Go 1.22.12，后续升级需显式评审。
- Rollback point：`v2.0.0` 正式标签与发布资产不移动。
