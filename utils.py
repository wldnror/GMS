from __future__ import annotations

import fcntl
import os
import queue
import random
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from tkinter import Button, Frame, Label, messagebox
from typing import Any

from cryptography.fernet import Fernet

BASE_DIR = Path(__file__).resolve().parent
GIT_BINARY = "/usr/bin/git"
STATE_DIR = Path(os.environ.get("GMS_STATE_DIR", str(BASE_DIR))).expanduser().resolve()
KEY_FILE = str(STATE_DIR / "secret.key")
IGNORE_COMMIT_FILE = str(STATE_DIR / "ignore_commit.txt")
IGNORE_BRANCH_FILE = str(STATE_DIR / "ignore_branch.txt")
SYNCED_BRANCHES_FILE = str(STATE_DIR / "synced_branches.txt")

checking_updates = False
ignore_commit: str | None = None
ignore_branch: str | None = None
synced_branches: set[str] = set()
update_notification_frame: Frame | None = None
_update_lock = threading.Lock()
_ui_root: Any = None
_ui_queue: queue.Queue[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = (
    queue.Queue()
)
_ui_after_id: str | None = None
_update_thread: threading.Thread | None = None
_update_stop_event = threading.Event()
_update_authorizer: Callable[[Callable[[], Any]], Any] | None = None
_cipher_lock = threading.Lock()
_cipher_suite: Fernet | None = None
_instance_handle: Any = None
_git_mutation_lock = threading.Lock()
_git_workers_lock = threading.Lock()
_git_workers: set[threading.Thread] = set()


def ensure_state_dir() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(STATE_DIR, 0o700)
    except OSError:
        pass
    return STATE_DIR


def acquire_instance_lock() -> None:
    """Prevent two kiosk processes from driving the same hardware/state."""
    global _instance_handle
    if _instance_handle is not None:
        return
    path = ensure_state_dir() / ".gms-instance.lock"
    handle = path.open("a+", encoding="ascii")
    try:
        os.chmod(path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
    except (BlockingIOError, OSError) as exc:
        handle.close()
        raise RuntimeError(f"다른 GMS 인스턴스가 이미 실행 중입니다 ({path}).") from exc
    _instance_handle = handle


def release_instance_lock() -> None:
    global _instance_handle
    handle = _instance_handle
    _instance_handle = None
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def start_git_worker(
    target: Callable[..., Any],
    *,
    args: tuple[Any, ...] = (),
    name: str,
) -> threading.Thread:
    """Start and track a Git-related worker so restart cannot race it."""

    def runner() -> None:
        try:
            target(*args)
        finally:
            with _git_workers_lock:
                _git_workers.discard(threading.current_thread())

    thread = threading.Thread(target=runner, name=name, daemon=True)
    with _git_workers_lock:
        _git_workers.add(thread)
    try:
        thread.start()
    except Exception:
        with _git_workers_lock:
            _git_workers.discard(thread)
        raise
    return thread


def begin_git_mutation() -> bool:
    """Reserve the working tree for one checkout/update operation."""

    return _git_mutation_lock.acquire(blocking=False)


def end_git_mutation() -> None:
    if _git_mutation_lock.locked():
        _git_mutation_lock.release()


def prepare_for_shutdown() -> None:
    """Stop scheduling update checks without blocking the Tk main thread."""

    global checking_updates
    checking_updates = False
    _update_stop_event.set()


def git_operations_in_progress() -> bool:
    with _git_workers_lock:
        active = any(thread.is_alive() for thread in _git_workers)
    checker = _update_thread
    return (
        active
        or _git_mutation_lock.locked()
        or (checker is not None and checker.is_alive())
    )


def fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


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


def _write_new_key(path: Path) -> bytes:
    key = Fernet.generate_key()
    fd, temp_name = tempfile.mkstemp(prefix=".secret-key.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        fsync_directory(path.parent)
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except OSError:
            pass
    return key


def generate_key() -> bytes:
    """Create a key once, safely, even if two GMS processes start together."""
    path = Path(KEY_FILE)
    ensure_state_dir()
    lock_path = path.with_name(".secret-key.lock")
    with lock_path.open("a+b") as lock_handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        if path.exists():
            return _read_valid_key(path)
        settings_path = STATE_DIR / "settings.json"
        if settings_path.exists():
            raise RuntimeError(
                f"설정 파일은 있지만 암호화 키가 없습니다: {path}. 새 키를 만들지 "
                "않았습니다. secret.key와 settings.json 백업을 함께 복원하세요."
            )
        return _write_new_key(path)


def _read_valid_key(path: Path) -> bytes:
    key = path.read_bytes().strip()
    try:
        Fernet(key)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"암호화 키가 손상되었습니다: {path}. secret.key와 settings.json의 "
            "백업 쌍을 복원해야 합니다."
        ) from exc
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def load_key() -> bytes:
    path = Path(KEY_FILE)
    ensure_state_dir()
    lock_path = path.with_name(".secret-key.lock")
    with lock_path.open("a+b") as lock_handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        if path.exists():
            return _read_valid_key(path)
        settings_path = STATE_DIR / "settings.json"
        if settings_path.exists():
            raise RuntimeError(
                f"설정 파일은 있지만 암호화 키가 없습니다: {path}. 새 키를 만들지 "
                "않았습니다. secret.key와 settings.json 백업을 함께 복원하세요."
            )
        return _write_new_key(path)


def _get_cipher_suite() -> Fernet:
    global _cipher_suite
    if _cipher_suite is None:
        with _cipher_lock:
            if _cipher_suite is None:
                _cipher_suite = Fernet(load_key())
    return _cipher_suite


def encrypt_data(data: str) -> bytes:
    return _get_cipher_suite().encrypt(str(data).encode("utf-8"))


def decrypt_data(data: bytes) -> str:
    return _get_cipher_suite().decrypt(data).decode("utf-8")


def run_on_ui(
    root: Any, callback: Callable[..., Any], *args: Any, **kwargs: Any
) -> None:
    """Queue a Tk callback without calling Tcl from the worker thread."""
    del root  # The installed dispatcher owns the Tk root.
    if _ui_root is None:
        return
    _ui_queue.put((callback, args, kwargs))


def create_keypad(
    entry: Any,
    parent: Any,
    row: int | None = None,
    column: int | None = None,
    columnspan: int = 1,
    geometry: str = "grid",
) -> Frame:
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
        [GIT_BINARY, *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_text(*args: str) -> str:
    result = _git(*args)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "git command failed"
        )
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
    if not begin_git_mutation():
        return None
    try:
        fetch = _git("fetch", "--quiet", "origin", branch, timeout=20)
        if fetch.returncode != 0:
            return None
        return _git_text("rev-parse", f"origin/{branch}") or None
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return None
    finally:
        end_git_mutation()


def commit_relation(local: str | None, remote: str | None) -> str:
    """Classify two commits without treating local-ahead as an update."""
    if not local or not remote:
        return "unknown"
    if local == remote:
        return "equal"
    try:
        local_is_ancestor = _git("merge-base", "--is-ancestor", local, remote)
        if local_is_ancestor.returncode == 0:
            return "behind"
        remote_is_ancestor = _git("merge-base", "--is-ancestor", remote, local)
        if remote_is_ancestor.returncode == 0:
            return "ahead"
        if local_is_ancestor.returncode == 1 and remote_is_ancestor.returncode == 1:
            return "diverged"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return "unknown"


def has_tracked_changes() -> bool:
    try:
        result = _git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.TimeoutExpired):
        return True
    return result.returncode != 0 or bool(result.stdout.strip())


def load_synced_branches() -> None:
    global synced_branches
    path = Path(SYNCED_BRANCHES_FILE)
    if path.exists():
        synced_branches = {
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }


def save_synced_branch(branch: str) -> None:
    branch = branch.strip()
    if not branch or branch in synced_branches:
        return
    synced_branches.add(branch)
    ensure_state_dir()
    path = Path(SYNCED_BRANCHES_FILE)
    with path.open("a", encoding="utf-8") as file:
        file.write(branch + "\n")


def _show_info(root: Any, title: str, message: str) -> None:
    run_on_ui(root, messagebox.showinfo, title, message)


def _show_error(root: Any, title: str, message: str) -> None:
    run_on_ui(root, messagebox.showerror, title, message)


def update_system(root: Any, expected_commit: str | None = None) -> None:
    """Fast-forward the current branch and restart only after a verified success."""
    if not _update_lock.acquire(blocking=False):
        _show_info(root, "시스템 업데이트", "이미 업데이트를 진행 중입니다.")
        return
    mutation_acquired = begin_git_mutation()
    if not mutation_acquired:
        _update_lock.release()
        _show_info(root, "시스템 업데이트", "다른 Git 작업이 진행 중입니다.")
        return

    try:
        branch = _git_text("branch", "--show-current")
        if not branch:
            raise RuntimeError("현재 Git 브랜치를 확인할 수 없습니다.")

        fetch = _git("fetch", "--prune", "origin", branch, timeout=30)
        if fetch.returncode != 0:
            raise RuntimeError(fetch.stderr.strip() or "git fetch 실패")

        local = _git_text("rev-parse", "HEAD")
        remote = _git_text("rev-parse", f"origin/{branch}")
        if expected_commit and remote != expected_commit:
            raise RuntimeError(
                "확인한 업데이트 이후 원격 커밋이 변경되었습니다. 다시 확인해 주세요."
            )
        relation = commit_relation(local, remote)
        if relation == "equal":
            _show_info(root, "시스템 업데이트", "이미 최신 버전입니다.")
            return
        if relation != "behind":
            raise RuntimeError(
                f"fast-forward 업데이트를 적용할 수 없습니다 (상태: {relation})."
            )
        if has_tracked_changes():
            raise RuntimeError(
                "추적 중인 로컬 변경이 있어 자동 업데이트를 중단했습니다."
            )
        dependency_diff = _git(
            "diff", "--quiet", local, remote, "--", "requirements.txt"
        )
        if dependency_diff.returncode == 1:
            raise RuntimeError(
                "requirements.txt가 변경된 업데이트입니다. 새 가상환경에서 의존성과 "
                "테스트를 검증한 뒤 수동 배포하세요."
            )
        if dependency_diff.returncode != 0:
            raise RuntimeError("업데이트 의존성 변경 여부를 확인하지 못했습니다.")

        merge = _git("merge", "--ff-only", remote, timeout=30)
        if merge.returncode != 0:
            raise RuntimeError(
                "자동 업데이트는 fast-forward만 허용됩니다.\n"
                + (merge.stderr.strip() or merge.stdout.strip() or "git merge 실패")
            )

        _show_info(
            root,
            "시스템 업데이트",
            "업데이트가 완료되었습니다. 애플리케이션을 재시작합니다.",
        )
        run_on_ui(root, lambda: root.after(1200, restart_application))
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        _show_error(root, "시스템 업데이트", f"업데이트 중 오류가 발생했습니다.\n{exc}")
    finally:
        end_git_mutation()
        _update_lock.release()


def check_for_updates(root: Any, interval: float = 30.0) -> None:
    """Poll only the current branch without changing the working tree."""
    global checking_updates
    load_synced_branches()

    while checking_updates:
        mutation_acquired = False
        try:
            mutation_acquired = begin_git_mutation()
            if not mutation_acquired:
                if _update_stop_event.wait(max(60.0, float(interval))):
                    break
                continue
            current_branch = _git_text("branch", "--show-current")
            if current_branch:
                fetch = _git(
                    "fetch", "--quiet", "--prune", "origin", current_branch, timeout=20
                )
                if fetch.returncode == 0:
                    local_commit = _git_text("rev-parse", "HEAD")
                    remote_commit = _git_text("rev-parse", f"origin/{current_branch}")
                    if (
                        commit_relation(local_commit, remote_commit) == "behind"
                        and remote_commit != ignore_commit
                    ):
                        run_on_ui(root, show_update_notification, root, remote_commit)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            print(f"[Update] check failed: {exc}")
        finally:
            if mutation_acquired:
                end_git_mutation()

        if _update_stop_event.wait(max(60.0, float(interval))):
            break


def sync_branches(root: Any) -> None:
    try:
        result = _git("fetch", "--all", "--prune", timeout=30)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git fetch 실패")
        run_on_ui(
            root,
            show_temporary_notification,
            root,
            "원격 브랜치 정보가 동기화되었습니다.",
        )
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        _show_error(root, "브랜치 동기화 오류", str(exc))


def show_update_notification(root: Any, remote_commit: str) -> None:
    global update_notification_frame
    if (
        update_notification_frame is not None
        and update_notification_frame.winfo_exists()
    ):
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
        command=lambda: request_update_authorization(root, remote_commit),
        font=("Arial", 14),
        fg="red",
    ).pack(side="left", padx=5)
    Button(
        frame,
        text="건너뛰기",
        command=lambda: request_ignore_authorization(remote_commit),
        font=("Arial", 14),
        fg="red",
    ).pack(side="left", padx=5)


