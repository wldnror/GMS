"""Encrypted settings storage and settings dialogs for GMS."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from cryptography.fernet import InvalidToken

import utils
from gms_core import hash_password, password_is_hashed, verify_password

SETTINGS_FILE = utils.STATE_DIR / "settings.json"
VALID_GAS_TYPES = ("ORG", "ARF-T", "HMDS", "HC-100")
DEFAULT_SETTINGS = {
    "modbus_boxes": 0,
    "analog_boxes": 0,
    "admin_password": None,
    "modbus_gas_types": {},
    "analog_gas_types": {},
    "audio_file": "audio/gms_k1.mp3",
    "battery_box_enabled": 0,
}

settings_window = None
password_window = None
attempt_count = 0
lock_time = 0.0
lock_window = None
box_settings_window = None
new_password_window = None
audio_selection_window = None
root = None
change_branch = None
audio_changed_callback = None
selected_audio_file = None

on_fw_file_all = None
on_fw_upgrade_all = None


def initialize_globals(main_root, change_branch_func, audio_changed_func=None) -> None:
    global root, change_branch, audio_changed_callback
    root = main_root
    change_branch = change_branch_func
    audio_changed_callback = audio_changed_func


def encrypt_data(data: str) -> bytes:
    return utils.encrypt_data(data)


def decrypt_data(data: bytes) -> str:
    return utils.decrypt_data(data)


def _coerce_count(value) -> int:
    try:
        return max(0, min(12, int(value)))
    except (TypeError, ValueError):
        return 0


def normalize_settings(value: object) -> dict:
    source = value if isinstance(value, dict) else {}
    result = dict(DEFAULT_SETTINGS)
    result["modbus_boxes"] = _coerce_count(source.get("modbus_boxes", 0))
    result["analog_boxes"] = _coerce_count(source.get("analog_boxes", 0))
    result["battery_box_enabled"] = 1 if source.get("battery_box_enabled") else 0
    while (
        result["modbus_boxes"] + result["analog_boxes"] + result["battery_box_enabled"]
        > 12
    ):
        if result["battery_box_enabled"]:
            result["battery_box_enabled"] = 0
        elif result["analog_boxes"]:
            result["analog_boxes"] -= 1
        else:
            result["modbus_boxes"] -= 1

    password = source.get("admin_password")
    result["admin_password"] = str(password) if password not in (None, "") else None
    audio = source.get("audio_file", DEFAULT_SETTINGS["audio_file"])
    result["audio_file"] = str(audio) if audio else None

    for group_name, count_key in (
        ("modbus_gas_types", "modbus_boxes"),
        ("analog_gas_types", "analog_boxes"),
    ):
        raw_group = source.get(group_name, {})
        raw_group = raw_group if isinstance(raw_group, dict) else {}
        prefix = "modbus_box" if group_name.startswith("modbus") else "analog_box"
        result[group_name] = {
            f"{prefix}_{index}": (
                raw_group.get(f"{prefix}_{index}")
                if raw_group.get(f"{prefix}_{index}") in VALID_GAS_TYPES
                else "ORG"
            )
            for index in range(result[count_key])
        }
    return result


def _backup_broken_settings() -> Path | None:
    if not SETTINGS_FILE.exists():
        return None
    try:
        encrypted = SETTINGS_FILE.read_bytes()
    except OSError as exc:
        print(f"손상된 설정 파일을 읽을 수 없습니다: {exc}")
        return None
    fingerprint = hashlib.sha256(encrypted).hexdigest()[:16]
    backup = SETTINGS_FILE.with_name(f"settings.broken-{fingerprint}.json")
    if backup.exists():
        return backup
    fd = -1
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
        utils.fsync_directory(backup.parent)
        print(f"손상된 설정 파일 사본을 {backup.name}(으)로 보존했습니다.")
        return backup
    except FileExistsError:
        return backup
    except OSError as exc:
        print(f"손상된 설정 파일 백업 실패: {exc}")
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return normalize_settings({})
    try:
        encrypted = SETTINGS_FILE.read_bytes()
        decoded = json.loads(decrypt_data(encrypted))
        if not isinstance(decoded, dict):
            raise TypeError("설정의 최상위 값은 JSON 객체여야 합니다.")
        return normalize_settings(decoded)
    except InvalidToken as exc:
        raise RuntimeError(
            "settings.json을 현재 secret.key로 복호화할 수 없습니다. 기본값으로 "
            "초기화하지 않았습니다. 두 파일의 같은 시점 백업을 복원하세요."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            "settings.json을 읽을 수 없습니다. 파일과 상태 디렉터리 권한을 확인하세요."
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        backup = _backup_broken_settings()
        backup_text = f" 백업 사본: {backup}" if backup is not None else ""
        raise RuntimeError(
            "복호화된 settings.json 내용이 손상되었습니다. 감시 설정을 기본값으로 "
            f"초기화하지 않았습니다. 올바른 백업을 복원하세요.{backup_text}"
        ) from exc


def save_settings(value: dict) -> None:
    normalized = normalize_settings(value)
    data = encrypt_data(json.dumps(normalized, ensure_ascii=False, sort_keys=True))
    utils.ensure_state_dir()
    fd, temp_name = tempfile.mkstemp(prefix=".settings.", dir=str(SETTINGS_FILE.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, SETTINGS_FILE)
        utils.fsync_directory(SETTINGS_FILE.parent)
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except OSError:
            pass


# Keep imports side-effect free: encrypted state is loaded only after Tk and
# the application lifecycle are ready to report a recovery error to the user.
settings = normalize_settings({})
admin_password = None
selected_audio_file = settings.get("audio_file")


def toast(
    msg: str, duration: int = 1800, bg: str = "#222222", fg: str = "white"
) -> None:
    if root is None:
        return
    try:
        if not root.winfo_exists():
            return
    except tk.TclError:
        return
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    frame = tk.Frame(win, bg=bg, bd=1, relief="solid")
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text=msg, bg=bg, fg=fg, font=("Arial", 12)).pack(padx=14, pady=10)
    root.update_idletasks()
    width = win.winfo_reqwidth()
    height = win.winfo_reqheight()
    x = root.winfo_rootx() + root.winfo_width() - width - 20
    y = root.winfo_rooty() + root.winfo_height() - height - 20
    win.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
    win.after(duration, lambda: win.destroy() if win.winfo_exists() else None)


def prompt_new_password() -> None:
    global new_password_window
    if root is None:
        return
    if new_password_window is not None and new_password_window.winfo_exists():
        new_password_window.focus_force()
        return
    win = tk.Toplevel(root)
    new_password_window = win
    win.title("관리자 비밀번호 설정")
    win.attributes("-topmost", True)
    tk.Label(win, text="새로운 관리자 비밀번호를 입력하세요", font=("Arial", 12)).pack(
        pady=10
    )
    entry = tk.Entry(win, show="*", font=("Arial", 12))
    entry.pack(pady=5)
    utils.create_keypad(entry, win, geometry="pack")

    def next_step() -> None:
        password = entry.get()
        if len(password) < 4:
            toast("비밀번호는 4자리 이상 입력하세요.", bg="#7a1f1f")
            return
        if len(password) > 64:
            toast("비밀번호는 64자를 초과할 수 없습니다.", bg="#7a1f1f")
            return
        win.destroy()
        prompt_confirm_password(password)

    tk.Button(win, text="다음", command=next_step).pack(pady=5)
    entry.focus_force()


def prompt_confirm_password(new_password: str) -> None:
    global new_password_window
    if root is None:
        return
    win = tk.Toplevel(root)
    new_password_window = win
    win.title("비밀번호 확인")
    win.attributes("-topmost", True)
    tk.Label(win, text="비밀번호를 다시 입력하세요", font=("Arial", 12)).pack(pady=10)
    entry = tk.Entry(win, show="*", font=("Arial", 12))
    entry.pack(pady=5)
    utils.create_keypad(entry, win, geometry="pack")

    def save_new_password() -> None:
        global settings, admin_password, new_password_window
        if entry.get() != new_password:
            toast("비밀번호가 일치하지 않습니다.", bg="#7a1f1f")
            win.destroy()
            new_password_window = None
            prompt_new_password()
            return
        settings = load_settings()
        settings["admin_password"] = hash_password(new_password)
        save_settings(settings)
        admin_password = settings["admin_password"]
        toast("새로운 비밀번호가 설정되었습니다.", bg="#1f4f1f")
        win.destroy()
        new_password_window = None
        utils.restart_application()

    tk.Button(win, text="저장", command=save_new_password).pack(pady=5)
    entry.focus_force()


def show_password_prompt(callback) -> None:
    global attempt_count, lock_time, password_window, settings_window, lock_window
    global admin_password
    if root is None:
        return
    current_settings = load_settings()
    admin_password = current_settings.get("admin_password")
    if not admin_password:
        prompt_new_password()
        return

    if time.monotonic() < lock_time:
        if lock_window is None or not lock_window.winfo_exists():
            lock_window = tk.Toplevel(root)
            lock_window.title("잠금")
            lock_window.attributes("-topmost", True)
            lock_window.geometry("330x160")
            label = tk.Label(lock_window, font=("Arial", 12))
            label.pack(pady=10)
            tk.Button(lock_window, text="확인", command=lock_window.destroy).pack(
                pady=5
            )

            def update_message() -> None:
                remaining = max(0, int(lock_time - time.monotonic()))
                if not label.winfo_exists():
                    return
                label.config(
                    text=f"비밀번호 입력 시도가 5회 초과되었습니다.\n{remaining}초 후에 다시 시도하십시오."
                )
                if remaining > 0:
                    label.after(1000, update_message)
                else:
                    lock_window.destroy()

            update_message()
        return

    if password_window is not None and password_window.winfo_exists():
        password_window.focus_force()
        return
    if settings_window is not None and settings_window.winfo_exists():
        settings_window.destroy()

    win = tk.Toplevel(root)
    password_window = win
    win.title("비밀번호 입력")
    win.attributes("-topmost", True)
    tk.Label(win, text="비밀번호를 입력하세요", font=("Arial", 12)).pack(pady=10)
    entry = tk.Entry(win, show="*", font=("Arial", 12))
    entry.pack(pady=5)
    utils.create_keypad(entry, win, geometry="pack")
    message = tk.Label(win, font=("Arial", 12), fg="red")
    message.pack(pady=5)

    def check_password() -> None:
        global admin_password, attempt_count, lock_time, password_window
        candidate = entry.get()
        if not 1 <= len(candidate) <= 64:
            accepted = False
        elif password_is_hashed(admin_password):
            accepted = verify_password(candidate, admin_password)
        else:
            accepted = hmac.compare_digest(candidate, str(admin_password))
        if accepted:
            attempt_count = 0
            if not password_is_hashed(admin_password):
                migrated = load_settings()
                migrated["admin_password"] = hash_password(candidate)
                save_settings(migrated)
                admin_password = migrated["admin_password"]
            win.destroy()
            password_window = None
            callback()
            return
        attempt_count += 1
        entry.delete(0, "end")
        if attempt_count >= 5:
            lock_time = time.monotonic() + 60
            attempt_count = 0
            win.destroy()
            password_window = None
            show_password_prompt(callback)
        else:
            message.config(text=f"비밀번호가 틀렸습니다. ({attempt_count}/5)")

    tk.Button(win, text="확인", command=check_password).pack(pady=5)
    entry.bind("<Return>", lambda _event: check_password())
    entry.focus_force()


def _call(callback) -> None:
    if callback:
        callback()
    else:
        toast("기능이 연결되지 않았습니다.", bg="#7a1f1f")


def show_settings() -> None:
    global settings_window, selected_audio_file, settings, admin_password
    if root is None:
        return
    settings = load_settings()
    admin_password = settings.get("admin_password")
    selected_audio_file = settings.get("audio_file")
    if settings_window is not None and settings_window.winfo_exists():
        settings_window.focus_force()
        return

    win = tk.Toplevel(root)
    settings_window = win
    win.title("설정 메뉴")
    win.attributes("-topmost", True)
    outer = tk.Frame(win)
    outer.pack(fill="both", expand=True)
    canvas = tk.Canvas(outer, highlightthickness=0)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    body = tk.Frame(canvas)
    body.bind(
        "<Configure>",
        lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.create_window((0, 0), window=body, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    tk.Label(body, text="GMS-1000 설정", font=("Arial", 16)).pack(pady=10)
    style = {"font": ("Arial", 14), "width": 25, "height": 2, "padx": 10, "pady": 10}
    tk.Button(body, text="상자 설정", command=show_box_settings, **style).pack(pady=5)
    tk.Button(body, text="비밀번호 변경", command=prompt_new_password, **style).pack(
        pady=5
    )
    tk.Button(
        body, text="FW 파일 전체 적용", command=lambda: _call(on_fw_file_all), **style
    ).pack(pady=5)
    tk.Button(
        body, text="전체 FW 업데이트", command=lambda: _call(on_fw_upgrade_all), **style
    ).pack(pady=5)

    row1 = tk.Frame(body)
    row1.pack(pady=5)
    tk.Button(
        row1,
        text="전체 화면 설정",
        command=lambda: utils.enter_fullscreen(root),
        font=("Arial", 14),
        width=12,
        height=2,
    ).grid(row=0, column=0)
    tk.Button(
        row1,
        text="창 크기 설정",
        command=lambda: utils.exit_fullscreen(root),
        font=("Arial", 14),
        width=12,
        height=2,
    ).grid(row=0, column=1)

    row2 = tk.Frame(body)
    row2.pack(pady=5)

    def start_manual_update() -> None:
        utils.start_git_worker(
            check_and_update_system,
            name="gms-manual-update",
        )

    tk.Button(
        row2,
        text="시스템 업데이트",
        command=start_manual_update,
        font=("Arial", 14),
        width=12,
        height=2,
    ).grid(row=0, column=0)
    tk.Button(
        row2,
        text="브랜치 변경",
        command=change_branch,
        font=("Arial", 14),
        width=12,
        height=2,
    ).grid(row=0, column=1)

    row3 = tk.Frame(body)
    row3.pack(pady=5)
    tk.Button(
        row3,
        text="재시작",
        command=utils.restart_application,
        font=("Arial", 14),
        width=12,
        height=2,
    ).grid(row=0, column=0)
    tk.Button(
        row3,
        text="종료",
        command=lambda: utils.exit_application(root),
        font=("Arial", 14),
        width=12,
        height=2,
    ).grid(row=0, column=1)

    selected_label = tk.Label(
        body,
        text=f"선택된 오디오 파일: {Path(selected_audio_file).name if selected_audio_file else '없음'}",
        font=("Arial", 12),
    )
    selected_label.pack(pady=10)

    def select_audio_file() -> None:
        global audio_selection_window, selected_audio_file, settings
        if audio_selection_window is not None and audio_selection_window.winfo_exists():
            audio_selection_window.focus_force()
            return
        audio_folder = utils.BASE_DIR / "audio"
        try:
            audio_files = sorted(
                path.name
                for path in audio_folder.iterdir()
                if path.is_file() and path.suffix.lower() in (".mp3", ".wav", ".ogg")
            )
        except OSError:
            audio_files = []
        if not audio_files:
            toast("audio 폴더에 지원되는 오디오 파일이 없습니다.", bg="#7a1f1f")
            return
        audio_win = tk.Toplevel(win)
        audio_selection_window = audio_win
        audio_win.title("오디오 파일 선택")
        audio_win.attributes("-topmost", True)
        tk.Label(audio_win, text="오디오 파일을 선택하세요", font=("Arial", 12)).pack(
            pady=10
        )
        combo = ttk.Combobox(
            audio_win, values=audio_files, state="readonly", font=("Arial", 12)
        )
        combo.pack(pady=5)

        def selected(_event=None) -> None:
            global selected_audio_file, settings, audio_selection_window
            if not combo.get():
                return
            selected_audio_file = str(Path("audio") / combo.get())
            settings = load_settings()
            settings["audio_file"] = selected_audio_file
            save_settings(settings)
            selected_label.config(text=f"선택된 오디오 파일: {combo.get()}")
            valid = True
            if callable(audio_changed_callback):
                try:
                    valid = audio_changed_callback(selected_audio_file) is not False
                except Exception as exc:
                    print(f"오디오 설정 적용 오류: {exc}")
                    valid = False
            if valid:
                toast(f"오디오 선택: {combo.get()}", bg="#1f4f1f")
            else:
                toast(
                    f"오디오를 저장했지만 재생 검증에 실패했습니다: {combo.get()}",
                    bg="#7a1f1f",
                )
            audio_win.destroy()
            audio_selection_window = None

        combo.bind("<<ComboboxSelected>>", selected)
        if audio_files:
            combo.current(0)
        tk.Button(audio_win, text="선택", command=selected).pack(pady=8)

    tk.Button(body, text="경고 오디오 선택", command=select_audio_file, **style).pack(
        pady=5
    )
    width = max(360, min(520, root.winfo_screenwidth() - 20))
    height = max(320, min(650, root.winfo_screenheight() - 60))
    win.geometry(f"{width}x{height}")


def check_and_update_system() -> None:
    branch = utils.current_branch()
    local = utils.local_commit()
    remote = utils.remote_commit(branch) if branch else None
    if not branch or not local:
        utils.run_on_ui(
            root, toast, "Git 저장소 상태를 확인할 수 없습니다.", bg="#7a1f1f"
        )
    elif remote is None:
        utils.run_on_ui(root, toast, "원격 브랜치를 확인할 수 없습니다.", bg="#7a1f1f")
    else:
        relation = utils.commit_relation(local, remote)
        if relation == "behind":
            utils.run_on_ui(
                root,
                toast,
                "새로운 업데이트가 있습니다. 업데이트를 시작합니다.",
                bg="#1f4f7a",
            )
            utils.update_system(root, expected_commit=remote)
        elif relation == "equal":
            utils.run_on_ui(root, toast, "현재 최신 버전입니다.", bg="#1f4f1f")
        elif relation == "ahead":
            utils.run_on_ui(
                root,
                toast,
                "로컬 커밋이 원격보다 앞서 있어 자동 업데이트하지 않습니다.",
                bg="#7a4f1f",
            )
        else:
            utils.run_on_ui(
                root,
                toast,
                f"자동 업데이트할 수 없는 Git 상태입니다: {relation}",
                bg="#7a1f1f",
            )


def show_box_settings() -> None:
    global box_settings_window, settings
    if root is None:
        return
    settings = load_settings()
    if box_settings_window is not None and box_settings_window.winfo_exists():
        box_settings_window.focus_force()
        return

    win = tk.Toplevel(root)
    box_settings_window = win
    win.title("상자 설정")
    win.attributes("-topmost", True)

    outer = tk.Frame(win)
    outer.pack(fill="both", expand=True)
    canvas = tk.Canvas(outer, highlightthickness=0)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    body = tk.Frame(canvas)
    body.bind(
        "<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    canvas.create_window((0, 0), window=body, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    modbus_var = tk.StringVar(value=str(settings["modbus_boxes"]))
    analog_var = tk.StringVar(value=str(settings["analog_boxes"]))
    battery_var = tk.IntVar(value=settings["battery_box_enabled"])

    tk.Label(body, text="Modbus TCP 상자 수", font=("Arial", 12)).grid(
        row=0, column=0, padx=4, pady=4, sticky="w"
    )
    tk.Label(body, text="4~20mA 상자 수", font=("Arial", 12)).grid(
        row=1, column=0, padx=4, pady=4, sticky="w"
    )

    def counter(row: int, variable: tk.StringVar) -> None:
        frame = tk.Frame(body)
        frame.grid(row=row, column=1, padx=4, pady=4)

        def change(delta: int) -> None:
            try:
                current = int(variable.get())
                other = int(
                    analog_var.get() if variable is modbus_var else modbus_var.get()
                )
            except ValueError:
                return
            new = max(0, min(12, current + delta))
            if new + other + battery_var.get() <= 12:
                variable.set(str(new))
            else:
                toast("상자의 총합은 12개를 초과할 수 없습니다.", bg="#7a1f1f")

        tk.Button(frame, text="-", command=lambda: change(-1), font=("Arial", 12)).grid(
            row=0, column=0
        )
        tk.Label(frame, textvariable=variable, width=4, font=("Arial", 12)).grid(
            row=0, column=1
        )
        tk.Button(frame, text="+", command=lambda: change(1), font=("Arial", 12)).grid(
            row=0, column=2
        )

    counter(0, modbus_var)
    counter(1, analog_var)
    tk.Checkbutton(
        body, text="배터리 박스 활성화", variable=battery_var, font=("Arial", 12)
    ).grid(row=0, column=2, padx=4, pady=4)

    modbus_rows = tk.Frame(body)
    analog_rows = tk.Frame(body)
    modbus_rows.grid(row=2, column=0, columnspan=2, sticky="nw", padx=4)
    analog_rows.grid(row=2, column=2, columnspan=2, sticky="nw", padx=4)
    modbus_gas_vars: list[tk.StringVar] = []
    analog_gas_vars: list[tk.StringVar] = []

    def rebuild_rows(*_args) -> None:
        previous_modbus = [variable.get() for variable in modbus_gas_vars]
        previous_analog = [variable.get() for variable in analog_gas_vars]
        for child in modbus_rows.winfo_children():
            child.destroy()
        for child in analog_rows.winfo_children():
            child.destroy()
        modbus_gas_vars.clear()
        analog_gas_vars.clear()
        try:
            modbus_count = int(modbus_var.get())
            analog_count = int(analog_var.get())
        except ValueError:
            return
        for index in range(modbus_count):
            tk.Label(modbus_rows, text=f"Modbus {index + 1}", font=("Arial", 11)).grid(
                row=index, column=0, padx=2, pady=2
            )
            selected = (
                previous_modbus[index]
                if index < len(previous_modbus)
                else settings["modbus_gas_types"].get(f"modbus_box_{index}", "ORG")
            )
            variable = tk.StringVar(value=selected)
            modbus_gas_vars.append(variable)
            ttk.Combobox(
                modbus_rows,
                textvariable=variable,
                values=VALID_GAS_TYPES,
                state="readonly",
                width=10,
            ).grid(row=index, column=1, padx=2, pady=2)
        for index in range(analog_count):
            tk.Label(analog_rows, text=f"4~20mA {index + 1}", font=("Arial", 11)).grid(
                row=index, column=0, padx=2, pady=2
            )
            selected = (
                previous_analog[index]
                if index < len(previous_analog)
                else settings["analog_gas_types"].get(f"analog_box_{index}", "ORG")
            )
            variable = tk.StringVar(value=selected)
            analog_gas_vars.append(variable)
            ttk.Combobox(
                analog_rows,
                textvariable=variable,
                values=VALID_GAS_TYPES,
                state="readonly",
                width=10,
            ).grid(row=index, column=1, padx=2, pady=2)
        win.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))

    def validate_total(*_args) -> None:
        try:
            total = int(modbus_var.get()) + int(analog_var.get()) + battery_var.get()
        except ValueError:
            return
        if total > 12:
            if battery_var.get():
                battery_var.set(0)
            toast("상자의 총합은 12개를 초과할 수 없습니다.", bg="#7a1f1f")

    modbus_var.trace_add("write", rebuild_rows)
    analog_var.trace_add("write", rebuild_rows)
    battery_var.trace_add("write", validate_total)
    rebuild_rows()

    def save_and_close() -> None:
        global settings, box_settings_window
        try:
            modbus_count = int(modbus_var.get())
            analog_count = int(analog_var.get())
        except ValueError:
            toast("올바른 숫자를 입력하세요.", bg="#7a1f1f")
            return
        if modbus_count + analog_count + battery_var.get() > 12:
            toast("상자의 총합은 12개를 초과할 수 없습니다.", bg="#7a1f1f")
            return
        settings = load_settings()
        settings["modbus_boxes"] = modbus_count
        settings["analog_boxes"] = analog_count
        settings["battery_box_enabled"] = battery_var.get()
        settings["modbus_gas_types"] = {
            f"modbus_box_{index}": variable.get()
            for index, variable in enumerate(modbus_gas_vars)
        }
        settings["analog_gas_types"] = {
            f"analog_box_{index}": variable.get()
            for index, variable in enumerate(analog_gas_vars)
        }
        save_settings(settings)
        toast("설정이 저장되었습니다. 재시작합니다.", bg="#1f4f1f")
        win.destroy()
        box_settings_window = None
        utils.restart_application()

    tk.Button(
        body,
        text="저장",
        command=save_and_close,
        font=("Arial", 12),
        width=15,
        height=2,
    ).grid(row=30, column=0, columnspan=4, pady=12)
    width = max(360, min(760, root.winfo_screenwidth() - 20))
    height = max(320, min(650, root.winfo_screenheight() - 60))
    win.geometry(f"{width}x{height}")
