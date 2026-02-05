# log_viewer.py
import tkinter as tk
from tkinter import ttk, Frame, Label, Scrollbar, Text, filedialog, messagebox
import datetime

# 그래프용
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


class LogViewer(tk.Toplevel):
    """
    로그를 '텍스트' + '그래프'로 보여주는 전용 창.
    logs: List[ (ts:str, value:int, alarm1:bool, alarm2:bool, error_reg:int) ]
    """

    def __init__(self, master, *, box_index: int, ip: str, get_logs_callable, on_clear_callable=None):
        super().__init__(master)

        self.box_index = box_index
        self.ip = ip
        self.get_logs = get_logs_callable
        self.on_clear = on_clear_callable

        self.title(f"Box {box_index + 1} 로그")
        self.configure(bg="#1e1e1e")
        self.geometry("900x520")
        self.minsize(820, 460)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 상단 타이틀
        Label(
            self,
            text=f"장치 {box_index + 1} 로그 (IP: {ip})",
            fg="white",
            bg="#1e1e1e",
            font=("Helvetica", 12, "bold"),
        ).pack(padx=12, pady=(12, 6), anchor="w")

        # 탭
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        # --- 텍스트 탭 ---
        self.tab_text = Frame(self, bg="#1e1e1e")
        self.nb.add(self.tab_text, text="텍스트")

        header = Label(
            self.tab_text,
            text="시간                 값      AL1  AL2  ERR",
            fg="#aaaaaa",
            bg="#1e1e1e",
            font=("Consolas", 10),
        )
        header.pack(padx=10, pady=(10, 2), anchor="w")

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
        self.text.config(state="disabled")

        # --- 그래프 탭 ---
        self.tab_graph = Frame(self, bg="#1e1e1e")
        self.nb.add(self.tab_graph, text="그래프")

        top_bar = Frame(self.tab_graph, bg="#1e1e1e")
        top_bar.pack(fill="x", padx=10, pady=(10, 0))

        Label(top_bar, text="표시 개수:", fg="white", bg="#1e1e1e", font=("Helvetica", 10)).pack(side="left")

        self.last_n_var = tk.StringVar(value="200")
        self.last_n_entry = tk.Entry(top_bar, textvariable=self.last_n_var, width=6)
        self.last_n_entry.pack(side="left", padx=(6, 10))
        self.last_n_entry.bind("<Return>", lambda e: self.refresh())

        self.btn_refresh = tk.Button(top_bar, text="새로고침", command=self.refresh)
        self.btn_refresh.pack(side="left", padx=5)

        self.btn_export = tk.Button(top_bar, text="파일로 저장", command=self.export_log)
        self.btn_export.pack(side="left", padx=5)

        self.btn_clear = tk.Button(top_bar, text="로그 삭제", command=self.clear_log)
        self.btn_clear.pack(side="left", padx=5)

        self.btn_close = tk.Button(top_bar, text="닫기", command=self._on_close)
        self.btn_close.pack(side="right", padx=5)

        # Figure
        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Value Trend")
        self.ax.set_xlabel("Time")
        self.ax.set_ylabel("Value")

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.tab_graph)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill="both", expand=True, padx=10, pady=10)

        # 주기 새로고침
        self._auto_refresh_ms = 1000
        self._alive = True
        self.after(self._auto_refresh_ms, self._auto_refresh)

        # 처음 표시
        self.refresh()

        # 포커스/최상위
        self.attributes("-topmost", True)
        self.transient(master)

    def _on_close(self):
        self._alive = False
        try:
            self.destroy()
        except Exception:
            pass

    def _auto_refresh(self):
        if not self._alive:
            return
        try:
            self.refresh()
        except Exception:
            pass
        self.after(self._auto_refresh_ms, self._auto_refresh)

    def _get_last_n(self) -> int:
        try:
            n = int((self.last_n_var.get() or "").strip())
            if n <= 0:
                n = 200
        except Exception:
            n = 200

        # 너무 큰 값 방지(선택): UI 느려질 수 있음
        if n > 5000:
            n = 5000

        # 입력칸 보정
        try:
            if str(n) != (self.last_n_var.get() or "").strip():
                self.last_n_var.set(str(n))
        except Exception:
            pass

        return n

    def _parse_logs(self):
        logs = list(self.get_logs() or [])

        # last N
        n = self._get_last_n()
        if len(logs) > n:
            logs = logs[-n:]

        xs = []
        ys = []
        a1_flags = []
        a2_flags = []
        err_flags = []

        for ts, val, a1, a2, err in logs:
            try:
                dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except Exception:
                dt = None
            xs.append(dt)
            ys.append(int(val) if val is not None else 0)
            a1_flags.append(bool(a1))
            a2_flags.append(bool(a2))
            err_flags.append(int(err) if err is not None else 0)

        return logs, xs, ys, a1_flags, a2_flags, err_flags

    def refresh(self):
        logs, xs, ys, a1, a2, err = self._parse_logs()

        # ---- 텍스트 갱신 ----
        self.text.config(state="normal")
        self.text.delete("1.0", "end")

        if not logs:
            self.text.insert("end", "로그가 없습니다.\n")
        else:
            for ts, val, al1, al2, err_reg in logs:
                a1_str = "ON " if al1 else "OFF"
                a2_str = "ON " if al2 else "OFF"
                err_str = f"0x{int(err_reg):04X}"
                line = f"{ts}  {int(val):6d}  {a1_str:>3}  {a2_str:>3}  {err_str}\n"
                self.text.insert("end", line)

        self.text.see("end")
        self.text.config(state="disabled")

        # ---- 그래프 갱신 ----
        self.ax.clear()
        self.ax.set_title("Value Trend")
        self.ax.set_xlabel("Time")
        self.ax.set_ylabel("Value")

        if not ys:
            self.ax.text(0.5, 0.5, "No logs", ha="center", va="center", transform=self.ax.transAxes)
            self.canvas.draw_idle()
            return

        # dt가 없는 경우 대비: 인덱스로 표시
        if any(x is None for x in xs) or len(xs) == 0:
            self.ax.plot(list(range(len(ys))), ys)
            self.ax.set_xlabel("Index")
        else:
            self.ax.plot(xs, ys)
            self.fig.autofmt_xdate()

        # AL1/AL2 상태는 점으로 오버레이
        idxs_a1 = [i for i, f in enumerate(a1) if f]
        idxs_a2 = [i for i, f in enumerate(a2) if f]

        if idxs_a1:
            if any(x is None for x in xs):
                self.ax.scatter(idxs_a1, [ys[i] for i in idxs_a1], marker="o", label="AL1")
            else:
                self.ax.scatter([xs[i] for i in idxs_a1], [ys[i] for i in idxs_a1], marker="o", label="AL1")

        if idxs_a2:
            if any(x is None for x in xs):
                self.ax.scatter(idxs_a2, [ys[i] for i in idxs_a2], marker="x", label="AL2")
            else:
                self.ax.scatter([xs[i] for i in idxs_a2], [ys[i] for i in idxs_a2], marker="x", label="AL2")

        if idxs_a1 or idxs_a2:
            self.ax.legend(loc="best")

        self.canvas.draw_idle()

    def clear_log(self):
        if not messagebox.askyesno("로그 삭제", "이 장치의 로그를 모두 삭제할까요?"):
            return
        if self.on_clear:
            self.on_clear()
        self.refresh()

    def export_log(self):
        logs = list(self.get_logs() or [])
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
                    err_str = f"0x{int(err_reg):04X}"
                    f.write(f"{ts},{val},{a1_str},{a2_str},{err_str}\n")
            messagebox.showinfo("로그 저장", "로그 파일이 저장되었습니다.")
        except Exception as e:
            messagebox.showerror("로그 저장", f"로그 저장 중 오류가 발생했습니다.\n{e}")
