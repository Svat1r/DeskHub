"""Помощник FPS. Запускается с правами администратора (это требование ETW/PresentMon),
читает поток кадров PresentMon и отправляет основной программе FPS по процессам через localhost."""
import collections
import csv
import json
import socket
import subprocess
import sys
import threading
import time

WINDOW = 1.0  # секунды для расчёта FPS


def run(port: int, token: str, presentmon: str):
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.sendall((token + "\n").encode())
    sock.settimeout(None)

    proc = subprocess.Popen(
        [presentmon, "--output_stdout", "--no_console_stats", "--v1_metrics", "--stop_existing_session",
         "--session_name", "DeskHubFPS", "--no_track_input", "--no_track_display",
         "--exclude", "dwm.exe", "--exclude", "explorer.exe"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    frames = collections.defaultdict(collections.deque)  # pid -> deque[(t, ms)]
    apps = {}
    lock = threading.Lock()
    alive = True

    def reader():
        nonlocal alive
        rows = csv.reader(proc.stdout)
        header = next(rows, None)
        if not header:
            alive = False
            return
        idx = {name.lower(): i for i, name in enumerate(header)}
        i_app, i_pid = idx.get("application"), idx.get("processid")
        i_ms = next((idx[k] for k in ("msbetweenpresents", "frametime", "msbetweenappstart") if k in idx), None)
        if None in (i_app, i_pid, i_ms):
            alive = False
            return
        for row in rows:
            try:
                pid, ms = int(row[i_pid]), float(row[i_ms])
            except (ValueError, IndexError, TypeError):
                continue
            now = time.monotonic()
            with lock:
                frames[pid].append((now, ms))
                apps[pid] = row[i_app]
        alive = False

    def watch_socket():
        nonlocal alive
        try:
            while sock.recv(64):
                pass
        except OSError:
            pass
        alive = False

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=watch_socket, daemon=True).start()

    try:
        while alive:
            time.sleep(0.5)
            now = time.monotonic()
            out = {}
            with lock:
                for pid, dq in list(frames.items()):
                    while dq and now - dq[0][0] > 5:
                        dq.popleft()
                    recent = [ms for t, ms in dq if now - t <= WINDOW]
                    if not recent:
                        if not dq:
                            del frames[pid]
                        continue
                    allms = sorted((ms for _, ms in dq), reverse=True)
                    worst = allms[: max(1, len(allms) // 100)]
                    avg = sum(recent) / len(recent)
                    out[pid] = {
                        "app": apps.get(pid, ""),
                        "fps": round(1000 / avg, 1) if avg > 0 else 0,
                        "ft": round(avg, 2),
                        "low1": round(1000 / (sum(worst) / len(worst)), 1) if worst[0] > 0 else 0,
                    }
            try:
                sock.sendall((json.dumps(out) + "\n").encode())
            except OSError:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    run(int(sys.argv[1]), sys.argv[2], sys.argv[3])
