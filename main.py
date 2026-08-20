"""GDSENG smart monitoring system entry point."""

from __future__ import annotations

import datetime as dt
import locale
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

os.environ.setdefault("DISPLAY", ":0")

import tkinter as tk
from tkinter import ttk

import psutil

import settings as settings_ui
import utils
from analog_ui import AnalogUI
from modbus_ui import ModbusUI
from ups_monitor_ui import UPSMonitorUI

try:
    import pygame
except Exception:  # pragma: no cover - optional audio dependency
    pygame = None

try:
    import RPi.GPIO as GPIO
except Exception:  # pragma: no cover - non-Raspberry-Pi development system
    GPIO = None

RED_PIN = 20
YELLOW_PIN = 21
KOREAN_WEEKDAYS = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")

root: tk.Tk | None = None
main_frame: tk.Frame | None = None
default_background = "#d9d9d9"
status_label: tk.Label | None = None
clock_label: tk.Label | None = None
date_label: tk.Label | None = None
clock_after_id: str | None = None
status_after_id: str | None = None
alarm_after_id: str | None = None

box_alarm_states: dict[str, dict[str, bool]] = {}
global_alarm_mode = "none"
alarm_phase = False
audio_playing = False
audio_available = False
closing = False
branch_window: tk.Toplevel | None = None

system_info_queue: queue.Queue[str] = queue.Queue(maxsize=1)
system_stop_event = threading.Event()

modbus_ui: ModbusUI | None = None
analog_ui: AnalogUI | None = None
ups_ui: UPSMonitorUI | None = None


def setup_locale() -> None:
    for name in ("ko_KR.UTF-8", "ko_KR.utf8", ""):
        try:
            locale.setlocale(locale.LC_TIME, name)
            return
        except locale.Error:
            continue


