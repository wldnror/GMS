# virtual_keyboard.py

import tkinter as tk

class VirtualKeyboard:
    def __init__(self, root):
        self.root = root
        self.keyboard_window = None
        self.hide_timer = None
        self.num_boxes = 1  # 기본적으로 상자의 수를 1로 설정

    def set_num_boxes(self, num_boxes):
        self.num_boxes = num_boxes  # 상자의 수를 설정하는 메서드

    def show(self, entry):
        # Entry가 disabled 상태인지 확인
        if entry['state'] != 'normal':
            return  # disabled 상태이면 가상 키보드를 표시하지 않음

        # 기존 키보드 창이 있으면 닫음
        if self.keyboard_window and self.keyboard_window.winfo_exists():
            self.keyboard_window.destroy()

        root_width = self.root.winfo_width()
        root_height = self.root.winfo_height()

        entry_x = entry.winfo_rootx() - self.root.winfo_rootx()
        entry_y = entry.winfo_rooty() - self.root.winfo_rooty()

        keyboard_width = 260  # 가상 키보드의 예상 너비
        keyboard_height = 240  # 가상 키보드의 예상 높이

        # 상자의 개수에 따라 키보드 위치를 동적으로 조정
        if self.num_boxes == 1:
            # 상자가 1개일 경우, 화면 중앙에 키보드 배치
            x = (root_width - keyboard_width) // 2
            y = entry_y + entry.winfo_height() + 10
        else:
            # 상자가 여러 개일 경우, 상자 바로 아래에 키보드를 배치
            x = entry_x
            y = entry_y + entry.winfo_height() + 10

        # 가상 키보드가 창 바깥으로 이탈하지 않도록 위치 조정
        if x + keyboard_width > root_width:
            x = root_width - keyboard_width - 10
        if y + keyboard_height > root_height:
            y = entry_y - keyboard_height - 10
        if y < 0:
            y = 0
        if x < 0:
            x = 0

        self.keyboard_window = tk.Toplevel(self.root)
        self.keyboard_window.overrideredirect(True)
        self.keyboard_window.geometry(f"{keyboard_width}x{keyboard_height}+{x}+{y}")
        self.keyboard_window.attributes("-topmost", True)

        frame = tk.Frame(self.keyboard_window)
        frame.pack()

        buttons = [
            '1', '2', '3',
            '4', '5', '6',
            '7', '8', '9',
            '.', '0', 'DEL'
        ]

        rows = 4
        cols = 3
        for i, button in enumerate(buttons):
            b = tk.Button(frame, text=button, width=5, height=2,
                          command=lambda b=button: self.on_button_click(b, entry))
            b.grid(row=i // cols, column=i % cols, padx=5, pady=5)

        self.reset_hide_timer()

        # Entry 위젯에 포커스 인 이벤트 추가
        entry.bind('<Key>', self.reset_hide_timer)
        # '<Button-1>' 이벤트 바인딩 제거
        # entry.bind('<Button-1>', lambda event, e=entry: self.show(e))

    def on_button_click(self, char, entry):
        if char == 'DEL':
            current_text = entry.get()
            entry.delete(0, tk.END)
            entry.insert(0, current_text[:-1])
        else:
            entry.insert(tk.END, char)
        self.reset_hide_timer()  # 입력 시 타이머 리셋

    def reset_hide_timer(self, event=None):
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
        self.hide_timer = self.root.after(10000, self.hide)  # 10초 후에 hide 메서드 호출

    def hide(self):
        if self.keyboard_window and self.keyboard_window.winfo_exists():
            self.keyboard_window.destroy()
        self.hide_timer = None  # 타이머 초기화
