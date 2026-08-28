from __future__ import annotations

import queue
import threading
from tkinter import Button, Canvas, Frame
from typing import Any

from core_utils import battery_percentage
from gms_core import ups_fault_active
from ui_config import UI_SCALE

try:
    import board
    from adafruit_ina219 import INA219
    from busio import I2C
except (ImportError, RuntimeError):
    board = None
    INA219 = None
    I2C = None

BATTERY_ADJUSTMENT = 0
BATTERY_CELLS = 6
SCALE_FACTOR = UI_SCALE


class UPSMonitorUI:
    """INA219 UPS display with all Tk updates kept on the main thread."""

    SENSOR_RETRY_SEC = 10.0
    SENSOR_POLL_SEC = 1.0

    LOW_BATTERY_PERCENT = 20
    CLEAR_BATTERY_PERCENT = 25

    def __init__(self, parent: Any, num_boxes: int, alarm_callback=None) -> None:
        self.parent = parent
        self.alarm_callback = alarm_callback
        self.box_frames: list[Frame] = []
        self.box_data: list[dict[str, Any]] = []
        self.ina219_available = False
        self.last_battery_level = 0
        self.last_voltage = 0.0
        self.last_error: str | None = "센서 초기화 중"
        self._stop_event = threading.Event()
        self._updates: queue.Queue[tuple[int, float, str | None]] = queue.Queue(
            maxsize=4
        )
        self._after_id: str | None = None
        self._sensor_lock = threading.RLock()
        self.ina219 = None
        self.i2c = None
        self.update_thread: threading.Thread | None = None
        self._fault_active = False
        self._sensor_fault_active = True
        self._sensor_healthy_samples = 0
        self._battery_fault_active = False

        for index in range(max(0, int(num_boxes))):
            self.create_ups_box(index)

        for index, data in enumerate(self.box_data):
            self.update_battery_status(index, 0, data["mode"], 0.0, self.last_error)
        if self.box_data:
            self._schedule_updates()
            self.update_thread = threading.Thread(
                target=self.update_loop,
                name="gms-ups-reader",
                daemon=True,
            )
            self.update_thread.start()

    def create_ups_box(self, index: int) -> None:
        box_frame = Frame(self.parent, highlightthickness=int(7 * SCALE_FACTOR))
        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=0, pady=0)

        box_canvas = Canvas(
            inner_frame,
            width=int(150 * SCALE_FACTOR),
            height=int(300 * SCALE_FACTOR),
            highlightthickness=int(3 * SCALE_FACTOR),
            highlightbackground="#000000",
            highlightcolor="#000000",
            bg="#4B4B4B",
        )
        box_canvas.pack()
        box_canvas.create_rectangle(
            0,
            0,
            int(160 * SCALE_FACTOR),
            int(200 * SCALE_FACTOR),
            fill="#4B4B4B",
            outline="black",
            tags="border",
        )
        box_canvas.create_rectangle(
            0,
            int(200 * SCALE_FACTOR),
            int(160 * SCALE_FACTOR),
            int(310 * SCALE_FACTOR),
            fill="black",
            outline="black",
            tags="border",
        )

        box_canvas.create_rectangle(
            int(15 * SCALE_FACTOR),
            int(20 * SCALE_FACTOR),
            int(135 * SCALE_FACTOR),
            int(60 * SCALE_FACTOR),
            fill="#4B4B4B",
            outline="black",
            width=int(3 * SCALE_FACTOR),
        )
        box_canvas.create_rectangle(
            int(135 * SCALE_FACTOR),
            int(30 * SCALE_FACTOR),
            int(145 * SCALE_FACTOR),
            int(50 * SCALE_FACTOR),
            fill="#4B4B4B",
            outline="black",
            width=int(2 * SCALE_FACTOR),
        )
        battery_level_bar = box_canvas.create_rectangle(
            int(20 * SCALE_FACTOR),
            int(25 * SCALE_FACTOR),
            int(20 * SCALE_FACTOR),
            int(55 * SCALE_FACTOR),
            fill="#00AA00",
            outline="",
        )
        battery_percentage_text = box_canvas.create_text(
            int(75 * SCALE_FACTOR),
            int(40 * SCALE_FACTOR),
            text="0%",
            font=("Helvetica", int(12 * SCALE_FACTOR), "bold"),
            fill="#FFFFFF",
            anchor="center",
        )
        voltage_text_id = box_canvas.create_text(
            int(75 * SCALE_FACTOR),
            int(80 * SCALE_FACTOR),
            text="0.00V",
            font=("Helvetica", int(12 * SCALE_FACTOR), "bold"),
            fill="#FFFFFF",
            anchor="center",
        )
        mode_text_id = box_canvas.create_text(
            int(75 * SCALE_FACTOR),
            int(105 * SCALE_FACTOR),
            text="상시 모드",
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#00FF00",
            anchor="center",
        )
        status_text_id = box_canvas.create_text(
            int(75 * SCALE_FACTOR),
            int(125 * SCALE_FACTOR),
            text="",
            font=("Helvetica", int(8 * SCALE_FACTOR)),
            fill="#FFD966",
            anchor="center",
        )
        toggle_button = Button(
            box_canvas,
            text="표시 모드 전환",
            command=lambda idx=index: self.toggle_mode(idx),
        )
        box_canvas.create_window(
            int(75 * SCALE_FACTOR), int(155 * SCALE_FACTOR), window=toggle_button
        )

        box_canvas.create_text(
            int(75 * SCALE_FACTOR),
            int(270 * SCALE_FACTOR),
            text="UPS Monitor",
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#FFFFFF",
            anchor="center",
        )
        box_canvas.create_text(
            int(75 * SCALE_FACTOR),
            int(295 * SCALE_FACTOR),
            text="GDS ENGINEERING CO.,LTD",
            font=("Helvetica", int(7 * SCALE_FACTOR), "bold"),
            fill="#999999",
            anchor="center",
        )

        self.box_frames.append(box_frame)
        self.box_data.append(
            {
                "box_canvas": box_canvas,
                "battery_level_bar": battery_level_bar,
                "battery_percentage_text": battery_percentage_text,
                "mode_text_id": mode_text_id,
                "status_text_id": status_text_id,
                "mode": "상시 모드",
                "voltage_text_id": voltage_text_id,
            }
        )
        # Geometry is intentionally controlled by main.py (grid); do not pack here.

    def update_battery_status(
        self,
        index: int,
        battery_level: int,
        mode: str,
        voltage: float = 0.0,
        error: str | None = None,
    ) -> None:
        if not 0 <= index < len(self.box_data):
            return
        adjusted = min(max(int(battery_level) + BATTERY_ADJUSTMENT, 0), 100)
        data = self.box_data[index]
        canvas: Canvas = data["box_canvas"]

        if self.ina219_available and error is None:
            width = int(110 * SCALE_FACTOR * (adjusted / 100.0))
            canvas.coords(
                data["battery_level_bar"],
                int(20 * SCALE_FACTOR),
                int(25 * SCALE_FACTOR),
                int(20 * SCALE_FACTOR) + width,
                int(55 * SCALE_FACTOR),
            )
            canvas.itemconfig(data["battery_percentage_text"], text=f"{adjusted}%")
            canvas.itemconfig(data["voltage_text_id"], text=f"{voltage:.2f}V")
            low_battery = (
                adjusted < self.CLEAR_BATTERY_PERCENT
                if self._battery_fault_active
                else adjusted <= self.LOW_BATTERY_PERCENT
            )
            warning = "배터리 부족" if low_battery else ""
            canvas.itemconfig(data["status_text_id"], text=warning)
        else:
            canvas.coords(
                data["battery_level_bar"],
                int(20 * SCALE_FACTOR),
                int(25 * SCALE_FACTOR),
                int(20 * SCALE_FACTOR),
                int(55 * SCALE_FACTOR),
            )
            canvas.itemconfig(data["battery_percentage_text"], text="연결되지 않음")
            canvas.itemconfig(data["voltage_text_id"], text="N/A")
            canvas.itemconfig(
                data["status_text_id"], text=(error or "INA219 없음")[:28]
            )

        if mode == "상시 모드":
            canvas.itemconfig(data["mode_text_id"], text=mode, fill="#00AA00")
        else:
            canvas.itemconfig(data["mode_text_id"], text="배터리 모드", fill="#AA0000")

    def toggle_mode(self, index: int) -> None:
        if not 0 <= index < len(self.box_data):
            return
        data = self.box_data[index]
        data["mode"] = "배터리 모드" if data["mode"] == "상시 모드" else "상시 모드"
        self.update_battery_status(
            index,
            self.last_battery_level,
            data["mode"],
            self.last_voltage,
            self.last_error,
        )

    @staticmethod
    def calculate_battery_percentage(voltage: float) -> int:
        return battery_percentage(voltage, cell_count=BATTERY_CELLS)

    def _publish(self, level: int, voltage: float, error: str | None) -> None:
        item = (level, voltage, error)
        try:
            self._updates.put_nowait(item)
        except queue.Full:
            try:
                self._updates.get_nowait()
            except queue.Empty:
                pass
            try:
                self._updates.put_nowait(item)
            except queue.Full:
                pass

    @staticmethod
    def _deinit_i2c(i2c: object | None) -> None:
        if i2c is None:
            return
        deinit = getattr(i2c, "deinit", None)
        if callable(deinit):
            try:
                deinit()
            except Exception as exc:
                print(f"UPS I2C 종료 오류: {exc}")

    def _release_sensor(self) -> None:
        with self._sensor_lock:
            i2c = self.i2c
            self.i2c = None
            self.ina219 = None
            self.ina219_available = False
        self._deinit_i2c(i2c)

    def _initialize_sensor(self) -> None:
        if INA219 is None or I2C is None or board is None:
            raise RuntimeError("INA219 라이브러리 또는 I2C를 사용할 수 없습니다.")

        i2c = None
        try:
            i2c = I2C(board.SCL, board.SDA)
            sensor = INA219(i2c)
            # Validate the device before publishing it to the reader loop.
            _ = float(sensor.bus_voltage)
        except Exception:
            self._deinit_i2c(i2c)
            raise

        if self._stop_event.is_set():
            self._deinit_i2c(i2c)
            return
        with self._sensor_lock:
            self.i2c = i2c
            self.ina219 = sensor
            self.ina219_available = True
        print("INA219 센서가 성공적으로 초기화되었습니다.")

    def update_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                with self._sensor_lock:
                    sensor = self.ina219
                if sensor is None:
                    try:
                        self._initialize_sensor()
                    except Exception as exc:
                        error = f"INA219 초기화 실패: {exc}"
                        self.last_battery_level = 0
                        self.last_voltage = 0.0
                        self.last_error = error
                        self._publish(0, 0.0, error)
                        if self._stop_event.wait(self.SENSOR_RETRY_SEC):
                            break
                        continue
                    with self._sensor_lock:
                        sensor = self.ina219
                    if sensor is None:
                        break

                try:
                    bus_voltage = float(sensor.bus_voltage)
                    shunt_voltage = float(sensor.shunt_voltage)
                    voltage = bus_voltage + (shunt_voltage / 1000.0)
                    level = self.calculate_battery_percentage(voltage)
                    error = None
                    self.ina219_available = True
                except Exception as exc:
                    error = f"INA219 읽기 실패: {exc}"
                    voltage = 0.0
                    level = 0
                    self._release_sensor()

                self.last_battery_level = level
                self.last_voltage = voltage
                self.last_error = error
                self._publish(level, voltage, error)
                wait_time = (
                    self.SENSOR_RETRY_SEC if error is not None else self.SENSOR_POLL_SEC
                )
                if self._stop_event.wait(wait_time):
                    break
        finally:
            self._release_sensor()

    def _schedule_updates(self) -> None:
        try:
            if not self._stop_event.is_set() and self.parent.winfo_exists():
                self._after_id = self.parent.after(200, self._drain_updates)
        except Exception:
            self._after_id = None

    def _drain_updates(self) -> None:
        self._after_id = None
        if self._stop_event.is_set():
            return
        try:
            latest = None
            while True:
                try:
                    latest = self._updates.get_nowait()
                except queue.Empty:
                    break
            if latest is not None:
                level, voltage, error = latest
                self.last_battery_level = level
                self.last_voltage = voltage
                self.last_error = error
                for index, data in enumerate(self.box_data):
                    self.update_battery_status(
                        index, level, data["mode"], voltage, error
                    )
                self._update_fault_state(level, error)
        except Exception as exc:
            print(f"UPS UI 갱신 오류: {exc}")
        finally:
            self._schedule_updates()

    def _update_fault_state(self, level: int, error: str | None) -> None:
        if error is not None:
            self._sensor_healthy_samples = 0
            self._sensor_fault_active = True
        else:
            self._sensor_healthy_samples += 1
            if self._sensor_healthy_samples >= 2:
                self._sensor_fault_active = False

        if error is None:
            self._battery_fault_active = ups_fault_active(
                level,
                sensor_error=False,
                was_fault=self._battery_fault_active,
                low_percent=self.LOW_BATTERY_PERCENT,
                clear_percent=self.CLEAR_BATTERY_PERCENT,
            )
        fault = self._sensor_fault_active or self._battery_fault_active
        if fault == self._fault_active:
            return
        self._fault_active = fault
        if not callable(self.alarm_callback):
            return
        for index in range(len(self.box_data)):
            try:
                self.alarm_callback(False, f"ups_{index}", fault)
            except TypeError:
                self.alarm_callback(fault, f"ups_{index}")

    def stop(self) -> None:
        self._stop_event.set()
        if self._after_id is not None:
            try:
                self.parent.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        thread = self.update_thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=2.0)
        if thread is None or not thread.is_alive():
            self._release_sensor()
