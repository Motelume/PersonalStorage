# PersonalStorage

Windows 本机资料整理工具。提供收件箱、审核与待办、AI管理、操作记录、规则与设置。保留网页版，也提供独立WebView2桌面应用。常规整理在本机完成；DeepSeek审核由用户选择资料、预览发送范围后手动触发，移动仍须预览并确认计划。

## 桌面版与AI

- 桌面版：双击打包目录中的`PersonalStorage.exe`，或代码目录的`启动桌面版.cmd`。不需要打开浏览器；打包版自带Python运行时，需要Windows WebView2 Runtime。
- 网页版：继续使用`启动界面.cmd`，原有功能保留。
- AI：进入“AI管理”保存DeepSeek密钥、启用审核并测试连接，默认模型为`deepseek-flash`。从收件箱或待办选择资料，预览名称、目录上下文及可选摘录后再发送。
- AI建议：返回建议后点击“采用AI建议并预览”，核对后执行。AI不能直接移动文件、覆盖人工锁定归属或修改长期规则。

详见[AI使用说明](docs/ai-guide.md)和[桌面版构建与分发](docs/desktop-guide.md)。

跨电脑使用与规则维护见[稳定架构与复用说明](docs/portability.md)。首次桌面启动可选择或经确认初始化资料目录；默认路径保存在本机状态目录，更新应用不会丢失。

## 目录架构与分类规则

- [完整目录树、顶层职责、细分规则与判断顺序](personalstorage/templates/classification.md)：供使用者和AI共同阅读。
- [配套结构化规则](personalstorage/templates/classification-config.json)：固定目录、细分建议、分类优先级、证据要求、归档状态及硬盘管理约定。

固定架构和通用规则随程序公开。实际课程、项目、比赛、个人事项及其绑定、真实资料和一次性例外保留在各电脑本机。

## 程序和资料分开

| 内容 | 存储位置 | 是否进入Git仓库 |
|---|---|---|
| Python程序、网页、测试和通用模板 | 当前程序目录 | 是 |
| 原资料、私有分类规则及任务 | 本机资料根目录，默认`D:\PersonalStorage` | 否 |
| 索引、审核状态、日志和管理快照 | `%LOCALAPPDATA%\PersonalStorage`及资料库管理目录 | 否 |
| 本机路径覆盖配置 | 程序目录`.local/config.json` | 否 |

程序目录与资料根目录必须分开。Git仓库用于同步代码，不同步资料、SQLite索引、实际分类配置或API密钥。密钥在本机经Windows当前用户DPAPI加密后保存，页面不回显原值；复制程序到另一台电脑后另行输入密钥。

## 启动

需要Windows和Python 3.11或以上版本。核心功能只有Python标准库依赖。PDF文字提取可选安装`requirements-optional.txt`。

已有配置的资料库：双击`启动界面.cmd`，浏览器自动打开本地界面。使用期间保留启动窗口。关闭浏览器不会停止服务；可以在规则与设置中退出本地服务，或关闭启动窗口。

首次使用，先创建空资料库：

```powershell
.\run.cmd setup
.\run.cmd validate
.\启动界面.cmd
```

`setup`创建11个编号顶层类别、12个固定子目录、管理入口及完整通用规则，不导入资料，不覆盖已有配置。学期、课程、项目、比赛、学科主题和事项按实际内容和证据创建，不批量预建空任务树。通用模板没有个人任务和自动执行条件；先使用界面人工选择目标，再按本机需要添加明确条件。

语义规则供AI或人工理解，程序自动执行仍只采用`executable_rules`白名单。更新仓库不会自动覆盖现有资料库；已有资料库可先对照完整规则，再合并需要的通用约定，保留自己的目录、任务和例外。

资料根目录优先级：`--root` > `PERSONALSTORAGE_ROOT` > `.local/config.json` > 本机已保存选择 > `D:\PersonalStorage`。例如：

```powershell
.\run.cmd --root "E:\PersonalStorage" validate
.\run.cmd --root "E:\PersonalStorage" serve
.\run.cmd configure-root "E:\PersonalStorage"
```

或创建不进入Git的`.local/config.json`：

```json
{"root":"E:\\PersonalStorage"}
```

通用规则升级先运行`run.cmd rules-status`或`run.cmd rules-update`预览，再用`run.cmd rules-update --approve`应用。它会备份并保留私人任务、执行条件和已修改字段，不重排原资料。

命令行必须在仓库目录运行。也可用`pip install -e .`安装本机命令`personalstorage`。

## 日常操作

- 收件箱：扫描、筛选、勾选、预览已选计划、执行。修改范围或目标后重新预览。
- 审核与待办：本地文字预览、选择现有目标目录、填写本次依据、离线JSON导出导入。待确认分流为独立动作。
- 已自行处理的缺失路径待办：点击“已在资源管理器处理”；记录移到“已处理记录”，不修改实际文件或猜测新位置。
- 操作记录：查看源与目标、校验和错误，核对恢复记录；撤销需要先检查再确认。
- 规则与设置：只读查看规则和任务，暂停/恢复，持续授权、启动/停止后台，下载管理快照。

启动界面不会自动授权或启动整理。后台启动后每60秒检查一次，不自动调用AI。关闭或暂停不会打断已进入执行器的批次。AI可手动使用DeepSeek或继续离线导出导入；OCR、跨盘搬移和树状总览尚未实现。

## 文件保护

保留原名、内容、修改时间及资料包内部结构；不删除、覆盖、去重或自动改名；只支持Windows同卷移动。旧预览、同名冲突、文件变化和人工锁定均经过检查。SQLite与逐项记录用于中断恢复。

管理快照包含索引、操作状态与规则文本，不包含原资料。它不能代替正文备份。路径改名或迁移请先停止服务、保留快照并迁移资料库身份；直接更改路径会使用不同的本机状态目录。

## 项目结构

```text
personalstorage/             核心模块、CLI和本机HTTP适配
  web_static/                HTML / CSS / JavaScript
  templates/                 不含个人信息的初始模板
tests/                       临时资料集中的单元测试
schemas/                     离线审核结果协议
tools/                       隔离浏览器验收及Git上传内容检查
docs/                        架构与开发说明
.github/workflows/           Windows自动化测试
```

架构说明见[docs/architecture.md](docs/architecture.md)，上传范围与数据边界见[SECURITY.md](SECURITY.md)。

## 验证与上传

```powershell
python -X utf8 -W error::ResourceWarning -m unittest discover -s tests -q
git add .
python -X utf8 tools/audit_repository.py
git diff --cached --stat
```

上传检查读取暂存区内容，拒绝意外资料目录、二进制文件、个人用户绝对路径、凭据和超限文件。不要使用`git add -f`绕过忽略规则。浏览器验收另需本机Edge和可用的Playwright包，执行`node tools/frontend_smoke.cjs`；这些不是应用运行依赖。
