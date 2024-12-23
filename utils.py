# utils.py
import os
import random
import subprocess
import sys
import threading
import time
from tkinter import Frame, Button, Label, messagebox, Toplevel

from cryptography.fernet import Fernet

KEY_FILE = "secret.key"
IGNORE_COMMIT_FILE = "ignore_commit.txt"
IGNORE_BRANCH_FILE = "ignore_branch.txt"  # 브랜치 무시 파일 추가
SYNCED_BRANCHES_FILE = "synced_branches.txt"  # 이미 동기화된 브랜치를 기록할 파일

# 전역 변수
checking_updates = False
ignore_commit = None
ignore_branch = None  # 무시된 브랜치 저장 변수
synced_branches = set()  # 동기화된 브랜치 집합
update_notification_frame = None

# 암호화 키 생성 및 로드
def generate_key():
    key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as key_file:
        key_file.write(key)

def load_key():
    if not os.path.exists(KEY_FILE):
        generate_key()
    with open(KEY_FILE, "rb") as key_file:
        return key_file.read()

key = load_key()
cipher_suite = Fernet(key)

def encrypt_data(data):
    return cipher_suite.encrypt(data.encode())

def decrypt_data(data):
    return cipher_suite.decrypt(data).decode()

def create_keypad(entry, parent, row=None, column=None, columnspan=1, geometry="grid"):
    keypad_frame = Frame(parent)
    if geometry == "grid":
        keypad_frame.grid(row=row, column=column, columnspan=columnspan, pady=5)
    elif geometry == "pack":
        keypad_frame.pack()

    def on_button_click(char):
        if char == 'DEL':
            current_text = entry.get()
            entry.delete(0, 'end')
            entry.insert(0, current_text[:-1])
        elif char == 'CLR':
            entry.delete(0, 'end')
        else:
            entry.insert('end', char)

    buttons = [str(i) for i in range(10)]
    random.shuffle(buttons)
    buttons.append('CLR')
    buttons.append('DEL')

    rows = 4
    cols = 3
    for i, button in enumerate(buttons):
        b = Button(keypad_frame, text=button, width=5, height=2, command=lambda b=button: on_button_click(b))
        b.grid(row=i // cols, column=i % cols, padx=5, pady=5)

    return keypad_frame

def exit_fullscreen(root, event=None):
    root.attributes("-fullscreen", False)
    root.attributes("-topmost", False)

def enter_fullscreen(root, event=None):
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)

def exit_application(root):
    root.destroy()
    sys.exit(0)

def load_synced_branches():
    """이미 동기화된 브랜치를 파일에서 로드"""
    global synced_branches
    if os.path.exists(SYNCED_BRANCHES_FILE):
        with open(SYNCED_BRANCHES_FILE, "r") as file:
            synced_branches = set(file.read().splitlines())

def save_synced_branch(branch):
    """동기화된 브랜치를 파일에 저장"""
    global synced_branches
    synced_branches.add(branch)
    with open(SYNCED_BRANCHES_FILE, "a") as file:
        file.write(branch + "\n")

def update_system(root):
    global checking_updates
    checking_updates = False  # 업데이트 중에 확인을 중지
    try:
        result = subprocess.run(['git', 'pull'], capture_output=True, text=True)
        message = "업데이트 완료. 애플리케이션을 재시작합니다."
        root.after(2000, restart_application)
    except Exception as e:
        message = f"업데이트 중 오류 발생: {e}"
    finally:
        checking_updates = True  # 업데이트 완료 후 다시 업데이트 체크 가능하게 설정
    
    messagebox.showinfo("시스템 업데이트", message)

