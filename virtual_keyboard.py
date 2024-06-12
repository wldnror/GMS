from tkinter import Toplevel, Frame, Button

class VirtualKeyboard:
    def __init__(self, master):
        self.master = master
        self.window = None
        self.target_entry = None  # 가상 키보드로 값을 입력할 타겟 입력 필드

    def show(self, target_entry):
        self.target_entry = target_entry
        if self.window and self.window.winfo_exists():
            self.window.focus()
            return

        self.window = Toplevel(self.master)
        self.window.title("Virtual Keyboard")
        self.window.attributes("-topmost", True)

        buttons_frame = Frame(self.window)
        buttons_frame.pack()

        buttons = [
            ('1', 1, 0), ('2', 1, 1), ('3', 1, 2),
            ('4', 2, 0), ('5', 2, 1), ('6', 2, 2),
            ('7', 3, 0), ('8', 3, 1), ('9', 3, 2),
            ('.', 4, 0), ('0', 4, 1), ('Del', 4, 2)
        ]

        for (text, row, col) in buttons:
            button = Button(buttons_frame, text=text, font=("Arial", 18),
                            command=lambda t=text: self.on_button_click(t))
            button.grid(row=row, column=col, padx=5, pady=5, ipadx=10, ipady=10)

    def on_button_click(self, char):
        if char == 'Del':
            current_text = self.target_entry.get()
            self.target_entry.delete(len(current_text) - 1, 'end')
        else:
            self.target_entry.insert('end', char)