def setup_gpio() -> None:
    if GPIO is None:
        print("RPi.GPIO를 사용할 수 없어 외부 경보 출력은 비활성화됩니다.")
        return
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(RED_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(YELLOW_PIN, GPIO.OUT, initial=GPIO.LOW)
    except Exception as exc:
        print(f"GPIO 초기화 실패: {exc}")


def gpio_write(pin: int, active: bool) -> None:
    if GPIO is None:
        return
    try:
        GPIO.output(pin, GPIO.HIGH if active else GPIO.LOW)
    except Exception as exc:
        print(f"GPIO 출력 오류: {exc}")


def setup_audio() -> None:
    global audio_available
    if pygame is None:
        print("pygame을 사용할 수 없어 경보음은 비활성화됩니다.")
        return
    try:
        pygame.mixer.init()
        audio_available = True
    except Exception as exc:
        audio_available = False
        print(f"오디오 장치를 초기화할 수 없습니다: {exc}")


def resolve_audio_path(value: object) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = utils.BASE_DIR / path
    return path


def play_alarm_sound() -> None:
    global audio_playing
    if not audio_available or pygame is None or audio_playing:
        return
    selected = settings_ui.load_settings().get("audio_file")
    path = resolve_audio_path(selected)
    if path is None or not path.is_file():
        return
    try:
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play(loops=-1)
        audio_playing = True
    except Exception as exc:
        print(f"경보음 재생 오류: {exc}")


def stop_alarm_sound() -> None:
    global audio_playing
    if pygame is not None and audio_available:
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
    audio_playing = False


def _set_alarm_surfaces(color: str) -> None:
    for widget in (root, main_frame):
        if widget is not None:
            try:
                widget.configure(background=color)
            except tk.TclError:
                pass


def set_alarm_status(active: bool, box_id: str, fut: bool = False) -> None:
    """Update a box alarm state. Box IDs are already fully qualified."""
    box_alarm_states[str(box_id)] = {"active": bool(active), "fut": bool(fut)}
    refresh_global_alarm()


def refresh_global_alarm() -> None:
    global global_alarm_mode, alarm_phase, alarm_after_id
    if root is None or closing:
        return
    has_fut = any(state.get("fut", False) for state in box_alarm_states.values())
    has_alarm = any(state.get("active", False) for state in box_alarm_states.values())
    new_mode = "fut" if has_fut else "alarm" if has_alarm else "none"
    if new_mode == global_alarm_mode:
        return

    if alarm_after_id:
        try:
            root.after_cancel(alarm_after_id)
        except tk.TclError:
            pass
        alarm_after_id = None
    global_alarm_mode = new_mode
    alarm_phase = False
    gpio_write(RED_PIN, False)
    gpio_write(YELLOW_PIN, False)
    _set_alarm_surfaces(default_background)

    if new_mode == "alarm":
        play_alarm_sound()
        alarm_tick()
    elif new_mode == "fut":
        stop_alarm_sound()
        alarm_tick()
    else:
        stop_alarm_sound()


def alarm_tick() -> None:
    global alarm_phase, alarm_after_id
    alarm_after_id = None
    if root is None or closing or global_alarm_mode == "none":
        return
    alarm_phase = not alarm_phase
    if global_alarm_mode == "alarm":
        color = "#ff0000" if alarm_phase else default_background
        gpio_write(RED_PIN, alarm_phase)
        gpio_write(YELLOW_PIN, False)
    else:
        color = "#ffd400" if alarm_phase else default_background
        gpio_write(RED_PIN, False)
        gpio_write(YELLOW_PIN, alarm_phase)
    _set_alarm_surfaces(color)
    try:
        alarm_after_id = root.after(1000, alarm_tick)
    except tk.TclError:
        alarm_after_id = None


def get_ip_address() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        sock.connect(("10.254.254.254", 1))
        return sock.getsockname()[0]
    except OSError:
        return "N/A"
    finally:
        sock.close()


def read_cpu_temperature() -> str:
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and "=" in result.stdout:
            return result.stdout.split("=", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        return f"{float(raw) / 1000.0:.1f}°C"
    except (OSError, ValueError):
        return "N/A"


def collect_system_info() -> str:
    branch = utils.current_branch() or "N/A"
    cpu_usage = psutil.cpu_percent(interval=0.2)
    memory_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage("/").percent
    net = psutil.net_io_counters()
    return (
        f"IP: {get_ip_address()} | Branch: {branch} | Temp: {read_cpu_temperature()} | "
        f"CPU: {cpu_usage:.0f}% | Mem: {memory_usage:.0f}% | Disk: {disk_usage:.0f}% | "
        f"Net: ↑{net.bytes_sent / 1048576:.1f}MB ↓{net.bytes_recv / 1048576:.1f}MB"
    )


def system_info_worker() -> None:
    while not system_stop_event.is_set():
        try:
            text = collect_system_info()
            while True:
                try:
                    system_info_queue.put_nowait(text)
                    break
                except queue.Full:
                    try:
                        system_info_queue.get_nowait()
                    except queue.Empty:
                        break
        except Exception as exc:
            print(f"시스템 정보 수집 오류: {exc}")
        system_stop_event.wait(2.0)


def poll_system_info() -> None:
    global status_after_id
    status_after_id = None
    if root is None or closing:
        return
    latest = None
    try:
        while True:
            latest = system_info_queue.get_nowait()
    except queue.Empty:
        pass
    if latest is not None and status_label is not None:
        status_label.config(text=latest)
    try:
        status_after_id = root.after(500, poll_system_info)
    except tk.TclError:
        status_after_id = None


def update_clock() -> None:
    global clock_after_id
    clock_after_id = None
    if root is None or closing or clock_label is None or date_label is None:
        return
    now = dt.datetime.now()
    clock_label.config(text=now.strftime("%H:%M:%S"))
    date_label.config(text=f"{now:%Y-%m-%d} {KOREAN_WEEKDAYS[now.weekday()]}")
    delay = max(100, 1000 - int(now.microsecond / 1000))
    try:
        clock_after_id = root.after(delay, update_clock)
    except tk.TclError:
        clock_after_id = None


def change_branch() -> None:
    global branch_window
    if root is None:
        return
    if branch_window is not None:
        try:
            if branch_window.winfo_exists():
                branch_window.focus_force()
                return
        except tk.TclError:
            pass

    result = utils.run_git("fetch", "--prune", "origin", timeout=30)
    if result.returncode != 0:
        settings_ui.toast("원격 브랜치 정보를 가져오지 못했습니다.", bg="#7a1f1f")
        return
    result = utils.run_git("branch", "-r", "--format=%(refname:short)")
    branches = []
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            value = line.strip()
            if value.startswith("origin/") and value != "origin/HEAD":
                name = value.removeprefix("origin/")
                if name and " -> " not in name:
                    branches.append(name)
    branches = sorted(set(branches))
    if not branches:
        settings_ui.toast("원격 브랜치를 찾지 못했습니다.", bg="#7a1f1f")
        return

    win = tk.Toplevel(root)
    branch_window = win
    win.title("브랜치 변경")
    win.attributes("-topmost", True)
    tk.Label(win, text=f"현재 브랜치: {utils.current_branch() or 'N/A'}", font=("Arial", 12)).pack(pady=10)
    variable = tk.StringVar(value=utils.current_branch() if utils.current_branch() in branches else branches[0])
    combo = ttk.Combobox(win, textvariable=variable, values=branches, state="readonly", font=("Arial", 12))
    combo.pack(padx=15, pady=5)

    def switch() -> None:
        target = variable.get()
        checkout = utils.run_git("checkout", target, timeout=30)
        if checkout.returncode != 0:
            checkout = utils.run_git("checkout", "-b", target, f"origin/{target}", timeout=30)
        if checkout.returncode != 0:
            settings_ui.toast(
                f"브랜치 변경 오류: {checkout.stderr.strip() or checkout.stdout.strip()}",
                bg="#7a1f1f",
            )
            return
        settings_ui.toast(f"{target} 브랜치로 변경되었습니다.", bg="#1f4f1f")
        win.destroy()
        utils.restart_application()

    tk.Button(win, text="브랜치 변경", command=switch).pack(pady=10)


def open_settings() -> None:
    current = settings_ui.load_settings()
    if not current.get("admin_password"):
        settings_ui.prompt_new_password()
    else:
        settings_ui.show_password_prompt(settings_ui.show_settings)


def on_closing(_event=None) -> None:
    global closing, clock_after_id, status_after_id, alarm_after_id
    if closing:
        return
    closing = True
    system_stop_event.set()
    utils.stop_update_checker()

    if root is not None:
        for after_id in (clock_after_id, status_after_id, alarm_after_id):
            if after_id:
                try:
                    root.after_cancel(after_id)
                except tk.TclError:
                    pass
    clock_after_id = status_after_id = alarm_after_id = None

    for ui in (modbus_ui, analog_ui, ups_ui):
        if ui is not None:
            try:
                ui.stop()
            except Exception as exc:
                print(f"UI 종료 처리 오류: {exc}")
    stop_alarm_sound()
    if pygame is not None and audio_available:
        try:
            pygame.mixer.quit()
        except Exception:
            pass
    gpio_write(RED_PIN, False)
    gpio_write(YELLOW_PIN, False)
    if GPIO is not None:
        try:
            GPIO.cleanup()
        except Exception:
            pass
    utils.stop_ui_dispatcher()
    if root is not None:
        try:
            root.destroy()
        except tk.TclError:
            pass


def build_ui() -> None:
    global root, main_frame, default_background, status_label, clock_label, date_label
    global modbus_ui, analog_ui, ups_ui, status_after_id

    setup_locale()
    setup_gpio()
    setup_audio()

    root = tk.Tk()
    root.title("GDSENG - 스마트 모니터링 시스템")
    default_background = root.cget("background")
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.bind("<Escape>", lambda event: utils.exit_fullscreen(root, event))
    root.bind("<<GMSExitRequested>>", on_closing)
    root.protocol("WM_DELETE_WINDOW", on_closing)
    utils.start_ui_dispatcher(root)
    settings_ui.initialize_globals(root, change_branch)

    settings = settings_ui.load_settings()
    main_frame = tk.Frame(root, bg=default_background)
    main_frame.grid(row=0, column=0, sticky="nsew")
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    callback = lambda active, box_id, fut=False: set_alarm_status(active, box_id, fut)
    modbus_ui = ModbusUI(
        main_frame,
        settings["modbus_boxes"],
        settings.get("modbus_gas_types", {}),
        callback,
    )
    analog_ui = AnalogUI(
        main_frame,
        settings["analog_boxes"],
        settings.get("analog_gas_types", {}),
        callback,
    )
    ups_ui = UPSMonitorUI(main_frame, 1) if settings.get("battery_box_enabled") else None

    all_boxes: list[tuple[tk.Frame, str]] = []
    if ups_ui:
        all_boxes.extend((frame, f"ups_{index}") for index, frame in enumerate(ups_ui.box_frames))
    all_boxes.extend((frame, f"modbus_{index}") for index, frame in enumerate(modbus_ui.box_frames))
    all_boxes.extend((frame, f"analog_{index}") for index, frame in enumerate(analog_ui.box_frames))
    for _frame, box_id in all_boxes:
        box_alarm_states.setdefault(box_id, {"active": False, "fut": False})

    max_columns = 6
    rows = max(1, (len(all_boxes) + max_columns - 1) // max_columns)
    for row in range(rows + 2):
        main_frame.grid_rowconfigure(row, weight=1 if row in (0, rows + 1) else 0)
    for column in range(max_columns + 2):
        main_frame.grid_columnconfigure(column, weight=1 if column in (0, max_columns + 1) else 0)
    for index, (frame, _box_id) in enumerate(all_boxes):
        frame.grid(
            row=index // max_columns + 1,
            column=index % max_columns + 1,
            padx=2,
            pady=2,
        )

    def fw_file_all() -> None:
        try:
            modbus_ui.select_fw_file_all()
        except Exception as exc:
            settings_ui.toast(f"실패: {exc}", bg="#7a1f1f")

    def fw_upgrade_all() -> None:
        try:
            modbus_ui.start_firmware_upgrade_all(only_connected=True, delay_sec=0.5)
        except Exception as exc:
            settings_ui.toast(f"실패: {exc}", bg="#7a1f1f")

    settings_ui.on_fw_file_all = fw_file_all
    settings_ui.on_fw_upgrade_all = fw_upgrade_all

    gear_button = tk.Button(
        root,
        text="⚙",
        command=open_settings,
        font=("Arial", 18),
        bg="#b2b2b2",
        fg="black",
        bd=0,
        highlightthickness=0,
        padx=10,
        pady=6,
        cursor="hand2",
    )
    gear_button.place(relx=1.0, rely=1.0, anchor="se")
    status_label = tk.Label(root, text="", font=("Arial", 10))
    status_label.place(relx=0.0, rely=1.0, anchor="sw")

    if len(all_boxes) <= 6:
        clock_label = tk.Label(
            root,
            font=("Helvetica", 60, "bold"),
            fg="white",
            bg="black",
            padx=10,
            pady=10,
        )
        clock_label.place(relx=0.5, rely=0.08, anchor="n")
        date_label = tk.Label(
            root,
            font=("Helvetica", 25),
            fg="white",
            bg="black",
            padx=5,
            pady=5,
        )
        date_label.place(relx=0.5, rely=0.19, anchor="n")
        update_clock()

    if not settings.get("admin_password"):
        root.after(100, settings_ui.prompt_new_password)

    threading.Thread(target=system_info_worker, name="gms-system-info", daemon=True).start()
    status_after_id = root.after(100, poll_system_info)
    utils.start_update_checker(root, interval=30.0)


def main() -> None:
    build_ui()
    assert root is not None
    signal.signal(signal.SIGINT, lambda _sig, _frame: root.after(0, on_closing))
    signal.signal(signal.SIGTERM, lambda _sig, _frame: root.after(0, on_closing))
    root.mainloop()


if __name__ == "__main__":
    main()
