import os
import sys
import io

# [HOTFIX] 针对 PyInstaller --noconsole 模式的终端模拟
# 因为没有控制台窗口，标准输入输出被设为了 None，Uvicorn 在初始化日志时会因无法找到 .isatty() 崩溃
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# 修正 Python 的搜索路径，使得 PyInstaller 打包及直接运行时能找到 backend 和 scripts 文件夹
if getattr(sys, 'frozen', False):
    root_path = sys._MEIPASS
else:
    root_path = os.path.dirname(os.path.abspath(__file__))

if root_path not in sys.path:
    sys.path.insert(0, root_path)

import uvicorn
import webbrowser
import threading
import time

from backend.main import app
import pystray
from pystray import MenuItem as item
from PIL import Image

def open_browser():
    # 延迟等待 fastapi 服务器启动
    time.sleep(2.0)
    print("正在尝试自动唤起系统默认浏览器...")
    webbrowser.open_new_tab("http://127.0.0.1:8001")

def run_server():
    print("正在启动 EVE 本地资产管理系统...")
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="error") # 隐藏不必要的繁杂日志

def on_open_clicked(icon, item):
    webbrowser.open_new_tab("http://127.0.0.1:8001")

def on_exit_clicked(icon, item):
    icon.stop()
    os._exit(0) # 干净彻底关闭后台服务和托盘

if __name__ == "__main__":
    # 检测是否已经有一个相同的程序在后台守护运行
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        # 尝试连接我们的 uvicorn 端口
        if s.connect_ex(("127.0.0.1", 8001)) == 0:
            s.close()
            # 如果连接成功意味着已经有一个程序实例在运行
            print("后台服务已启动，正在直接唤起面板...")
            webbrowser.open_new_tab("http://127.0.0.1:8001")
            sys.exit(0)
    except Exception:
        pass

    # 使用守护线程自动打开浏览器页签
    t_browser = threading.Thread(target=open_browser)
    t_browser.daemon = True
    t_browser.start()
    
    # 以后台常驻保护方式运行 API 服务端
    t_server = threading.Thread(target=run_server)
    t_server.daemon = True
    t_server.start()

    # 获取图标逻辑判断
    icon_path = os.path.join(root_path, "dist", "favicon.ico")
    if os.path.exists(icon_path):
        image = Image.open(icon_path)
    else:
        # 当作保底的一张纯色图标
        image = Image.new('RGB', (64, 64), color=(64, 158, 255))
        
    menu = pystray.Menu(
        item('打开 资产管理 面板', on_open_clicked, default=True),
        item('彻底退出程序', on_exit_clicked)
    )

    # 主线程完全让位给系统原生托盘事件大循环，实现完美无控制台进程防僵尸
    icon = pystray.Icon("EVE资产管理器", image, "EVE资产管理后台", menu)
    icon.run()
