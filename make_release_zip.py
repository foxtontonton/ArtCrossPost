# -*- coding: utf-8 -*-
"""
make_release_zip.py
构建完整项目发行与源码打包包，保证目录树结构（尤其是 dispatchers/ 独立包结构）100% 完整保留。
"""
import os
import zipfile
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 包含的文件和目录
INCLUDE_FILES = [
    "app.py",
    "censor_engine.py",
    "config.py",
    "diagnostics.py",
    "logger.py",
    "visual_editor.py",
    "watermark_engine.py",
    "x_text_utils.py",
    "requirements.txt",
    "start.bat",
    "README.md",
    "PROJECT_HANDOVER.md",
    "MUSE_DEBUG_HANDOVER.md",
]

INCLUDE_DIRS = [
    "dispatchers",
]

OUTPUT_ZIP = os.path.join(BASE_DIR, "artcrosspost_release.zip")
FALLBACK_ZIP = os.path.join(BASE_DIR, "artcrosspost_code_only.zip")

def build_zip():
    print(f"[1/3] 开始打包发行包: {OUTPUT_ZIP} ...")
    
    zip_targets = [OUTPUT_ZIP, FALLBACK_ZIP]
    for zpath in zip_targets:
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in INCLUDE_FILES:
                fpath = os.path.join(BASE_DIR, fname)
                if os.path.exists(fpath):
                    zf.write(fpath, arcname=fname)
                    print(f"  + 添加文件: {fname}")
                else:
                    print(f"  ! 警告: 文件未找到: {fname}")
                    
            for dname in INCLUDE_DIRS:
                dpath = os.path.join(BASE_DIR, dname)
                if os.path.exists(dpath):
                    for root, _, files in os.walk(dpath):
                        for file in files:
                            if file.endswith((".pyc", ".pyo")) or "__pycache__" in root:
                                continue
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, BASE_DIR)
                            zf.write(full_path, arcname=rel_path)
                            print(f"  + 添加目录文件: {rel_path}")

            # 确保 data 目录占位符存在（方便解压即用）
            data_info = zipfile.ZipInfo("data/")
            zf.writestr(data_info, "")
            zf.writestr("data/.gitkeep", "")

    print(f"\n[2/3] 打包完成！已生成:")
    print(f"  - {OUTPUT_ZIP} ({os.path.getsize(OUTPUT_ZIP)} bytes)")
    print(f"  - {FALLBACK_ZIP} ({os.path.getsize(FALLBACK_ZIP)} bytes)")

def test_unzip_and_import():
    print("\n[3/3] 验证解压与独立环境导入测试...")
    temp_dir = os.path.join(BASE_DIR, "temp_verify_extract")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        with zipfile.ZipFile(OUTPUT_ZIP, "r") as zf:
            zf.extractall(temp_dir)
            
        test_script = (
            "import sys, os\n"
            "sys.path.insert(0, os.getcwd())\n"
            "from dispatchers import get_all_dispatchers\n"
            "from dispatchers.base import BaseDispatcher\n"
            "from dispatchers.x_twitter import XTwitterDispatcher\n"
            "from dispatchers.deviantart import DeviantArtDispatcher\n"
            "from dispatchers.monetization import PatreonDispatcher, KofiDispatcher\n"
            "import diagnostics\n"
            "import app\n"
            "print('>>> ALL IMPORTS PASSED SUCCESSFULLY!')\n"
        )
        test_file = os.path.join(temp_dir, "test_imports.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(test_script)
            
        res = subprocess.run([sys.executable, "test_imports.py"], cwd=temp_dir, capture_output=True, text=True)
        print("STDOUT:", res.stdout.strip())
        if res.stderr:
            print("STDERR:", res.stderr.strip())
            
        if res.returncode == 0 and "ALL IMPORTS PASSED SUCCESSFULLY!" in res.stdout:
            print("SUCCESS: 验证通过！解压后子包与全量模块导入完全正常，零导入错误！")
        else:
            raise RuntimeError(f"验证失败: returncode={res.returncode}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("临时验证目录已清理完毕。")

if __name__ == "__main__":
    build_zip()
    test_unzip_and_import()
