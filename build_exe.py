import os
import subprocess
import shutil

def build():
    print("开始构建 EVE Asset Manager 可执行程序...")
    
    # 确保当前目录有 data 活文件
    if not os.path.exists("data"):
        os.makedirs("data")
        
    print("打包前端代码...")
    if os.path.exists("frontend"):
        # Windows上需要用 shell=True, 确保能找到 npm
        subprocess.run("npm install", cwd="frontend", shell=True, check=True)
        subprocess.run("npm run build", cwd="frontend", shell=True, check=True)
        print("前端打包完毕。")
    else:
        print("未找到 frontend 目录！打包停止。")
        return

    print("构建 PyInstaller 规范参数...")
    # PyInstaller 打包指令
    # 我们通过 --collect-all 将 uvicorn 和 fastapi 等模块静态绑定
    # 通过 --add-data 将 dist (前端构建物) 绑定到跟目录下的 dist
    cmd = [
        "pyinstaller",
        "--name", "EVE 资产管理本地版",
        "--onefile",
        "--noconsole",
        "--clean",
        "--collect-all", "fastapi",
        "--collect-all", "uvicorn",
        "--collect-all", "pydantic",
        "--collect-all", "bcrypt",
        "--collect-all", "passlib",
        # 支持托盘的必要系统库显式绑定
        "--hidden-import", "pystray",
        "--hidden-import", "PIL",
        # Windows 分隔符是分号 ;
        "--add-data", "frontend/dist;dist",
        "--add-data", "data/eve_universe_serenity.sqlite;data",
        "--add-data", "data/eve_universe_tranquility.sqlite;data",
        "--add-data", "data/eve_universe_infinity.sqlite;data",
        "--icon", "frontend/dist/favicon.ico",
        "launcher.py"
    ]
    
    print("执行 PyInstaller (稍候片刻，这可能会花费几分钟)...")
    subprocess.run(cmd, check=True)
    
    print("构建完成！请在 dist 目录下寻找生成的 'EVE 资产管理平台.exe'")
    print("注意: 运行时它会读取/在它所在目录旁创建一个名为 'data' 的文件夹。")

if __name__ == "__main__":
    build()
