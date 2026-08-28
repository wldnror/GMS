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
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import psutil

import settings as settings_ui
import utils
from analog_ui import AnalogUI
from gms_core import aggregate_alarm_mode
from modbus_ui import ModbusUI
from ui_config import UI_SCALE
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
shutdown_after_id: str | None = None
audio_retry_after_id: str | None = None
_AUDIO_SETTING_UNSET = object()

box_alarm_states: dict[str, dict[str, bool]] = {}
global_alarm_mode = "none"
alarm_phase = False
audio_playing = False
audio_available = False
gpio_available = False
closing = False
restart_requested = False
branch_window: tk.Toplevel | None = None
branch_loading = False

system_info_queue: queue.Queue[str] = queue.Queue(maxsize=1)
system_stop_event = threading.Event()
system_info_thread: threading.Thread | None = None
_branch_cache: tuple[float, str] = (0.0, "N/A")
system_faults: set[str] = set()
system_faults_lock = threading.Lock()

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


def report_system_fault(component: str, fault: bool) -> None:
    """Expose degraded safety outputs as persistent FUT state and status text."""

    component = str(component).strip().lower()
    if not component:
        return
    with system_faults_lock:
        if fault:
            system_faults.add(component)
        else:
            system_faults.discard(component)
    box_alarm_states[f"system_{component}"] = {"active": False, "fut": bool(fault)}
    if root is not None and not closing:
        try:
            root.after_idle(refresh_global_alarm)
        except tk.TclError:
            pass


