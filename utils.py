from __future__ import annotations

import os
import random
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from tkinter import Button, Frame, Label, messagebox
from typing import Any, Callable

from cryptography.fernet import Fernet

BASE_DIR = Path(__file__).resolve().parent
KEY_FILE = str(BASE_DIR / "secret.key")
IGNORE_COMMIT_FILE = str(BASE_DIR / "ignore_commit.txt")
IGNORE_BRANCH_FILE = str(BASE_DIR / "ignore_branch.txt")
SYNCED_BRANCHES_FILE = str(BASE_DIR / "synced_branches.txt")

checking_updates = False
ignore_commit: str | None = None
ignore_branch: str | None = None
synced_branches: set[str] = set()
update_notification_frame: Frame | None = None
_update_lock = threading.Lock()
_ui_root: Any = None
_ui_queue: queue.Queue[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = queue.Queue()
_ui_after_id: str | None = None
_update_thread: threading.Thread | None = None


def install_ui_dispatcher(root: Any) -> None:
    """Install a main-thread queue used by background workers to update Tk safely."""
    global _ui_root
    _ui_root = root
    _drain_ui_queue()


def _drain_ui_queue() -> None:
    global _ui_after_id
    _ui_after_id = None
    root = _ui_root
    if root is None:
        return
    try:
        if not root.winfo_exists():
            return
    except Exception:
        return
    processed = 0
    while processed < 100:
        try:
            callback, args, kwargs = _ui_queue.get_nowait()
        except queue.Empty:
            break
        try:
            callback(*args, **kwargs)
        except Exception as exc:
            print(f"[UI] callback failed: {exc}")
        processed += 1
    try:
        _ui_after_id = root.after(50, _drain_ui_queue)
    except Exception:
        _ui_after_id = None


def shutdown_ui_dispatcher() -> None:
    global _ui_root, _ui_after_id
    root = _ui_root
    if root is not None and _ui_after_id is not None:
        try:
            root.after_cancel(_ui_after_id)
        except Exception:
            pass
    _ui_after_id = None
    _ui_root = None
    while True:
        try:
            _ui_queue.get_nowait()
        except queue.Empty:
            break


def generate_key() -> bytes:
    key = Fernet.generate_key()
    path = Path(KEY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(key)
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, path)
    return key


def load_key() -> bytes:
    path = Path(KEY_FILE)
    if not path.exists():
        return generate_key()
    key = path.read_bytes().strip()
    try:
        Fernet(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key
    except (TypeError, ValueError):
        backup = path.with_name(f"secret.broken-{time.strftime('%Y%m%d-%H%M%S')}.key")
        try:
            path.replace(backup)
            print(f"손상된 암호화 키를 {backup.name}(으)로 이동했습니다.")
        except OSError as exc:
            print(f"손상된 암호화 키 백업 실패: {exc}")
        return generate_key()


key = load_key()
cipher_suite = Fernet(key)


def encrypt_data(data: str) -> bytes:
    return cipher_suite.encrypt(str(data).encode("utf-8"))


def decrypt_data(data: bytes) -> str:
    return cipher_suite.decrypt(data).decode("utf-8")


def run_on_ui(root: Any, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Queue a Tk callback without calling Tcl from the worker thread."""
    del root  # The installed dispatcher owns the Tk root.
    _ui_queue.put((callback, args, kwargs))


def create_keypad(entry: Any, parent: Any, row: int | None = None, column: int | None = None,
                  columnspan: int = 1, geometry: str = "grid") -> Frame:
    keypad_frame = Frame(parent)
    if geometry == "grid":
        keypad_frame.grid(row=row, column=column, columnspan=columnspan, pady=5)
    elif geometry == "pack":
        keypad_frame.pack()
    else:
        raise ValueError(f"unsupported geometry manager: {geometry}")

    def on_button_click(char: str) -> None:
        if char == "DEL":
            current_text = entry.get()
            entry.delete(0, "end")
            entry.insert(0, current_text[:-1])
        elif char == "CLR":
            entry.delete(0, "end")
        else:
            entry.insert("end", char)

    buttons = [str(i) for i in range(10)]
    random.SystemRandom().shuffle(buttons)
    buttons.extend(("CLR", "DEL"))
    for index, button in enumerate(buttons):
        Button(
            keypad_frame,
            text=button,
            width=5,
            height=2,
            command=lambda value=button: on_button_click(value),
        ).grid(row=index // 3, column=index % 3, padx=5, pady=5)
    return keypad_frame


def exit_fullscreen(root: Any, event: Any = None) -> None:
    root.attributes("-fullscreen", False)
    root.attributes("-topmost", False)


def enter_fullscreen(root: Any, event: Any = None) -> None:
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)


def exit_application(root: Any) -> None:
    try:
        root.event_generate("<<GMSExitRequested>>", when="tail")
    except Exception:
        root.destroy()


def _git(*args: str, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_text(*args: str) -> str:
    result = _git(*args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout.strip()




def current_branch() -> str | None:
    try:
        value = _git_text("branch", "--show-current")
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return None
    return value or None


def local_commit() -> str | None:
    try:
        return _git_text("rev-parse", "HEAD") or None
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return None


def remote_commit(branch: str | None) -> str | None:
    if not branch:
        return None
    try:
        fetch = _git("fetch", "--quiet", "origin", branch, timeout=20)
        if fetch.returncode != 0:
            return None
        return _git_text("rev-parse", f"origin/{branch}") or None
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return None


def load_synced_branches() -> None:
    global synced_branches
    path = Path(SYNCED_BRANCHES_FILE)
    if path.exists():
        synced_branches = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def save_synced_branch(branch: str) -> None:
    branch = branch.strip()
    if not branch or branch in synced_branches:
        return
    synced_branches.add(branch)
    path = Path(SYNCED_BRANCHES_FILE)
    with path.open("a", encoding="utf-8") as file:
        file.write(branch + "\n")


def _show_info(root: Any, title: str, message: str) -> None:
    run_on_ui(root, messagebox.showinfo, title, message)


def _show_error(root: Any, title: str, message: str) -> None:
    run_on_ui(root, messagebox.showerror, title, message)


def update_system(root: Any) -> None:
    """Fast-forward the current branch and restart only after a verified success."""
    global checking_updates
    if not _update_lock.acquire(blocking=False):
        _show_info(root, "시스템 업데이트", "이미 업데이트를 진행 중입니다.")
        return

    try:
        branch = _git_text("branch", "--show-current")
        if not branch:
            raise RuntimeError("현재 Git 브랜치를 확인할 수 없습니다.")

        fetch = _git("fetch", "--prune", "origin", branch, timeout=30)
        if fetch.returncode != 0:
            raise RuntimeError(fetch.stderr.strip() or "git fetch 실패")

        pull = _git("pull", "--ff-only", "origin", branch, timeout=30)
        if pull.returncode != 0:
            raise RuntimeError(
                "자동 업데이트는 fast-forward만 허용됩니다.\n"
                + (pull.stderr.strip() or pull.stdout.strip() or "git pull 실패")
            )

        _show_info(root, "시스템 업데이트", "업데이트가 완료되었습니다. 애플리케이션을 재시작합니다.")
        run_on_ui(root, lambda: root.after(1200, restart_application))
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        _show_error(root, "시스템 업데이트", f"업데이트 중 오류가 발생했습니다.\n{exc}")
    finally:
        _update_lock.release()


def check_for_updates(root: Any, interval: float = 30.0) -> None:
    """Poll only the current branch without changing the working tree."""
    global checking_updates
    load_synced_branches()

    while checking_updates:
        try:
            current_branch = _git_text("branch", "--show-current")
            if current_branch:
                fetch = _git("fetch", "--quiet", "--prune", "origin", current_branch, timeout=20)
                if fetch.returncode == 0:
                    local_commit = _git_text("rev-parse", "HEAD")
                    remote_commit = _git_text("rev-parse", f"origin/{current_branch}")
                    if (
                        local_commit != remote_commit
                        and remote_commit
                        and remote_commit != ignore_commit
                    ):
                        run_on_ui(root, show_update_notification, root, remote_commit)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            print(f"[Update] check failed: {exc}")

        end = time.monotonic() + max(5.0, float(interval))
        while checking_updates and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))


def sync_branches(root: Any) -> None:
    try:
        result = _git("fetch", "--all", "--prune", timeout=30)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git fetch 실패")
        run_on_ui(root, show_temporary_notification, root, "원격 브랜치 정보가 동기화되었습니다.")
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        _show_error(root, "브랜치 동기화 오류", str(exc))


def show_update_notification(root: Any, remote_commit: str) -> None:
    global update_notification_frame
    if update_notification_frame is not None and update_notification_frame.winfo_exists():
        return

    frame = Frame(root)
    update_notification_frame = frame
    frame.place(relx=0.5, rely=0.95, anchor="center")

    Label(
        frame,
        text="새로운 버전이 있습니다. 업데이트를 진행하시겠습니까?",
        font=("Arial", 15),
        fg="red",
    ).pack(side="left", padx=5)
    Button(
        frame,
        text="예",
        command=lambda: start_update(root, remote_commit),
        font=("Arial", 14),
        fg="red",
    ).pack(side="left", padx=5)
    Button(
        frame,
        text="건너뛰기",
        command=lambda: ignore_update(remote_commit),
        font=("Arial", 14),
        fg="red",
    ).pack(side="left", padx=5)


def show_temporary_notification(root: Any, message: str, duration: int = 5000) -> None:
    frame = Frame(root, bg="green")
    frame.place(relx=0.5, rely=0.95, anchor="center")
    Label(frame, text=message, font=("Arial", 14), fg="white", bg="green").pack(
        side="left", padx=5
    )
    root.after(duration, frame.destroy)


def prune_deleted_branches(root: Any) -> None:
    sync_branches(root)


def start_update(root: Any, remote_commit: str) -> None:
    global update_notification_frame, ignore_commit, checking_updates
    ignore_commit = None
    try:
        Path(IGNORE_COMMIT_FILE).unlink(missing_ok=True)
    except OSError:
        pass
    if update_notification_frame is not None and update_notification_frame.winfo_exists():
        update_notification_frame.destroy()
    update_notification_frame = None
    threading.Thread(target=update_system, args=(root,), daemon=True).start()


def ignore_update(remote_commit: str) -> None:
    global ignore_commit, update_notification_frame
    ignore_commit = str(remote_commit).strip() or None
    if ignore_commit:
        Path(IGNORE_COMMIT_FILE).write_text(ignore_commit, encoding="utf-8")
    if update_notification_frame is not None and update_notification_frame.winfo_exists():
        update_notification_frame.destroy()
    update_notification_frame = None


def ignore_branch_sync(current_branch: str) -> None:
    global ignore_branch, update_notification_frame
    ignore_branch = str(current_branch).strip() or None
    if ignore_branch:
        Path(IGNORE_BRANCH_FILE).write_text(ignore_branch, encoding="utf-8")
    if update_notification_frame is not None and update_notification_frame.winfo_exists():
        update_notification_frame.destroy()
    update_notification_frame = None



# Public compatibility aliases used by the main/settings modules.
def start_ui_dispatcher(root: Any) -> None:
    install_ui_dispatcher(root)


def stop_ui_dispatcher() -> None:
    shutdown_ui_dispatcher()


def dispatch_ui(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    run_on_ui(_ui_root, callback, *args, **kwargs)


def run_git(*args: str, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return _git(*args, timeout=timeout)


def start_update_checker(root: Any, interval: float = 30.0) -> threading.Thread:
    global checking_updates, _update_thread, ignore_commit
    if _update_thread is not None and _update_thread.is_alive():
        return _update_thread
    checking_updates = True
    try:
        value = Path(IGNORE_COMMIT_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    ignore_commit = value or None
    _update_thread = threading.Thread(
        target=check_for_updates,
        args=(root, interval),
        name="gms-update-checker",
        daemon=True,
    )
    _update_thread.start()
    return _update_thread


def stop_update_checker() -> None:
    global checking_updates
    checking_updates = False

def restart_application() -> None:
    python = sys.executable
    os.execl(python, python, *sys.argv)
