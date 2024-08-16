import tkinter as tk

class VirtualKeyboard:
    def __init__(self, root):
        self.root = root
        self.keyboard_window = None
        self.hide_timer = None

    def show(self, entry):
        # 기존 키보드 창이 있으면 닫음
        if self.keyboard_window and self.keyboard_window.winfo_exists():
            self.keyboard_window.destroy()

        root_width = self.root.winfo_width()
        root_height = self.root.winfo_height()

        x = entry.winfo_rootx() - self.root.winfo_rootx()
        y = entry.winfo_rooty() - self.root.winfo_rooty() + entry.winfo_width() + 50

        keyboard_width = 260  # 가상 키보드의 예상 너비
        keyboard_height = 240  # 가상 키보드의 예상 높이

        # 가상 키보드가 창 바깥으로 이탈하지 않도록 위치 조정
        if x + keyboard_width > root_width:
            x = entry.winfo_rootx() - self.root.winfo_rootx() - keyboard_width - 0
        if y + keyboard_height > root_height:
            y = entry.winfo_rooty() - self.root.winfo_rooty() - keyboard_height - 5
        if y < 0:
            y = 0

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
        entry.bind('<Button-1>', lambda event, e=entry: self.show(e))

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
