# V2.4 RC1 发布记录

## 版本与资格

- 候选版本：`2.4.0-rc.1`。
- 不可变注释标签：`v2.4.0-rc.1`。
- 冻结提交：`36cc7ffe9224674e95b847c100baa74ca6cc14b2`。
- 精确提交质量门：GitHub Actions `34989636838`，全部成功。
- 标签发布工作流：GitHub Actions `34996208143`，Windows/Ubuntu 核心门、Linux 四种真实
  发布组合、资产构建和 GitHub prerelease 发布全部成功。
- 发布入口：<https://github.com/ZerDarOn/scaffold-compiler/releases/tag/v2.4.0-rc.1>。
- 本阶段补齐了 CI 显式测试清单中的 `test_recipe_discovery_release_capsule.py`，使源码与胶囊
  发现结果一致性在 Windows 和 Linux 上持续验证。

## 公开资产验收

2026-09-16 从 GitHub Release 重新下载 ZIP 与 `.sha256`，独立验证通过。

公开 ZIP SHA-256：

```text
dc3016a74c4ea84e02941cc1ec5feb43e0ffd63529fcd9c7f3d4e649e4bc7938
```

- ZIP 完整性、摘要文件、GitHub 资产摘要、胶囊版本身份一致。
- `recipes` 返回 schema 2、候选版本和三种内置配方；执行前后文件/目录快照一致。
- 在 Windows 上以 89 字符目标目录名运行 FastAPI `init`、`preview`、`run` 与 `FINALIZE`。
- 实际生成和必需验证门成功，成品包含预期 ASGI 入口。
- 胶囊和外部清理日志自删除，`.scw-*` 工作区为零，生成项目无 scaffold 命名条目。

本地双构建字节一致属于准备证据；公开资产来自 Linux 标签工作流，本记录以上述公开下载
摘要为发布身份，不将本地 Windows 构建摘要混用为公开资产摘要。

## 状态

RC1 已发布并验收。`v2.3.0` 继续作为最新稳定版和回滚点；稳定版晋升尚未执行。
候选缺陷应进入后续候选提交和新标签，不移动或覆盖本次标签与资产。
