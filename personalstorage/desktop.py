"""Native WebView2 application hosting the same loopback web frontend."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time

from .config import default_root,save_root,validate_location
from .common import SafetyError
from .web import make_server


def choose_library(window=None):
    """Native user selection; never silently initialize or overwrite a library."""
    while True:
        if window is None:
            import tkinter
            from tkinter import filedialog
            picker=tkinter.Tk();picker.withdraw();picker.attributes('-topmost',True)
            try:selected=filedialog.askdirectory(title='选择PersonalStorage资料目录',mustexist=True,parent=picker)
            finally:picker.destroy()
        else:
            import webview
            selection=window.create_file_dialog(webview.FileDialog.FOLDER)
            selected=selection[0] if selection else None
        if not selected:return None
        try:
            root=validate_location(selected)
            if not (root/'_整理规则/分类配置.json').is_file():
                answer=ctypes.windll.user32.MessageBoxW(None,
                    '这个目录还没有PersonalStorage配置。\n\n是否创建通用分类架构和规则？\n已有文件不会被移动、修改或覆盖。\n\n'+str(root),
                    '初始化资料目录',0x24)
                if answer!=6:continue
                from .setup import setup_library
                setup_library(root)
            return save_root(root)
        except Exception as exc:ctypes.windll.user32.MessageBoxW(None,str(exc),'请选择其他资料目录',0x10)


def main(argv=None):
    parser=argparse.ArgumentParser(description='PersonalStorage 独立桌面应用')
    parser.add_argument('--root',default=default_root());parser.add_argument('--profile')
    parser.add_argument('--choose-root',action='store_true',help='重新选择这台电脑的资料目录')
    parser.add_argument('--self-test-report',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if os.name!='nt':raise SafetyError('桌面版当前只支持Windows')
    # Windowed executables have no console. Give pythonnet and library loggers
    # Unicode-safe streams before importing them; shutdown warnings may include
    # Chinese paths and must never raise a second exception in a .NET callback.
    if getattr(sys,'frozen',False):
        for name in ('stdout','stderr'):
            stream=getattr(sys,name)
            if stream is None:setattr(sys,name,open(os.devnull,'w',encoding='utf-8',errors='backslashreplace'))
            elif hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='backslashreplace')
    try:import webview
    except ImportError:raise SafetyError('桌面组件未安装，请使用打包的PersonalStorage.exe或安装requirements-desktop.txt') from None
    if args.choose_root or not (Path(args.root)/'_整理规则/分类配置.json').is_file():
        if args.self_test_report:raise SafetyError('桌面自检须指定已配置的资料目录')
        selected=choose_library()
        if not selected:return 0
        args.root=selected
    server=make_server(args.root,args.profile)
    server.desktop.native_mode=True
    server_thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.2},daemon=True)
    server_thread.start()
    webview.settings['ALLOW_DOWNLOADS']=True
    webview.settings['ALLOW_FILE_URLS']=False
    webview.settings['OPEN_DEVTOOLS_IN_DEBUG']=False
    from webview.menu import Menu,MenuAction
    def change_root():
        selected=choose_library(window)
        if selected:ctypes.windll.user32.MessageBoxW(None,'已保存本机默认资料目录，下次无路径覆盖启动时生效。\n当前窗口继续使用原资料目录。\n\n'+selected,'PersonalStorage',0)
    window=webview.create_window('PersonalStorage',server.desktop.url,width=1400,height=940,
        min_size=(850,650),background_color='#f5f7fb',text_select=True,
        menu=[Menu('设置',[MenuAction('选择资料目录（下次启动生效）',change_root)])])
    def pick_inbox():
        selected=window.create_file_dialog(webview.FileDialog.FOLDER)
        return selected[0] if selected else None
    server.desktop.folder_picker=pick_inbox
    finished=threading.Event()
    def closed():
        finished.set();server.desktop.stop.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    window.events.closed+=closed
    def observe_service():
        server_thread.join()
        if not finished.is_set():
            try:window.destroy()
            except Exception:pass
    threading.Thread(target=observe_service,daemon=True).start()
    def smoke():
        if not args.self_test_report:return
        result={'ok':False,'engine':'edgechromium','ui_mode':'desktop'}
        try:
            for _ in range(200):
                if finished.is_set():raise SafetyError('测试窗口提前关闭')
                try:
                    ready=window.evaluate_js('typeof S !== "undefined" && !!S.boot && !S.busy')
                    if ready:break
                except Exception:pass
                time.sleep(.1)
            else:raise SafetyError('桌面页面未完成加载')
            data=window.evaluate_js('({version:S.boot.app_version,root:S.boot.status.root,mode:S.boot.ui_mode,pages:document.querySelectorAll("nav button").length,model_enabled:S.boot.model_enabled})')
            if data['mode']!='desktop' or data['pages']!=7:raise SafetyError('桌面界面状态不正确')
            window.evaluate_js('load("overview");true')
            for _ in range(100):
                if window.evaluate_js('document.querySelectorAll(".ov-node").length > 0'):break
                time.sleep(.1)
            else:raise SafetyError('关系图未完成加载')
            result['overview_nodes']=window.evaluate_js('document.querySelectorAll(".ov-node").length')
            window.evaluate_js('OV.mode="tree";render();true')
            if not window.evaluate_js('document.querySelectorAll(".ov-tree-row").length > 0'):raise SafetyError('目录树切换失败')
            result['overview_views']=['graph','tree']
            window.evaluate_js('load("inboxes");true')
            for _ in range(100):
                if window.evaluate_js('document.getElementById("inbox-mode") !== null'):break
                time.sleep(.1)
            else:raise SafetyError('收件箱管理页面未显示')
            modes=window.evaluate_js('Array.from(document.getElementById("inbox-mode").options).map(o=>o.value)')
            if modes!=['copy','move']:raise SafetyError('收件箱两种归档方式未显示')
            result['inbox_modes']=modes
            window.evaluate_js('load("ai");true')
            for _ in range(100):
                if window.evaluate_js('document.getElementById("ai-key") !== null'):break
                time.sleep(.1)
            else:raise SafetyError('AI管理页面未显示')
            if not window.evaluate_js('document.getElementById("ai-key").value === ""'):raise SafetyError('页面不应回显密钥')
            import tempfile,zipfile
            from .extraction import extract
            with tempfile.TemporaryDirectory(prefix='personalstorage-native-test-') as folder:
                sample=Path(folder)/'example.docx'
                with zipfile.ZipFile(sample,'w') as archive:
                    archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>native extraction sample</w:t></w:r></w:p></w:body></w:document>')
                parsed=extract(sample)
                if 'native extraction sample' not in parsed.get('text',''):raise SafetyError('打包版文档提取失败')
            result['bundled_document_extraction']=True
            from .ocr import self_test
            result['bundled_offline_ocr']=self_test()
            from .rule_regex import search as regex_search
            if not regex_search('native.*sample','native regex sample'):raise SafetyError('打包版隔离正则匹配失败')
            result['bundled_bounded_regex']=True
            window.evaluate_js('load("settings");true')
            for _ in range(100):
                if window.evaluate_js('typeof RM !== "undefined" && !!RM.status && !S.busy'):break
                time.sleep(.1)
            else:raise SafetyError('规则管理未完成加载')
            result['rule_tabs']=window.evaluate_js('Array.from(document.querySelectorAll(".rm-tabs button")).map(b=>b.textContent)')
            if len(result['rule_tabs'])!=5:raise SafetyError('规则管理页签不完整')
            window.evaluate_js('RM.tab="runtime";render();true')
            for _ in range(100):
                if window.evaluate_js('document.getElementById("ocr-enabled") !== null'):break
                time.sleep(.1)
            else:raise SafetyError('OCR设置面板未显示')
            result.update(ok=True,**data)
        except Exception as exc:result['error']=str(exc)
        finally:
            path=Path(args.self_test_report);path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
            window.destroy()
    try:
        # Private browser caches belong to this window, never to the shared
        # management profile. Concurrent windows must not delete each other's
        # WebView2 data at shutdown.
        webview.start(smoke,gui='edgechromium',debug=False,private_mode=True)
    finally:
        finished.set();server.desktop.stop.set();server.shutdown();server_thread.join(timeout=5)
        server.server_close();server.desktop.close()
    return 0


def entry():
    if len(sys.argv)==4 and sys.argv[1]=='--regex-worker':
        from .rule_regex import worker
        worker(sys.argv[2],sys.argv[3]);return 0
    if len(sys.argv)==5 and sys.argv[1]=='--extract-worker':
        from .extraction import _extract_local
        target=Path(sys.argv[4])
        with target.open('x',encoding='utf-8') as stream:json.dump(_extract_local(sys.argv[2],int(sys.argv[3])),stream,ensure_ascii=False)
        return 0
    try:return main()
    except Exception as exc:
        message=str(exc)
        if os.name=='nt':ctypes.windll.user32.MessageBoxW(None,'启动失败：'+message+'\n\n请确认资料目录已配置，且已安装Microsoft WebView2 Runtime。','PersonalStorage',0x10)
        return 2


if __name__=='__main__':raise SystemExit(entry())