def request_update_authorization(root: Any, remote_commit: str) -> None:
    def callback() -> None:
        start_update(root, remote_commit)

    if _update_authorizer is None:
        callback()
    else:
        _update_authorizer(callback)


def request_ignore_authorization(remote_commit: str) -> None:
    def callback() -> None:
        ignore_update(remote_commit)

    if _update_authorizer is None:
        callback()
    else:
        _update_authorizer(callback)


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
    global update_notification_frame, ignore_commit
    ignore_commit = None
    try:
        Path(IGNORE_COMMIT_FILE).unlink(missing_ok=True)
    except OSError:
        pass
    if (
        update_notification_frame is not None
        and update_notification_frame.winfo_exists()
    ):
        update_notification_frame.destroy()
    update_notification_frame = None
    start_git_worker(
        update_system,
        args=(root, remote_commit),
        name="gms-updater",
    )


def ignore_update(remote_commit: str) -> None:
    global ignore_commit, update_notification_frame
    ignore_commit = str(remote_commit).strip() or None
    if ignore_commit:
        ensure_state_dir()
        Path(IGNORE_COMMIT_FILE).write_text(ignore_commit, encoding="utf-8")
    if (
        update_notification_frame is not None
        and update_notification_frame.winfo_exists()
    ):
        update_notification_frame.destroy()
    update_notification_frame = None