def setup_gpio() -> None:
    global gpio_available
    gpio_available = False
    if GPIO is None:
        print("RPi.GPIO를 사용할 수 없어 외부 경보 출력은 비활성화됩니다.")
        report_system_fault("gpio", True)
        return
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(RED_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(YELLOW_PIN, GPIO.OUT, initial=GPIO.LOW)
        gpio_available = True
        report_system_fault("gpio", False)
    except Exception as exc:
        print(f"GPIO 초기화 실패: {exc}")
        report_system_fault("gpio", True)


def gpio_write(pin: int, active: bool) -> None:
    global gpio_available
    if GPIO is None or not gpio_available:
        return
    try:
        GPIO.output(pin, GPIO.HIGH if active else GPIO.LOW)
    except Exception as exc:
        print(f"GPIO 출력 오류: {exc}")
        gpio_available = False
        report_system_fault("gpio", True)


def _cancel_audio_retry() -> None:
    global audio_retry_after_id
    after_id = audio_retry_after_id
    audio_retry_after_id = None
    if root is not None and after_id is not None:
        try:
            root.after_cancel(after_id)
        except tk.TclError:
            pass


def _schedule_audio_retry() -> None:
    global audio_retry_after_id
    if root is None or closing or audio_retry_after_id is not None:
        return
    try:
        audio_retry_after_id = root.after(10000, setup_audio)
    except tk.TclError:
        audio_retry_after_id = None


def validate_audio_configuration(
    audio_setting: object = _AUDIO_SETTING_UNSET, *, schedule_retry: bool = True
) -> bool:
    """Load (but do not play) the configured alarm so failures surface at idle."""

    if pygame is None or not audio_available:
        report_system_fault("audio", True)
        if schedule_retry:
            _schedule_audio_retry()
        return False
    try:
        selected = (
            settings_ui.load_settings().get("audio_file")
            if audio_setting is _AUDIO_SETTING_UNSET
            else audio_setting
        )
        path = resolve_audio_path(selected)
        if path is None or not path.is_file():
            raise FileNotFoundError(f"경보음 파일을 찾을 수 없습니다: {path}")
        # music.load validates the same decoder path used during a real alarm.
        # This is called only while audio_playing is false.
        pygame.mixer.music.load(str(path))
    except Exception as exc:
        print(f"경보음 설정 검증 오류: {exc}")
        report_system_fault("audio", True)
        if schedule_retry:
            _schedule_audio_retry()
        return False
    _cancel_audio_retry()
    report_system_fault("audio", False)
    return True


def setup_audio(audio_setting: object = _AUDIO_SETTING_UNSET) -> None:
    global audio_available, audio_retry_after_id
    _cancel_audio_retry()
    audio_retry_after_id = None
    if pygame is None:
        audio_available = False
        print("pygame을 사용할 수 없어 경보음은 비활성화됩니다.")
        report_system_fault("audio", True)
        return
    try:
        pygame.mixer.init()
        audio_available = True
    except Exception as exc:
        audio_available = False
        print(f"오디오 장치를 초기화할 수 없습니다: {exc}")
        report_system_fault("audio", True)
        _schedule_audio_retry()
        return
    if validate_audio_configuration(audio_setting):
        if global_alarm_mode in ("alarm", "alarm_fut") and not audio_playing:
            play_alarm_sound()


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
    try:
        selected = settings_ui.load_settings().get("audio_file")
        path = resolve_audio_path(selected)
        if path is None or not path.is_file():
            report_system_fault("audio", True)
            _schedule_audio_retry()
            return
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play(loops=-1)
        audio_playing = True
        _cancel_audio_retry()
        report_system_fault("audio", False)
    except Exception as exc:
        print(f"경보음 재생 오류: {exc}")
        audio_playing = False
        report_system_fault("audio", True)
        _schedule_audio_retry()


def stop_alarm_sound() -> None:
    global audio_playing
    if pygame is not None and audio_available:
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
    audio_playing = False


def handle_audio_setting_changed(audio_setting: object) -> bool:
    """Immediately validate a newly selected file and resume an active alarm."""

    was_alarm = global_alarm_mode in ("alarm", "alarm_fut")
    stop_alarm_sound()
    valid = validate_audio_configuration(audio_setting)
    if valid and was_alarm:
        play_alarm_sound()
    return valid


def check_alarm_audio_health() -> None:
    """Detect a loop that stopped after startup instead of trusting a stale flag."""

    global audio_playing
    if not audio_playing or pygame is None or not audio_available:
        return
    get_busy = getattr(pygame.mixer.music, "get_busy", None)
    if not callable(get_busy):
        return
    try:
        healthy = bool(get_busy())
    except Exception as exc:
        print(f"경보음 상태 확인 오류: {exc}")
        healthy = False
    if healthy:
        return
    audio_playing = False
    report_system_fault("audio", True)
    _schedule_audio_retry()


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
    new_mode = aggregate_alarm_mode(box_alarm_states.values())
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

    if new_mode in ("alarm", "alarm_fut"):
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
    has_alarm = global_alarm_mode in ("alarm", "alarm_fut")
    has_fut = global_alarm_mode in ("fut", "alarm_fut")
    if has_alarm:
        check_alarm_audio_health()
        color = "#ff0000" if alarm_phase else default_background
        gpio_write(RED_PIN, alarm_phase)
        gpio_write(YELLOW_PIN, has_fut and alarm_phase)
    else:
        color = "#ffd400" if alarm_phase else default_background
        gpio_write(RED_PIN, False)
        gpio_write(YELLOW_PIN, has_fut and alarm_phase)
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
        for addresses in psutil.net_if_addrs().values():
            for address in addresses:
                if address.family == socket.AF_INET and not address.address.startswith(
                    "127."
                ):
                    return address.address
        return "N/A"
    finally:
        sock.close()


def read_cpu_temperature() -> str:
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        return f"{float(raw) / 1000.0:.1f}°C"
    except (OSError, ValueError):
        pass
    for executable in (Path("/usr/bin/vcgencmd"), Path("/opt/vc/bin/vcgencmd")):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            continue
        try:
            result = subprocess.run(
                [str(executable), "measure_temp"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.returncode == 0 and "=" in result.stdout:
                return result.stdout.split("=", 1)[1].strip()
        except (OSError, subprocess.SubprocessError):
            continue
    return "N/A"


def cached_branch() -> str:
    global _branch_cache
    now = time.monotonic()
    cached_at, value = _branch_cache
    if now - cached_at >= 60.0:
        value = utils.current_branch() or "N/A"
        _branch_cache = (now, value)
    return value


def collect_system_info() -> str:
    branch = cached_branch()
    cpu_usage = psutil.cpu_percent(interval=0.2)
    memory_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage("/").percent
    net = psutil.net_io_counters()
    with system_faults_lock:
        degraded = ",".join(sorted(system_faults)) or "없음"
    log_drops = int(getattr(analog_ui, "log_dropped_count", 0) or 0)
    return (
        f"IP: {get_ip_address()} | Branch: {branch} | Temp: {read_cpu_temperature()} | "
        f"CPU: {cpu_usage:.0f}% | Mem: {memory_usage:.0f}% | Disk: {disk_usage:.0f}% | "
        f"Net: ↑{net.bytes_sent / 1048576:.1f}MB ↓{net.bytes_recv / 1048576:.1f}MB | "
        f"FUT: {degraded} | LogDrop: {log_drops}"
    )


def system_info_worker() -> None:
    try:
        interval = max(2.0, float(os.environ.get("GMS_STATUS_INTERVAL_SEC", "5")))
    except ValueError:
        interval = 5.0
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
        system_stop_event.wait(interval)


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
    global branch_loading, branch_window
    if root is None:
        return
    if branch_window is not None:
        try:
            if branch_window.winfo_exists():
                branch_window.focus_force()
                return
        except tk.TclError:
            pass
    if branch_loading:
        settings_ui.toast("브랜치 정보를 확인 중입니다.", bg="#1f4f7a")
        return
    branch_loading = True
    settings_ui.toast("원격 브랜치 정보를 확인합니다.", bg="#1f4f7a")

    def finish_load(branches: list[str], error: str | None = None) -> None:
        global branch_loading, branch_window
        branch_loading = False
        if root is None or closing:
            return
        if error:
            settings_ui.toast(error, bg="#7a1f1f")
            return
        if not branches:
            settings_ui.toast("원격 브랜치를 찾지 못했습니다.", bg="#7a1f1f")
            return

        win = tk.Toplevel(root)
        branch_window = win
        win.title("브랜치 변경")
        win.attributes("-topmost", True)
        current = utils.current_branch() or "N/A"
        tk.Label(win, text=f"현재 브랜치: {current}", font=("Arial", 12)).pack(pady=10)
        variable = tk.StringVar(value=current if current in branches else branches[0])
        combo = ttk.Combobox(
            win,
            textvariable=variable,
            values=branches,
            state="readonly",
            font=("Arial", 12),
        )
        combo.pack(padx=15, pady=5)
        status = tk.Label(win, text="", font=("Arial", 10))
        status.pack(pady=3)

        def switch() -> None:
            target = variable.get()
            if target not in branches:
                return
            button.config(state="disabled")
            combo.config(state="disabled")
            status.config(text="브랜치를 변경 중입니다…")

            def switch_worker() -> None:
                error_text = ""
                if not utils.begin_git_mutation():
                    error_text = "다른 Git 작업이 진행 중입니다."
                else:
                    try:
                        branch_check = utils.run_git(
                            "check-ref-format", "--branch", target
                        )
                        if branch_check.returncode != 0:
                            error_text = "안전하지 않거나 잘못된 브랜치 이름입니다."
                        elif utils.has_tracked_changes():
                            error_text = "추적 중인 로컬 변경이 있어 브랜치를 변경할 수 없습니다."
                        else:
                            dependency_diff = utils.run_git(
                                "diff",
                                "--quiet",
                                "HEAD",
                                f"origin/{target}",
                                "--",
                                "requirements.txt",
                            )
                            if dependency_diff.returncode == 1:
                                error_text = (
                                    "대상 브랜치의 Python 의존성이 다릅니다. 새 가상환경에서 "
                                    "검증한 뒤 수동 배포하세요."
                                )
                            elif dependency_diff.returncode != 0:
                                error_text = (
                                    "대상 브랜치의 의존성을 확인하지 못했습니다."
                                )
                        if not error_text:
                            checkout = utils.run_git("checkout", target, timeout=30)
                            if checkout.returncode != 0:
                                checkout = utils.run_git(
                                    "checkout",
                                    "-b",
                                    target,
                                    f"origin/{target}",
                                    timeout=30,
                                )
                            if checkout.returncode != 0:
                                error_text = (
                                    checkout.stderr.strip()
                                    or checkout.stdout.strip()
                                    or "git checkout 실패"
                                )
                    finally:
                        utils.end_git_mutation()

                def switched() -> None:
                    if error_text:
                        button.config(state="normal")
                        combo.config(state="readonly")
                        status.config(text="")
                        settings_ui.toast(
                            f"브랜치 변경 오류: {error_text}", bg="#7a1f1f"
                        )
                        return
                    settings_ui.toast(
                        f"{target} 브랜치로 변경되었습니다.", bg="#1f4f1f"
                    )
                    win.destroy()
                    utils.restart_application()

                utils.run_on_ui(root, switched)

            utils.start_git_worker(
                switch_worker,
                name="gms-branch-switch",
            )

        button = tk.Button(win, text="브랜치 변경", command=switch)
        button.pack(pady=10)

    def load_worker() -> None:
        if not utils.begin_git_mutation():
            utils.run_on_ui(root, finish_load, [], "다른 Git 작업이 진행 중입니다.")
            return
        try:
            result = utils.run_git("fetch", "--prune", "origin", timeout=30)
            if result.returncode != 0:
                utils.run_on_ui(
                    root,
                    finish_load,
                    [],
                    result.stderr.strip() or "원격 브랜치 정보를 가져오지 못했습니다.",
                )
                return
            result = utils.run_git("branch", "-r", "--format=%(refname:short)")
            if result.returncode != 0:
                utils.run_on_ui(root, finish_load, [], "원격 브랜치를 찾지 못했습니다.")
                return
            branches = []
            for line in result.stdout.splitlines():
                value = line.strip()
                if value.startswith("origin/") and value != "origin/HEAD":
                    name = value.removeprefix("origin/")
                    if name and " -> " not in name:
                        branches.append(name)
            utils.run_on_ui(root, finish_load, sorted(set(branches)))
        finally:
            utils.end_git_mutation()

    utils.start_git_worker(
        load_worker,
        name="gms-branch-loader",
    )


def open_settings() -> None:
    current = settings_ui.load_settings()
    if not current.get("admin_password"):
        settings_ui.prompt_new_password()
    else:
        settings_ui.show_password_prompt(settings_ui.show_settings)


def request_fullscreen_exit(_event=None) -> str:
    if root is not None:
        settings_ui.show_password_prompt(lambda: utils.exit_fullscreen(root))
    return "break"


def request_user_exit() -> None:
    settings_ui.show_password_prompt(on_closing)


def request_restart(_event=None) -> None:
    global restart_requested
    restart_requested = True
    on_closing()


def on_closing(_event=None) -> None:
    global alarm_after_id, audio_retry_after_id, clock_after_id, closing
    global gpio_available, shutdown_after_id
    global status_after_id
    if closing:
        return
    utils.prepare_for_shutdown()
    if utils.git_operations_in_progress():
        if root is not None:
            shutdown_after_id = root.after(250, on_closing)
        return
    closing = True
    system_stop_event.set()
    utils.stop_update_checker()

    if root is not None:
        for after_id in (
            clock_after_id,
            status_after_id,
            alarm_after_id,
            audio_retry_after_id,
            shutdown_after_id,
        ):
            if after_id:
                try:
                    root.after_cancel(after_id)
                except tk.TclError:
                    pass
    clock_after_id = status_after_id = alarm_after_id = None
    audio_retry_after_id = shutdown_after_id = None

    for ui in (modbus_ui, analog_ui, ups_ui):
        if ui is not None:
            try:
                ui.stop()
            except Exception as exc:
                print(f"UI 종료 처리 오류: {exc}")
    thread = system_info_thread
    if (
        thread is not None
        and thread.is_alive()
        and thread is not threading.current_thread()
    ):
        thread.join(timeout=1.0)
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
    gpio_available = False
    utils.stop_ui_dispatcher()
    if root is not None:
        try:
            root.destroy()
        except tk.TclError:
            pass
    utils.release_instance_lock()
    if restart_requested:
        python = sys.executable
        os.execl(python, python, *sys.argv)


def build_ui() -> None:
    global root, main_frame, default_background, status_label, clock_label, date_label
    global modbus_ui, analog_ui, system_info_thread, ups_ui, status_after_id

    setup_locale()
    root = tk.Tk()
    root.title("GDSENG - 스마트 모니터링 시스템")
    default_background = root.cget("background")
    setup_gpio()
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.bind("<Escape>", request_fullscreen_exit)
    root.bind("<<GMSExitRequested>>", on_closing)
    root.bind("<<GMSRestartRequested>>", request_restart)
    root.protocol("WM_DELETE_WINDOW", request_user_exit)
    utils.start_ui_dispatcher(root)
    settings_ui.initialize_globals(root, change_branch, handle_audio_setting_changed)

    try:
        settings = settings_ui.load_settings()
    except RuntimeError as exc:
        messagebox.showerror("GMS 설정 복구 필요", str(exc), parent=root)
        raise
    setup_audio(settings.get("audio_file"))
    main_frame = tk.Frame(root, bg=default_background)
    main_frame.grid(row=0, column=0, sticky="nsew")
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    def alarm_callback(active: bool, box_id: str, fut: bool = False) -> None:
        set_alarm_status(active, box_id, fut)

    callback = alarm_callback
    modbus_ui = ModbusUI(
        main_frame,
        settings["modbus_boxes"],
        settings.get("modbus_gas_types", {}),
        callback,
        authorize_callback=settings_ui.show_password_prompt,
    )
    analog_ui = AnalogUI(
        main_frame,
        settings["analog_boxes"],
        settings.get("analog_gas_types", {}),
        callback,
    )
    ups_ui = (
        UPSMonitorUI(main_frame, 1, alarm_callback=callback)
        if settings.get("battery_box_enabled")
        else None
    )

    all_boxes: list[tuple[tk.Frame, str]] = []
    if ups_ui:
        all_boxes.extend(
            (frame, f"ups_{index}") for index, frame in enumerate(ups_ui.box_frames)
        )
    all_boxes.extend(
        (frame, f"modbus_{index}") for index, frame in enumerate(modbus_ui.box_frames)
    )
    all_boxes.extend(
        (frame, f"analog_{index}") for index, frame in enumerate(analog_ui.box_frames)
    )
    for _frame, box_id in all_boxes:
        box_alarm_states.setdefault(box_id, {"active": False, "fut": False})

    panel_width = max(1, int(165 * UI_SCALE))
    max_columns = max(1, min(6, root.winfo_screenwidth() // panel_width))
    rows = max(1, (len(all_boxes) + max_columns - 1) // max_columns)
    for row in range(rows + 2):
        main_frame.grid_rowconfigure(row, weight=1 if row in (0, rows + 1) else 0)
    for column in range(max_columns + 2):
        main_frame.grid_columnconfigure(
            column, weight=1 if column in (0, max_columns + 1) else 0
        )
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

    system_info_thread = threading.Thread(
        target=system_info_worker,
        name="gms-system-info",
        daemon=True,
    )
    system_info_thread.start()
    status_after_id = root.after(100, poll_system_info)
    try:
        update_interval = max(
            60.0, float(os.environ.get("GMS_UPDATE_INTERVAL_SEC", "900"))
        )
    except ValueError:
        update_interval = 900.0
    utils.start_update_checker(
        root,
        interval=update_interval,
        authorize_callback=settings_ui.show_password_prompt,
    )


def main() -> None:
    try:
        utils.acquire_instance_lock()
        build_ui()
        if root is None:
            raise RuntimeError("Tk root 초기화에 실패했습니다.")
        signal.signal(signal.SIGINT, lambda _sig, _frame: root.after(0, on_closing))
        signal.signal(signal.SIGTERM, lambda _sig, _frame: root.after(0, on_closing))
        root.mainloop()
    finally:
        if not closing:
            on_closing()


if __name__ == "__main__":
    main()
