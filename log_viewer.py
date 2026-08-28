from __future__ import annotations

import csv
import datetime as dt
import tkinter as tk
from tkinter import Frame, Label, Scrollbar, Text, filedialog, messagebox, ttk
from typing import Any, Iterable

from gms_core import parse_numeric


class LogViewer(tk.Toplevel):
    """Show Modbus and analog logs without assuming every field is an integer."""

    MAX_VISIBLE_ENTRIES = 1000
    MAX_GRAPH_MARKERS_PER_LEVEL = 200

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
        self._graph_after_id: str | None = None
        self._last_signature: tuple[Any, ...] | None = None
        self._cached_logs: list[tuple[str, Any, bool, bool, Any]] = []
        self._graph_dirty = True

        self.title(f"Box {box_index + 1} 로그")
        self.configure(bg="#1e1e1e")
        screen_width = max(320, self.winfo_screenwidth())
        screen_height = max(240, self.winfo_screenheight())
        window_width = max(320, min(900, screen_width - 20))
        window_height = max(240, min(520, screen_height - 60))
        self.geometry(f"{window_width}x{window_height}")
        self.minsize(min(620, window_width), min(360, window_height))
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
        Label(
            top_bar, text="표시 개수:", fg="white", bg="#1e1e1e", font=("Helvetica", 10)
        ).pack(side="left")
        self.last_n_var = tk.StringVar(value="200")
        tk.Entry(top_bar, textvariable=self.last_n_var, width=6).pack(
            side="left", padx=(6, 10)
        )
        tk.Button(top_bar, text="새로고침", command=self.refresh).pack(
            side="left", padx=5
        )
        tk.Button(top_bar, text="CSV 저장", command=self.export_log).pack(
            side="left", padx=5
        )
        tk.Button(top_bar, text="로그 삭제", command=self.clear_log).pack(
            side="left", padx=5
        )
        tk.Button(top_bar, text="닫기", command=self._on_close).pack(
            side="right", padx=5
        )

        self.graph_canvas = tk.Canvas(
            self.tab_graph,
            bg="#121212",
            highlightthickness=0,
        )
        self.graph_canvas.pack(fill="both", expand=True, padx=10, pady=10)
        # Keep the old attribute name as a lightweight compatibility alias.
        self.canvas = self.graph_canvas
        self.graph_canvas.bind("<Configure>", self._on_graph_configure, add="+")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

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

    def _requested_count(self) -> int:
        try:
            return max(
                1,
                min(self.MAX_VISIBLE_ENTRIES, int(self.last_n_var.get().strip())),
            )
        except (TypeError, ValueError):
            return 200

    @classmethod
    def _limited_markers(
        cls, points: list[tuple[Any, float]]
    ) -> list[tuple[Any, float]]:
        limit = cls.MAX_GRAPH_MARKERS_PER_LEVEL
        if len(points) <= limit:
            return points
        stride = max(1, (len(points) + limit - 1) // limit)
        return points[::stride][:limit]

    def _limited_logs(self) -> list[tuple[str, Any, bool, bool, Any]]:
        raw_logs: Iterable[Any] = self.get_logs() or []
        count = self._requested_count()
        snapshot = list(raw_logs)
        return [self._normalize_entry(item) for item in snapshot[-count:]]

    @staticmethod
    def _signature_value(value: Any) -> tuple[str, str]:
        try:
            rendered = repr(value)
        except Exception:
            rendered = f"<unrepresentable:{id(value)}>"
        return type(value).__name__, rendered

    def _snapshot_with_signature(
        self,
    ) -> tuple[list[tuple[str, Any, bool, bool, Any]], tuple[Any, ...]]:
        raw_logs: Iterable[Any] = self.get_logs() or []
        snapshot = list(raw_logs)
        count = self._requested_count()
        logs = [self._normalize_entry(item) for item in snapshot[-count:]]
        signature = (
            count,
            len(snapshot),
            tuple(
                (
                    timestamp,
                    self._signature_value(value),
                    alarm1,
                    alarm2,
                    self._signature_value(extra),
                )
                for timestamp, value, alarm1, alarm2, extra in logs
            ),
        )
        return logs, signature

    def _parse_logs(self, logs=None):
        if logs is None:
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
            x_value: dt.datetime | int = (
                index if use_index else self._parse_timestamp(timestamp)
            )  # type: ignore[assignment]
            graph_times.append(x_value)
            graph_values.append(number)
            if alarm1:
                alarm1_points.append((x_value, number))
            if alarm2:
                alarm2_points.append((x_value, number))
        return logs, graph_times, graph_values, alarm1_points, alarm2_points, use_index

    def _graph_tab_is_visible(self) -> bool:
        try:
            return self.nb.select() == str(self.tab_graph)
        except tk.TclError:
            return False

    def _update_text(self, logs: list[tuple[str, Any, bool, bool, Any]]) -> None:
        lines = []
        for timestamp, value, alarm1, alarm2, extra in logs:
            value_text = str(value)
            lines.append(
                f"{timestamp:<19}  {value_text:<27.27}  "
                f"{'ON' if alarm1 else 'OFF':>3}  {'ON' if alarm2 else 'OFF':>3}  "
                f"{self._format_extra(extra)}\n"
            )
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        if lines:
            self.text.insert("end", "".join(lines))
            self.text.see("end")
        self.text.config(state="disabled")

    def refresh(self) -> None:
        if not self._alive or not self.winfo_exists():
            return
        logs, signature = self._snapshot_with_signature()
        if signature != self._last_signature:
            self._last_signature = signature
            self._cached_logs = logs
            self._update_text(logs)
            self._graph_dirty = True
        if self._graph_dirty and self._graph_tab_is_visible():
            self._cancel_graph_draw()
            self._draw_graph()

    def _on_tab_changed(self, _event=None) -> None:
        if self._alive and self._graph_dirty and self._graph_tab_is_visible():
            self._schedule_graph_draw(0)

    def _on_graph_configure(self, event: tk.Event) -> None:
        if not self._alive:
            return
        if getattr(event, "width", 0) < 2 or getattr(event, "height", 0) < 2:
            return
        self._graph_dirty = True
        if self._graph_tab_is_visible():
            self._schedule_graph_draw(80)

    def _schedule_graph_draw(self, delay_ms: int) -> None:
        self._cancel_graph_draw()
        try:
            self._graph_after_id = self.after(delay_ms, self._draw_graph)
        except tk.TclError:
            self._graph_after_id = None

    def _cancel_graph_draw(self) -> None:
        if self._graph_after_id is not None:
            try:
                self.after_cancel(self._graph_after_id)
            except tk.TclError:
                pass
            self._graph_after_id = None

    @staticmethod
    def _graph_x_number(value: dt.datetime | int) -> float:
        if isinstance(value, dt.datetime):
            return value.timestamp()
        return float(value)

    @staticmethod
    def _format_x_label(value: dt.datetime | int, use_index: bool) -> str:
        if use_index or not isinstance(value, dt.datetime):
            return str(value)
        return value.strftime("%m-%d %H:%M")

    def _draw_graph(self) -> None:
        self._graph_after_id = None
        if not self._alive or not self._graph_dirty or not self._graph_tab_is_visible():
            return
        canvas = self.graph_canvas
        try:
            width = max(2, canvas.winfo_width())
            height = max(2, canvas.winfo_height())
        except tk.TclError:
            return
        if width < 120 or height < 100:
            self._schedule_graph_draw(80)
            return

        _logs, xs, ys, alarm1_points, alarm2_points, use_index = self._parse_logs(
            self._cached_logs
        )
        canvas.delete("all")
        axis_color = "#8a8a8a"
        grid_color = "#303030"
        text_color = "#e8e8e8"
        left, right, top, bottom = 62, 18, 32, 46
        plot_left = left
        plot_right = max(left + 1, width - right)
        plot_top = top
        plot_bottom = max(top + 1, height - bottom)

        canvas.create_text(
            width / 2,
            15,
            text="Value Trend",
            fill=text_color,
            font=("Helvetica", 11, "bold"),
        )
        canvas.create_line(plot_left, plot_top, plot_left, plot_bottom, fill=axis_color)
        canvas.create_line(
            plot_left, plot_bottom, plot_right, plot_bottom, fill=axis_color
        )

        if not ys:
            canvas.create_text(
                (plot_left + plot_right) / 2,
                (plot_top + plot_bottom) / 2,
                text="표시할 숫자 로그가 없습니다.",
                fill=text_color,
                font=("Helvetica", 11),
            )
            self._graph_dirty = False
            return

        x_numbers = [self._graph_x_number(value) for value in xs]
        x_min, x_max = min(x_numbers), max(x_numbers)
        y_min, y_max = min(ys), max(ys)
        if x_min == x_max:
            x_min -= 0.5
            x_max += 0.5
        if y_min == y_max:
            padding = max(1.0, abs(y_min) * 0.05)
            y_min -= padding
            y_max += padding
        else:
            padding = (y_max - y_min) * 0.08
            y_min -= padding
            y_max += padding

        def map_point(
            x_value: dt.datetime | int, y_value: float
        ) -> tuple[float, float]:
            x_number = self._graph_x_number(x_value)
            px = plot_left + (x_number - x_min) / (x_max - x_min) * (
                plot_right - plot_left
            )
            py = plot_bottom - (float(y_value) - y_min) / (y_max - y_min) * (
                plot_bottom - plot_top
            )
            return px, py

        for tick in range(5):
            ratio = tick / 4
            py = plot_bottom - ratio * (plot_bottom - plot_top)
            value = y_min + ratio * (y_max - y_min)
            canvas.create_line(plot_left, py, plot_right, py, fill=grid_color)
            canvas.create_text(
                plot_left - 7,
                py,
                text=f"{value:.1f}",
                fill=text_color,
                anchor="e",
                font=("Helvetica", 8),
            )

        points: list[float] = []
        for x_value, y_value in zip(xs, ys):
            px, py = map_point(x_value, y_value)
            points.extend((px, py))
        if len(points) >= 4:
            canvas.create_line(*points, fill="#4da3ff", width=2)
        elif points:
            px, py = points
            canvas.create_oval(
                px - 2, py - 2, px + 2, py + 2, fill="#4da3ff", outline=""
            )

        for x_value, y_value in self._limited_markers(alarm1_points):
            px, py = map_point(x_value, y_value)
            canvas.create_oval(
                px - 3, py - 3, px + 3, py + 3, outline="#ffcc00", width=2
            )
        for x_value, y_value in self._limited_markers(alarm2_points):
            px, py = map_point(x_value, y_value)
            canvas.create_line(px - 4, py - 4, px + 4, py + 4, fill="#ff4d4d", width=2)
            canvas.create_line(px - 4, py + 4, px + 4, py - 4, fill="#ff4d4d", width=2)

        canvas.create_text(
            plot_left,
            plot_bottom + 17,
            text=self._format_x_label(xs[0], use_index),
            fill=text_color,
            anchor="w",
            font=("Helvetica", 8),
        )
        canvas.create_text(
            plot_right,
            plot_bottom + 17,
            text=self._format_x_label(xs[-1], use_index),
            fill=text_color,
            anchor="e",
            font=("Helvetica", 8),
        )
        canvas.create_text(
            (plot_left + plot_right) / 2,
            height - 10,
            text="Index" if use_index else "Time",
            fill=text_color,
            font=("Helvetica", 9),
        )
        canvas.create_text(5, plot_top - 10, text="Value", fill=text_color, anchor="w")
        if alarm1_points or alarm2_points:
            legend = []
            if alarm1_points:
                legend.append("AL1 ○")
            if alarm2_points:
                legend.append("AL2 ×")
            canvas.create_text(
                plot_right - 5,
                plot_top + 4,
                text="   ".join(legend),
                fill=text_color,
                anchor="ne",
                font=("Helvetica", 8, "bold"),
            )
        self._graph_dirty = False

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
        if not messagebox.askyesno(
            "로그 삭제", "이 장치의 로그를 모두 삭제할까요?", parent=self
        ):
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
            messagebox.showerror(
                "로그 저장", f"로그 저장 중 오류가 발생했습니다.\n{exc}", parent=self
            )

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
        for attribute in ("_after_id", "_graph_after_id"):
            after_id = getattr(self, attribute)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attribute, None)
        self._notify_closed()
        try:
            self.destroy()
        except tk.TclError:
            pass
