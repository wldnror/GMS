from __future__ import annotations

import csv
import datetime as dt
import tkinter as tk
from tkinter import Frame, Label, Scrollbar, Text, filedialog, messagebox, ttk
from typing import Any, Iterable

import matplotlib

try:
    matplotlib.use("TkAgg")
except ImportError:
    # Headless imports (tests/CI) still need the module to load.
    matplotlib.use("Agg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from gms_core import parse_numeric


class LogViewer(tk.Toplevel):
    """Show Modbus and analog logs without assuming every field is an integer."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        box_index: int,
        ip: str,
        get_logs_callable,
        on_clear_callable=None,
        on_close_callable=None,
    ) -> None:
        super().__init__(master)
        self.box_index = box_index
        self.ip = ip
        self.get_logs = get_logs_callable
        self.on_clear = on_clear_callable
        self.on_close = on_close_callable
        self._alive = True
        self._close_notified = False
        self._after_id: str | None = None

        self.title(f"Box {box_index + 1} 로그")
        self.configure(bg="#1e1e1e")
        self.geometry("900x520")
        self.minsize(820, 460)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Destroy>", self._on_destroy, add="+")

        Label(
            self,
            text=f"장치 {box_index + 1} 로그 (IP: {ip})",
            fg="white",
            bg="#1e1e1e",
            font=("Helvetica", 12, "bold"),
        ).pack(padx=12, pady=(12, 6), anchor="w")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        self.tab_text = Frame(self, bg="#1e1e1e")
        self.nb.add(self.tab_text, text="텍스트")
        Label(
            self.tab_text,
            text="시간                 값                         AL1  AL2  상태/이벤트",
            fg="#aaaaaa",
            bg="#1e1e1e",
            font=("Consolas", 10),
        ).pack(padx=10, pady=(10, 2), anchor="w")

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

        self.tab_graph = Frame(self, bg="#1e1e1e")
        self.nb.add(self.tab_graph, text="그래프")
        top_bar = Frame(self.tab_graph, bg="#1e1e1e")
        top_bar.pack(fill="x", padx=10, pady=(10, 0))
        Label(top_bar, text="표시 개수:", fg="white", bg="#1e1e1e", font=("Helvetica", 10)).pack(
            side="left"
        )
        self.last_n_var = tk.StringVar(value="200")
        tk.Entry(top_bar, textvariable=self.last_n_var, width=6).pack(side="left", padx=(6, 10))
        tk.Button(top_bar, text="새로고침", command=self.refresh).pack(side="left", padx=5)
        tk.Button(top_bar, text="CSV 저장", command=self.export_log).pack(side="left", padx=5)
        tk.Button(top_bar, text="로그 삭제", command=self.clear_log).pack(side="left", padx=5)
        tk.Button(top_bar, text="닫기", command=self._on_close).pack(side="right", padx=5)

        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.tab_graph)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)

        self._auto_refresh_ms = 1000
        self.refresh()
        self._schedule_refresh()
        self.attributes("-topmost", True)
        self.transient(master)

    @staticmethod
    def _normalize_entry(entry: Any) -> tuple[str, Any, bool, bool, Any]:
        if isinstance(entry, dict):
            return (
                str(entry.get("timestamp", "")),
                entry.get("value"),
                bool(entry.get("alarm1", False)),
                bool(entry.get("alarm2", False)),
                entry.get("event", entry.get("error", "")),
            )
        try:
            ts, value, alarm1, alarm2, extra = entry
        except (TypeError, ValueError):
            return ("", entry, False, False, "")
        return str(ts), value, bool(alarm1), bool(alarm2), extra

    @staticmethod
    def _parse_timestamp(value: str) -> dt.datetime | None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return dt.datetime.strptime(value, fmt)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _format_extra(value: Any) -> str:
        if value is None or value == "":
            return ""
        if isinstance(value, bool):
            return "ON" if value else "OFF"
        if isinstance(value, int):
            return f"0x{value:04X}"
        return str(value)

    def _limited_logs(self) -> list[tuple[str, Any, bool, bool, Any]]:
        raw_logs: Iterable[Any] = self.get_logs() or []
        logs = [self._normalize_entry(item) for item in list(raw_logs)]
        try:
            count = max(1, min(10000, int(self.last_n_var.get().strip())))
        except (TypeError, ValueError):
            count = 200
        return logs[-count:]

    def _parse_logs(self):
        logs = self._limited_logs()
        graph_times: list[dt.datetime | int] = []
        graph_values: list[float] = []
        alarm1_points: list[tuple[dt.datetime | int, float]] = []
        alarm2_points: list[tuple[dt.datetime | int, float]] = []

        use_index = any(self._parse_timestamp(ts) is None for ts, *_rest in logs)
        for index, (timestamp, value, alarm1, alarm2, _extra) in enumerate(logs):
            number = parse_numeric(value)
            if number is None:
                continue
            x_value: dt.datetime | int = index if use_index else self._parse_timestamp(timestamp)  # type: ignore[assignment]
            graph_times.append(x_value)
            graph_values.append(number)
            if alarm1:
                alarm1_points.append((x_value, number))
            if alarm2:
                alarm2_points.append((x_value, number))
        return logs, graph_times, graph_values, alarm1_points, alarm2_points, use_index

    def refresh(self) -> None:
        if not self._alive or not self.winfo_exists():
            return
        logs, xs, ys, alarm1_points, alarm2_points, use_index = self._parse_logs()

        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        for timestamp, value, alarm1, alarm2, extra in logs:
            value_text = str(value)
            line = (
                f"{timestamp:<19}  {value_text:<27.27}  "
                f"{'ON' if alarm1 else 'OFF':>3}  {'ON' if alarm2 else 'OFF':>3}  "
                f"{self._format_extra(extra)}\n"
            )
            self.text.insert("end", line)
        self.text.see("end")
        self.text.config(state="disabled")

        self.ax.clear()
        self.ax.set_title("Value Trend")
        self.ax.set_xlabel("Index" if use_index else "Time")
        self.ax.set_ylabel("Value")
        if ys:
            self.ax.plot(xs, ys, label="Value")
            if alarm1_points:
                self.ax.scatter(
                    [item[0] for item in alarm1_points],
                    [item[1] for item in alarm1_points],
                    marker="o",
                    label="AL1",
                )
            if alarm2_points:
                self.ax.scatter(
                    [item[0] for item in alarm2_points],
                    [item[1] for item in alarm2_points],
                    marker="x",
                    label="AL2",
                )
            if alarm1_points or alarm2_points:
                self.ax.legend(loc="best")
            if not use_index:
                self.fig.autofmt_xdate()
        else:
            self.ax.text(0.5, 0.5, "표시할 숫자 로그가 없습니다.", ha="center", va="center", transform=self.ax.transAxes)
        self.canvas.draw_idle()

    def _schedule_refresh(self) -> None:
        if self._alive and self.winfo_exists():
            self._after_id = self.after(self._auto_refresh_ms, self._auto_refresh)

    def _auto_refresh(self) -> None:
        self._after_id = None
        if not self._alive:
            return
        try:
            self.refresh()
        except tk.TclError:
            self._alive = False
            return
        except Exception as exc:
            print(f"[LogViewer] refresh failed: {exc}")
        self._schedule_refresh()

    def clear_log(self) -> None:
        if not messagebox.askyesno("로그 삭제", "이 장치의 로그를 모두 삭제할까요?", parent=self):
            return
        if self.on_clear:
            self.on_clear()
        self.refresh()

    def export_log(self) -> None:
        logs = [self._normalize_entry(item) for item in list(self.get_logs() or [])]
        if not logs:
            messagebox.showinfo("로그 저장", "저장할 로그가 없습니다.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="로그 파일 저장",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(["시간", "값", "AL1", "AL2", "상태/이벤트"])
                for timestamp, value, alarm1, alarm2, extra in logs:
                    writer.writerow(
                        [
                            timestamp,
                            value,
                            "ON" if alarm1 else "OFF",
                            "ON" if alarm2 else "OFF",
                            self._format_extra(extra),
                        ]
                    )
            messagebox.showinfo("로그 저장", "로그 파일이 저장되었습니다.", parent=self)
        except Exception as exc:
            messagebox.showerror("로그 저장", f"로그 저장 중 오류가 발생했습니다.\n{exc}", parent=self)

    def _notify_closed(self) -> None:
        if self._close_notified:
            return
        self._close_notified = True
        if self.on_close:
            try:
                self.on_close()
            except Exception as exc:
                print(f"[LogViewer] close callback failed: {exc}")

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            self._alive = False
            self._notify_closed()

    def close(self) -> None:
        self._on_close()

    def _on_close(self) -> None:
        self._alive = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self._notify_closed()
        try:
            self.destroy()
        except tk.TclError:
            pass
