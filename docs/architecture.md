# 架构与维护

浏览器 → `web.py`本机服务 → `Application` → 计划、安全执行器与SQLite。CLI共用同一Application入口。

| 模块 | 职责 |
|---|---|
| config / setup | 本机根目录设置、通用模板初始化 |
| discovery / extraction | 收件箱发现、稳定性检查、只读文字提取 |
| policy / planner | 白名单规则、任务上下文、单次人工决定与预览 |
| executor / recovery | 同卷不覆盖移动、前后校验、中断恢复与受限撤销 |
| state / maintenance | 身份隔离、索引与版本、观测和内容变化 |
| review / reports | 离线审核、手动完成待办、历史与状态 |
| snapshot | 一致性管理数据库快照和规则文本说明 |
| web / web_static | 回环接口、单工作队列与四页前端 |
| ai / model_client / credentials | 明确授权的DeepSeek审核、用量管理、DPAPI凭据；与移动执行器分离 |
| desktop | 内嵌WebView2窗口，共用网页与本机HTTP适配 |

每个工作线程创建自己的SQLite连接。长任务进入单工作队列；所有实际移动仍受进程间锁约束。HTTP对象句柄和预览属于当前服务会话，重启后重新读取列表及生成预览。

## 配置与资料

运行时读取资料根目录下的`_整理规则/分类配置.json`。程序包内模板只用于新建空资料库，不自动覆盖已有规则。语义规则、任务和人工例外留在对应电脑本机；仓库中的示例不是个人分类配置。

模板公开固定架构、细分分类边界及判断顺序。`initialization.fixed_subdirectories`用于创建固定子目录；`subcategory_rules`和`classification_rules`供人工与AI读取。学期、课程、任务和主题按需创建，尖括号占位符不创建为实体目录。语义规则不是自动执行条件；自动执行仍使用`executable_rules`。

状态目录按规范化根路径派生身份。资料根路径改名后，应在停止服务和其他CLI执行器的前提下备份并迁移状态身份，保留历史文件相对路径。旧预览必须失效；持续授权应撤销并在新身份下重新决定。历史操作仍按相对源/目标解释，不自动改写原资料正文。

## 测试

`unittest discover`使用临时目录，不在真实资料库移动文件。Web测试覆盖来源校验、已选独立计划、过期预览、待确认分流、撤销检查、授权和启动分离、外部已处理归档与历史保留。

`tools/frontend_smoke.cjs`启动临时资料服务，并通过本机Edge验收完整操作。临时资料由`tools/web_fixture.py`生成。测试退出后销毁临时目录。应用运行不依赖Node或Playwright。
