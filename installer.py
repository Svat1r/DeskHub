"""Установщик DeskHub (собирается в один DeskHub-Setup.exe с архивом программы внутри).

Ставит программу в %LOCALAPPDATA%\\Programs\\DeskHub без прав администратора, создаёт ярлыки
и запись в «Установленных приложениях». Режим --silent используется автообновлением.
"""
import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import winreg
import zipfile
from pathlib import Path

from deskhub import __version__ as VERSION

APP = "DeskHub"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP
EXE = INSTALL_DIR / f"{APP}.exe"
START_MENU = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP}.lnk"
DESKTOP = Path(os.environ["USERPROFILE"]) / "Desktop" / f"{APP}.lnk"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP}"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def payload() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload.zip"


def close_running():
    """Закрывает запущенный DeskHub (и ждёт, пока освободятся файлы)."""
    subprocess.run(["taskkill", "/IM", f"{APP}.exe", "/F"], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(30):
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {APP}.exe", "/NH"], capture_output=True, text=True,
                           creationflags=NO_WINDOW)
        if f"{APP}.exe".lower() not in r.stdout.lower():
            return
        time.sleep(0.3)


def make_shortcut(link: Path, target: Path):
    link.parent.mkdir(parents=True, exist_ok=True)
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
          f"$s.TargetPath='{target}';$s.WorkingDirectory='{target.parent}';$s.IconLocation='{target},0';"
          f"$s.Description='DeskHub — виджеты и инструменты';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                   capture_output=True, creationflags=NO_WINDOW)


def register_uninstall(size_kb: int):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in (("DisplayName", APP), ("DisplayVersion", VERSION), ("Publisher", "Svat1r"),
                            ("DisplayIcon", str(EXE)), ("InstallLocation", str(INSTALL_DIR)),
                            ("UninstallString", f'"{EXE}" --uninstall'), ("URLInfoAbout",
                                                                          "https://github.com/Svat1r/DeskHub")):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)


def fix_autostart():
    """Если автозапуск уже был включён (например, на старую папку) — направляем на новую."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            winreg.QueryValueEx(k, APP)
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, f'"{EXE}" --autostart')
    except OSError:
        pass


def install(desktop_shortcut: bool, progress=lambda v, t: None):
    progress(5, "Закрываю DeskHub…")
    close_running()
    progress(15, "Удаляю старую версию…")
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(payload()) as z:
        names = z.infolist()
        total = sum(i.file_size for i in names) or 1
        done = 0
        for i in names:
            z.extract(i, INSTALL_DIR)
            done += i.file_size
            progress(15 + int(75 * done / total), "Копирую файлы…")
    progress(92, "Создаю ярлыки…")
    make_shortcut(START_MENU, EXE)
    if desktop_shortcut:
        make_shortcut(DESKTOP, EXE)
    register_uninstall(total // 1024)
    fix_autostart()
    progress(100, "Готово")


def launch():
    # запускаем через explorer — так DeskHub стартует как обычная программа пользователя
    subprocess.Popen(["explorer.exe", str(EXE)], creationflags=NO_WINDOW)


def run_silent():
    try:
        install(desktop_shortcut=DESKTOP.exists())
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(None, f"Не удалось обновить DeskHub:\n{e}", APP, 0x10)
        return
    launch()


def run_gui():
    import tkinter as tk
    from tkinter import ttk

    BG, FG, SUB, ACCENT = "#161922", "#f1f3f8", "#9097a8", "#4a78d6"
    root = tk.Tk()
    root.title(f"Установка {APP}")
    root.configure(bg=BG)
    root.resizable(False, False)
    w, h = 460, 300
    root.geometry(f"{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 2}")
    try:
        root.iconbitmap(str(Path(getattr(sys, "_MEIPASS", ".")) / "deskhub.ico"))
    except tk.TclError:
        pass

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Bar.Horizontal.TProgressbar", troughcolor="#252a36", background=ACCENT, bordercolor=BG,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)

    tk.Label(root, text=APP, bg=BG, fg=FG, font=("Segoe UI Variable Display", 26, "bold")).pack(pady=(30, 0))
    tk.Label(root, text=f"версия {VERSION} · виджеты, скриншоты, запись экрана, ИИ", bg=BG, fg=SUB,
             font=("Segoe UI", 10)).pack()
    desk = tk.BooleanVar(value=True)
    cb = tk.Checkbutton(root, text="Ярлык на рабочем столе", variable=desk, bg=BG, fg=FG, selectcolor="#252a36",
                        activebackground=BG, activeforeground=FG, font=("Segoe UI", 10), bd=0, highlightthickness=0)
    cb.pack(pady=(26, 6))
    status = tk.Label(root, text=f"Будет установлено в {INSTALL_DIR}", bg=BG, fg=SUB, font=("Segoe UI", 9),
                      wraplength=420)
    status.pack()
    bar = ttk.Progressbar(root, style="Bar.Horizontal.TProgressbar", length=380, maximum=100)
    btn = tk.Button(root, text="Установить", bg=ACCENT, fg="white", activebackground="#5b88e6",
                    activeforeground="white", relief="flat", font=("Segoe UI", 12, "bold"), padx=28, pady=6,
                    cursor="hand2", bd=0)
    btn.pack(pady=18)

    def progress(v, text):
        root.after(0, lambda: (bar.configure(value=v), status.configure(text=text)))

    def worker():
        try:
            install(desk.get(), progress)
        except Exception as e:
            root.after(0, lambda: (status.configure(text=f"Ошибка: {e}", fg="#ff7070"),
                                   btn.configure(text="Закрыть", state="normal", command=root.destroy)))
            return
        root.after(0, done)

    def done():
        status.configure(text="DeskHub установлен и запущен. Значок — в трее у часов.", fg=FG)
        btn.configure(text="Готово", state="normal", command=root.destroy)
        launch()

    def start():
        btn.configure(state="disabled", text="Устанавливаю…")
        cb.configure(state="disabled")
        bar.pack(before=btn, pady=(12, 0))
        threading.Thread(target=worker, daemon=True).start()

    btn.configure(command=start)
    root.mainloop()


if __name__ == "__main__":
    if "--silent" in sys.argv:
        run_silent()
    else:
        run_gui()
