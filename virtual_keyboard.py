import tkinter as tk

class VirtualKeyboard:
    def __init__(self, root):
        self.root = root
        self.keyboard_window = None

    def show(self, entry):
        if self.keyboard_window and self.keyboard_window.winfo_exists():
            self.keyboard_window.destroy()

        root_width = self.root.winfo_width()
        root_height = self.root.winfo_height()

        x = entry.winfo_rootx()
        y = entry.winfo_rooty() + entry.winfo_height()

        keyboard_width = 270  # 가상 키보드의 예상 너비
        keyboard_height = 250  # 가상 키보드의 예상 높이

        # 가상 키보드가 창 바깥으로 이탈하지 않도록 위치 조정
        if x + keyboard_width > root_width:
            x = root_width - keyboard_width
        if y + keyboard_height > root_height:
            y = root_height - keyboard_height

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

    def on_button_click(self, char, entry):
        if char == 'DEL':
            current_text = entry.get()
            entry.delete(0, tk.END)
            entry.insert(0, current_text[:-1])
        else:
            entry.insert(tk.END, char)

    def hide(self):
        if self.keyboard_window and self.keyboard_window.winfo_exists():
            self.keyboard_window.destroy()
