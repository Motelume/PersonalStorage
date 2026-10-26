# PersonalStorage

Windows 本机资料整理工具。提供收件箱、审核与待办、操作记录、规则与设置四个页面；使用现有分类规则生成计划，在用户预览后移动文件。模型API保持关闭。

## 程序和资料分开

| 内容 | 存储位置 | 是否进入Git仓库 |
|---|---|---|
| Python程序、网页、测试和通用模板 | 当前程序目录 | 是 |
| 原资料、私有分类规则及任务 | 本机资料根目录，默认`D:\PersonalStorage` | 否 |
| 索引、审核状态、日志和管理快照 | `%LOCALAPPDATA%\PersonalStorage`及资料库管理目录 | 否 |
| 本机路径覆盖配置 | 程序目录`.local/config.json` | 否 |

程序目录与资料根目录必须分开。Git仓库用于同步代码；不用于同步两台电脑的资料、SQLite索引或个人分类配置。即便仓库为私有，也不要上传这些数据。

## 启动

需要Windows和Python 3.11或以上版本。核心功能只有Python标准库依赖。PDF文字提取可选安装`requirements-optional.txt`。

已有配置的资料库：双击`启动界面.cmd`，浏览器自动打开本地界面。使用期间保留启动窗口。关闭浏览器不会停止服务；可以在规则与设置中退出本地服务，或关闭启动窗口。

首次使用，先创建空资料库：

```powershell
.\run.cmd setup
.\run.cmd validate
.\启动界面.cmd
```

`setup`创建分类目录、收件箱和通用规则，不导入资料，不覆盖已有配置。通用模板没有个人任务和自动执行规则；先使用界面人工选择目标，再按本机需要添加明确规则。

资料根目录优先级：命令行`--root` > `PERSONALSTORAGE_ROOT`环境变量 > `.local/config.json`的`root` > `D:\PersonalStorage`。例如：

```powershell
.\run.cmd --root "E:\PersonalStorage" validate
.\run.cmd --root "E:\PersonalStorage" serve
```

或创建不进入Git的`.local/config.json`：

```json
{"root":"E:\\PersonalStorage"}
```

命令行必须在仓库目录运行。也可用`pip install -e .`安装本机命令`personalstorage`。

## 日常操作

- 收件箱：扫描、筛选、勾选、预览已选计划、执行。修改范围或目标后重新预览。
- 审核与待办：本地文字预览、选择现有目标目录、填写本次依据、离线JSON导出导入。待确认分流为独立动作。
- 已自行处理的缺失路径待办：点击“已在资源管理器处理”；记录移到“已处理记录”，不修改实际文件或猜测新位置。
- 操作记录：查看源与目标、校验和错误，核对恢复记录；撤销需要先检查再确认。
- 规则与设置：只读查看规则和任务，暂停/恢复，持续授权、启动/停止后台，下载管理快照。

启动界面不会自动授权或启动整理。后台启动后每60秒检查一次；关闭或暂停不会打断已经进入执行器的批次。模型审核采用离线导出导入；没有启用网络模型、OCR、跨盘搬移或文件夹树状总览。

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
