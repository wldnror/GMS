import tkinter as tk

class VirtualKeyboard:
    def __init__(self, root):
        self.root = root
        self.keyboard_window = None

    def show(self, entry):
        if self.keyboard_window and self.keyboard_window.winfo_exists():
            self.keyboard_window.destroy()

        x = entry.winfo_rootx()
        y = entry.winfo_rooty() + entry.winfo_height()

        self.keyboard_window = tk.Toplevel(self.root)
        self.keyboard_window.overrideredirect(True)
        self.keyboard_window.geometry(f"+{x}+{y}")
        self.keyboard_window.attributes("-topmost", True)

        frame = tk.Frame(self.keyboard_window)
        frame.pack()

        buttons = [
            '1', '2', '3',
            '4', '5', '6',
            '7', '8', '9',
            '.', '0', 'DEL'
        ]

        for button in buttons:
            b = tk.Button(frame, text=button, width=5, height=2,
                          command=lambda b=button: self.on_button_click(b, entry))
            b.pack(side=tk.LEFT)

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
