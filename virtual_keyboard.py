"""On-screen numeric keyboard used by IP address entry widgets."""

from __future__ import annotations

import tkinter as tk


class VirtualKeyboard:
    def __init__(self, root):
        self.root = root
        self.keyboard_window = None
        self.hide_timer = None
        self.num_boxes = 1
        self._bound_entry = None

    def set_num_boxes(self, num_boxes: int) -> None:
        self.num_boxes = max(0, int(num_boxes))

    def show(self, entry) -> None:
        if str(entry.cget("state")) != "normal":
            return
        self.hide()
        self._bound_entry = entry
        self.root.update_idletasks()

        keyboard_width = 260
        keyboard_height = 240
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        entry_x = entry.winfo_rootx()
        entry_y = entry.winfo_rooty()

        if self.num_boxes <= 1:
            root_x = self.root.winfo_rootx()
            x = root_x + max(0, (self.root.winfo_width() - keyboard_width) // 2)
        else:
            x = entry_x
        y = entry_y + entry.winfo_height() + 10
        if x + keyboard_width > screen_width:
            x = screen_width - keyboard_width - 10
        if y + keyboard_height > screen_height:
            y = entry_y - keyboard_height - 10
        x = max(0, x)
        y = max(0, y)

        window = tk.Toplevel(self.root)
        self.keyboard_window = window
        window.overrideredirect(True)
        window.geometry(f"{keyboard_width}x{keyboard_height}+{x}+{y}")
        window.attributes("-topmost", True)
        window.bind("<Escape>", lambda _event: self.hide())

        frame = tk.Frame(window)
        frame.pack(expand=True, fill="both")
        buttons = ("1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", "DEL")
        for index, value in enumerate(buttons):
            tk.Button(
                frame,
                text=value,
                width=5,
                height=2,
                command=lambda char=value: self.on_button_click(char, entry),
            ).grid(row=index // 3, column=index % 3, padx=5, pady=5)
        self.reset_hide_timer()

    def on_button_click(self, char: str, entry) -> None:
        if not entry.winfo_exists() or str(entry.cget("state")) != "normal":
            self.hide()
            return
        if char == "DEL":
            text = entry.get()
            entry.delete(0, tk.END)
            entry.insert(0, text[:-1])
        else:
            entry.insert(tk.END, char)
        self.reset_hide_timer()

    def reset_hide_timer(self, _event=None) -> None:
        if self.hide_timer is not None:
            try:
                self.root.after_cancel(self.hide_timer)
            except tk.TclError:
                pass
        self.hide_timer = self.root.after(10000, self.hide)

    def hide(self) -> None:
        if self.hide_timer is not None:
            try:
                self.root.after_cancel(self.hide_timer)
            except tk.TclError:
                pass
        self.hide_timer = None
        if self.keyboard_window is not None:
            try:
                if self.keyboard_window.winfo_exists():
                    self.keyboard_window.destroy()
            except tk.TclError:
                pass
        self.keyboard_window = None
        self._bound_entry = None

    def stop(self) -> None:
        self.hide()
