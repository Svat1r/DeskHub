"""Сборка DeskHub: программа (PyInstaller, папка) -> payload.zip -> один установщик DeskHub-Setup.exe.

Запуск:  python build.py
Результат: dist/DeskHub-Setup.exe (и dist/DeskHub/ — сама программа, для отладки).
"""
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
# короткий рабочий путь: у PyInstaller бывают ошибки с очень длинными путями
WORK = Path(tempfile.gettempdir()) / "dh_build"


def pyinstaller(*args):
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--workpath", str(WORK),
                    "--specpath", str(WORK), *args], check=True, cwd=ROOT)


def main():
    from deskhub import __version__
    print(f"== DeskHub {__version__}")
    icon = str(ROOT / "assets" / "deskhub.ico")

    # 1. сама программа
    pyinstaller("--windowed", "--name", "DeskHub", "--icon", icon, "--distpath", str(DIST),
                "--add-data", f"{ROOT / 'tools' / 'PresentMon.exe'};tools",
                "--collect-all", "winrt", "--collect-all", "imageio_ffmpeg", "--collect-submodules", "comtypes",
                "--hidden-import", "pycaw.pycaw", "--hidden-import", "anthropic",
                str(ROOT / "DeskHub.py"))

    # 2. архив для установщика
    payload = WORK / "payload.zip"
    app_dir = DIST / "DeskHub"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in app_dir.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(app_dir))
    print(f"payload: {payload.stat().st_size / 1024 / 1024:.1f} МБ")

    # 3. установщик одним файлом
    pyinstaller("--onefile", "--windowed", "--name", "DeskHub-Setup", "--icon", icon, "--distpath", str(DIST),
                "--add-data", f"{payload};.", "--add-data", f"{icon};.", str(ROOT / "installer.py"))
    shutil.rmtree(WORK, ignore_errors=True)
    setup = DIST / "DeskHub-Setup.exe"
    print(f"== готово: {setup} ({setup.stat().st_size / 1024 / 1024:.1f} МБ)")


if __name__ == "__main__":
    main()