def check_for_updates(root):
    global checking_updates, ignore_commit, ignore_branch
    load_synced_branches()
    
    while checking_updates:
        try:
            # 현재 체크아웃된 브랜치 이름 가져오기
            current_branch = subprocess.check_output(['git', 'branch', '--show-current']).strip().decode()

            # 원격 브랜치 목록 가져오기
            remote_branches = subprocess.check_output(['git', 'ls-remote', '--heads', 'origin']).strip().decode().splitlines()
            remote_branch_names = [line.split()[1].split('/')[-1] for line in remote_branches]

            # 로컬에서 추적하는 원격 브랜치 목록 가져오기
            tracked_remote_branches = subprocess.check_output(['git', 'branch', '-r']).strip().decode().splitlines()
            tracked_remote_branch_names = [line.split('/')[-1].strip() for line in tracked_remote_branches]

            # 로컬 브랜치 목록 가져오기
            local_branches = subprocess.check_output(['git', 'branch', '--list']).strip().decode().splitlines()
            local_branch_names = [branch.strip().replace('* ', '') for branch in local_branches]

            # 삭제된 원격 브랜치 확인
            deleted_branches = [branch for branch in tracked_remote_branch_names if branch not in remote_branch_names]

            # 새로운 원격 브랜치 확인
            new_branches = [branch for branch in remote_branch_names if branch not in local_branch_names and branch not in synced_branches]

            # 원격 브랜치의 커밋 해시 가져오기
            remote_branch_info = subprocess.check_output(['git', 'ls-remote', '--heads', 'origin', current_branch]).strip().decode()
            remote_branch_commit = remote_branch_info.split()[0] if remote_branch_info else None

            # 로컬 브랜치의 커밋 해시 가져오기
            local_commit = subprocess.check_output(['git', 'rev-parse', current_branch]).strip().decode()

            # 삭제된 원격 브랜치 동기화
            if deleted_branches:
                prune_deleted_branches(root)
                show_temporary_notification(root, "삭제된 브랜치가 정리되었습니다.")
            elif new_branches:  # 새로운 브랜치가 발견된 경우
                sync_branches(root)  # 새로운 브랜치가 있으면 자동 동기화
                for branch in new_branches:
                    save_synced_branch(branch)
                show_temporary_notification(root, "새로운 브랜치가 동기화되었습니다.")
            elif local_commit != remote_branch_commit and remote_branch_commit != ignore_commit:
                show_update_notification(root, remote_branch_commit)
        except Exception as e:
            print(f"Error checking for updates or branch sync: {e}")
        
        time.sleep(1)

def sync_branches(root):
    try:
        subprocess.check_call(['git', 'fetch', '--all'])
        subprocess.check_call(['git', 'pull', '--all'])
        subprocess.check_call(['git', 'remote', 'prune', 'origin'])
    except subprocess.CalledProcessError as e:
        messagebox.showerror("브랜치 동기화 오류", f"브랜치 동기화 중 오류가 발생했습니다: {e}")

def show_update_notification(root, remote_commit):
    global update_notification_frame
    if update_notification_frame and update_notification_frame.winfo_exists():
        return

    def on_yes():
        start_update(root, remote_commit)
    def on_no():
        ignore_update(remote_commit)

    update_notification_frame = Frame(root)
    update_notification_frame.place(relx=0.5, rely=0.95, anchor='center')

    update_label = Label(update_notification_frame, text="새로운 버젼이 있습니다. 업데이트를 진행하시겠습니까?", font=("Arial", 15), fg="red")
    update_label.pack(side="left", padx=5)

    yes_button = Button(update_notification_frame, text="예", command=on_yes, font=("Arial", 14), fg="red")
    yes_button.pack(side="left", padx=5)
    
    no_button = Button(update_notification_frame, text="건너뛰기", command=on_no, font=("Arial", 14), fg="red")
    no_button.pack(side="left", padx=5)

def show_temporary_notification(root, message, duration=5000):
    """ 일시적인 알림을 화면에 표시 """
    notification_frame = Frame(root, bg="green")
    notification_frame.place(relx=0.5, rely=0.95, anchor='center')

    notification_label = Label(notification_frame, text=message, font=("Arial", 14), fg="white", bg="green")
    notification_label.pack(side="left", padx=5)

    # 지정된 시간 후 알림이 사라지도록 설정
    root.after(duration, notification_frame.destroy)

def prune_deleted_branches(root):
    try:
        # 로컬에서 원격에서 삭제된 브랜치를 삭제합니다
        subprocess.check_call(['git', 'fetch', '--prune'])
    except subprocess.CalledProcessError as e:
        messagebox.showerror("브랜치 정리 오류", f"브랜치 정리 중 오류가 발생했습니다: {e}")

def start_update(root, remote_commit):
    global update_notification_frame, ignore_commit, checking_updates
    ignore_commit = None  # '예'를 누르면 기록된 커밋을 초기화
    checking_updates = False  # 업데이트 시작 시 업데이트 체크 중지
    if update_notification_frame and update_notification_frame.winfo_exists():
        update_notification_frame.destroy()
    threading.Thread(target=update_system, args=(root,)).start()

def ignore_update(remote_commit):
    global ignore_commit, update_notification_frame
    ignore_commit = remote_commit
    with open(IGNORE_COMMIT_FILE, "w") as file:
        file.write(ignore_commit)
    if update_notification_frame and update_notification_frame.winfo_exists():
        update_notification_frame.destroy()

def ignore_branch_sync(current_branch):
    global ignore_branch, update_notification_frame
    ignore_branch = current_branch
    with open(IGNORE_BRANCH_FILE, "w") as file:
        file.write(ignore_branch)
    if update_notification_frame and update_notification_frame.winfo_exists():
        update_notification_frame.destroy()

def restart_application():
    python = sys.executable
    os.execl(python, python, *sys.argv)
