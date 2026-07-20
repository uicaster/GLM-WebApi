"""
PyInstaller build script for GLM API Proxy GUI version.
Run: python build_gui.py
Produces: GLM_Api.exe (直接输出到项目根目录 C:\\Users\\Administrator\\Documents\\Project\\GLM Api)

注意：PyInstaller --onedir 模式会在 --distpath 下自动创建与 --name 同名的子文件夹，
      所以本脚本先打包到临时目录，再把内容（exe + _internal）移动到项目根目录。
"""
import PyInstaller.__main__
import os
import shutil
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_FILE = os.path.join(SCRIPT_DIR, "app_icon.ico")
APP_NAME = "GLM_Api"

# 1. 先打包到临时目录
tmp_dist = tempfile.mkdtemp(prefix="glm_build_")
tmp_work = tempfile.mkdtemp(prefix="glm_work_")
tmp_spec = tempfile.mkdtemp(prefix="glm_spec_")

print(f"[1/3] PyInstaller 打包中... (临时输出目录: {tmp_dist})")
PyInstaller.__main__.run([
    os.path.join(SCRIPT_DIR, "gui_app.py"),
    f"--name={APP_NAME}",
    "--onedir",
    "--windowed",          # GUI mode, no console window
    f"--icon={ICON_FILE}",
    f"--add-data={os.path.join(SCRIPT_DIR, 'chatglm_api.py')}{os.pathsep}.",
    f"--add-data={ICON_FILE}{os.pathsep}.",
    "--hidden-import=chatglm_api",
    "--hidden-import=waitress",
    "--hidden-import=flask",
    "--hidden-import=pystray",
    "--hidden-import=PIL",
    "--hidden-import=PIL.Image",
    "--hidden-import=PIL.ImageDraw",
    "--collect-all=waitress",
    "--collect-all=pystray",
    "--collect-all=PIL",
    "--noconfirm",
    f"--distpath={tmp_dist}",
    f"--workpath={tmp_work}",
    f"--specpath={tmp_spec}",
])

# 2. 把临时输出目录中的内容移动到项目根目录
src_dir = os.path.join(tmp_dist, APP_NAME)
print(f"[2/3] 移动文件到项目根目录: {SCRIPT_DIR}")

# 先清理根目录下可能存在的旧文件（支持新旧 exe 名）
old_names = [APP_NAME, "glm_api_gui"]  # 新名 + 旧名，便于迁移
old_exe = None
for name in old_names:
    candidate = os.path.join(SCRIPT_DIR, f"{name}.exe")
    if os.path.exists(candidate):
        os.remove(candidate)
        old_exe = candidate
old_internal = os.path.join(SCRIPT_DIR, "_internal")
if os.path.exists(old_internal):
    shutil.rmtree(old_internal, ignore_errors=True)

# 安全检查：确认旧目录已完全删除，否则中断（避免嵌套 _internal\_internal 问题）
if os.path.exists(old_internal):
    print(f"\n✗ 错误：无法删除旧目录 {old_internal}")
    print("  可能有进程正在使用这些文件。请先关闭 GLM_Api.exe，然后重试。")
    shutil.rmtree(tmp_dist, ignore_errors=True)
    shutil.rmtree(tmp_work, ignore_errors=True)
    shutil.rmtree(tmp_spec, ignore_errors=True)
    raise SystemExit(1)

# 移动 exe
new_exe = os.path.join(SCRIPT_DIR, f"{APP_NAME}.exe")
shutil.move(os.path.join(src_dir, f"{APP_NAME}.exe"), new_exe)
# 移动 _internal
shutil.move(os.path.join(src_dir, "_internal"), old_internal)

# 3. 清理临时目录
print("[3/3] 清理临时目录...")
shutil.rmtree(tmp_dist, ignore_errors=True)
shutil.rmtree(tmp_work, ignore_errors=True)
shutil.rmtree(tmp_spec, ignore_errors=True)

print()
print(f"✓ 打包完成！exe 位置: {new_exe}")
print(f"✓ 依赖目录位置: {old_internal}")
