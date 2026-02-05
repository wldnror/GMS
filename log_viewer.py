# log_viewer.py
import datetime
from tkinter import Toplevel, Frame, Label, Button, Text, Scrollbar
from tkinter import filedialog, messagebox
from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


class LogViewerWindow:
    """
    logs: list of tuples -> (ts_str, value_int, alarm1_bool, alarm2_bool, error_reg_int)
    on_mark_read: callback to mark log as read (e.g. update badge)
    on_clear: callback after clear
    """
    def __init__(self, parent, title, ip_text, logs_ref, on_mark_read=None, on_clear=None):
        self.parent = parent
        self.logs_ref = logs_ref
        self.on_mark_read = on_mark_read
        self.on_clear = on_clear

        self.win = Toplevel(parent)
        self.win.title(title)
        self.win.configure(bg="#1e1e1e")
        self.win.resizable(True, True)

        top = Frame(self.win, bg="#1e1e1e")
        top.pack(fill="x", padx=10, pady=(10, 6))

        Label(
            top,
            text=ip_text,
            fg="white",
            bg="#1e1e1e",
            font=("Helvetica", 12, "bold"),
        ).pack(side="left")

        self.notebook = ttk.Notebook(self.win)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # --- Tab 1: Text
        self.tab_text = Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(self.tab_text, text="텍스트 로그")

        header = Label(
            self.tab_text,
            text="시간                 값      AL1  AL2  ERR",
            fg="#aaaaaa",
            bg="#1e1e1e",
            font=("Consolas", 10),
        )
        header.pack(padx=10, pady=(10, 0), anchor="w")

        log_frame = Frame(self.tab_text, bg="#1e1e1e")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        scrollbar = Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")

        self.text = Text(
            log_frame,
            bg="#121212",
            fg="#f0f0f0",
            insertbackground="white",
            font=("Consolas", 10),
            yscrollcommand=scrollbar.set,
            wrap="none",
        )
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.text.yview)

        # --- Tab 2: Graph
        self.tab_graph = Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(self.tab_graph, text="그래프")

        self.fig = Figure(figsize=(7.5, 3.8), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("값 추이")
        self.ax.set_xlabel("Time")
        self.ax.set_ylabel("Value")

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.tab_graph)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)

        # Bottom buttons
        btn_frame = Frame(self.win, bg="#1e1e1e")
        btn_frame.pack(padx=10, pady=(0, 10))

        Button(
            btn_frame,
            text="새로고침",
            command=self.refresh,
            width=12,
            bg="#555555",
            fg="white",
            relief="raised",
            bd=1,
        ).grid(row=0, column=0, padx=5, pady=5)

        Button(
            btn_frame,
            text="로그 삭제",
            command=self.clear_log,
            width=12,
            bg="#aa4444",
            fg="white",
            relief="raised",
            bd=1,
        ).grid(row=0, column=1, padx=5, pady=5)

        Button(
            btn_frame,
            text="파일로 저장",
            command=self.export_log,
            width=12,
            bg="#4444aa",
            fg="white",
            relief="raised",
            bd=1,
        ).grid(row=0, column=2, padx=5, pady=5)

        Button(
            btn_frame,
            text="닫기",
            command=self.close,
            width=10,
            bg="#333333",
            fg="white",
            relief="raised",
            bd=1,
        ).grid(row=0, column=3, padx=5, pady=5)

        self.win.protocol("WM_DELETE_WINDOW", self.close)

        # 최초 refresh + 읽음 처리
        self.refresh()
        if callable(self.on_mark_read):
            self.on_mark_read()

        # 자동 갱신(1초)
        self._auto_job = self.win.after(1000, self._auto_refresh)

    def close(self):
        try:
            if self._auto_job:
                self.win.after_cancel(self._auto_job)
        except Exception:
            pass
        self._auto_job = None
        self.win.destroy()

    def _auto_refresh(self):
        if self.win.winfo_exists():
            self.refresh()
            self._auto_job = self.win.after(1000, self._auto_refresh)

    def refresh(self):
        logs = self.logs_ref

        # ---- Text
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        for ts, val, a1, a2, err_reg in logs:
            a1_str = "ON " if a1 else "OFF"
            a2_str = "ON " if a2 else "OFF"
            err_str = f"0x{err_reg:04X}"
            line = f"{ts}  {val:6d}  {a1_str:>3}  {a2_str:>3}  {err_str}\n"
            self.text.insert("end", line)
        self.text.see("end")
        self.text.config(state="disabled")

        # ---- Graph
        self._draw_graph(logs)

    def _parse_ts(self, ts_str: str):
        # "YYYY-MM-DD HH:MM:SS"
        try:
            return datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    def _draw_graph(self, logs):
        self.ax.clear()
        self.ax.set_title("값 추이")
        self.ax.set_xlabel("Time")
        self.ax.set_ylabel("Value")

        if not logs:
            self.ax.text(0.5, 0.5, "로그 없음", ha="center", va="center")
            self.canvas.draw()
            return

        # 최근 N개만
        N = 250
        view = logs[-N:]

        xs = []
        ys = []
        al1_marks_x = []
        al1_marks_y = []
        al2_marks_x = []
        al2_marks_y = []

        for ts, val, a1, a2, _err in view:
            dt = self._parse_ts(ts)
            if dt is None:
                continue
            xs.append(dt)
            ys.append(val)
            if a1:
                al1_marks_x.append(dt)
                al1_marks_y.append(val)
            if a2:
                al2_marks_x.append(dt)
                al2_marks_y.append(val)

        if xs:
            self.ax.plot(xs, ys)
            # 알람 마커(색 지정은 안 함: matplotlib 기본색 사용)
            if al1_marks_x:
                self.ax.scatter(al1_marks_x, al1_marks_y, marker="o", s=18, label="AL1")
            if al2_marks_x:
                self.ax.scatter(al2_marks_x, al2_marks_y, marker="x", s=22, label="AL2")
            if al1_marks_x or al2_marks_x:
                self.ax.legend(loc="best")

            self.fig.autofmt_xdate()
        else:
            self.ax.text(0.5, 0.5, "시간 파싱 실패", ha="center", va="center")

        self.canvas.draw()

    def clear_log(self):
        if messagebox.askyesno("로그 삭제", "이 장치의 로그를 모두 삭제할까요?"):
            self.logs_ref.clear()
            self.refresh()
            if callable(self.on_clear):
                self.on_clear()

    def export_log(self):
        logs = self.logs_ref
        if not logs:
            messagebox.showinfo("로그 저장", "저장할 로그가 없습니다.")
            return
        path = filedialog.asksaveasfilename(
            title="로그 파일 저장",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("시간,값,AL1,AL2,ERR\n")
                for ts, val, a1, a2, err_reg in logs:
                    a1_str = "ON" if a1 else "OFF"
                    a2_str = "ON" if a2 else "OFF"
                    err_str = f"0x{err_reg:04X}"
                    f.write(f"{ts},{val},{a1_str},{a2_str},{err_str}\n")
            messagebox.showinfo("로그 저장", "로그 파일이 저장되었습니다.")
        except Exception as e:
            messagebox.showerror("로그 저장", f"로그 저장 중 오류가 발생했습니다.\n{e}")