def ignore_branch_sync(current_branch: str) -> None:
    global ignore_branch, update_notification_frame
    ignore_branch = str(current_branch).strip() or None
    if ignore_branch:
        ensure_state_dir()
        Path(IGNORE_BRANCH_FILE).write_text(ignore_branch, encoding="utf-8")
    if (
        update_notification_frame is not None
        and update_notification_frame.winfo_exists()
    ):
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


def start_update_checker(
    root: Any,
    interval: float = 900.0,
    authorize_callback: Callable[[Callable[[], Any]], Any] | None = None,
) -> threading.Thread:
    global checking_updates, _update_authorizer, _update_thread, ignore_commit
    if _update_thread is not None and _update_thread.is_alive():
        return _update_thread
    checking_updates = True
    _update_authorizer = authorize_callback
    _update_stop_event.clear()
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
    global checking_updates, _update_thread
    checking_updates = False
    _update_stop_event.set()
    thread = _update_thread
    if (
        thread is not None
        and thread.is_alive()
        and thread is not threading.current_thread()
    ):
        thread.join(timeout=2.0)
    if thread is None or not thread.is_alive():
        _update_thread = None


def restart_application() -> None:
    root = _ui_root
    if root is not None:
        prepare_for_shutdown()
        if git_operations_in_progress():
            try:
                root.after(250, restart_application)
            except Exception:
                pass
            return
        run_on_ui(root, root.event_generate, "<<GMSRestartRequested>>", when="tail")
        return
    python = sys.executable
    os.execl(python, python, *sys.argv)
