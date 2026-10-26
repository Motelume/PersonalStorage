# 独立桌面版

桌面版使用pywebview及Windows WebView2 Runtime承载同一套网页。它有自己的原生应用窗口，不启动普通浏览器；网页入口继续保留。

## 使用

运行打包目录中的`PersonalStorage.exe`。整个目录一起保留，不能只复制exe而丢弃`_internal`。打包版已带Python运行时和PDF提取组件，不要求目标电脑安装Python；仍需要Windows及WebView2 Runtime。

默认资料根目录为`D:/PersonalStorage`。不可用时会弹出首次目录选择器，可选择现有资料库或经明确确认初始化通用架构。菜单“设置”可选择下次启动的目录，`--choose-root`可强制重选。本机默认选择保存在用户状态目录，与程序位置和版本分离。

`--root`、`PERSONALSTORAGE_ROOT`及程序旁`.local/config.json`仍可显式覆盖。源码CLI也可用`setup`新建空资料库，`configure-root`记住这台电脑的路径。详见[跨电脑复用](portability.md)。

应用窗口与本机服务同生命周期，关闭应用会请求停止后台；已经进入的操作或审核会完成收尾后退出。网页版独立启动时关闭网页不会关闭服务。同一资料库的文件执行与AI审核通过进程间锁协调。

API密钥、索引、审核历史和运行数据仍在当前电脑的私有状态目录，不嵌入exe、不放入便携包。新电脑需使用自己的资料路径和重新输入密钥。

## 从源码构建

```powershell
py -3.13 -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv-desktop\Scripts\python.exe tools/build_desktop.py
```

产物为`dist/PersonalStorage/PersonalStorage.exe`及依赖目录。构建只显式收集网页、通用模板、程序和运行库，不收集`.local`、资料目录或凭据。

自检会创建真实原生窗口，验证页面、AI管理页不回显密钥及打包版文档提取，然后自动关闭。报告须使用不存在的新文件名：

```powershell
dist\PersonalStorage\PersonalStorage.exe --self-test-report .local\native-test-new.json
```

网页版和AI流程的隔离验收分别是`tools/frontend_smoke.cjs`与`tools/frontend_ai_smoke.cjs`，需本机Edge和Playwright。后者只使用合成资料、临时密钥和模拟AI，不访问真实API。

实现依据：[pywebview安装](https://pywebview.flowrl.com/guide/installation.html)、[打包说明](https://pywebview.flowrl.com/guide/freezing.html)。
