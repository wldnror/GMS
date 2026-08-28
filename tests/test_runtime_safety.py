from __future__ import annotations

import importlib.util
import os
import queue
import tempfile
import threading
import unittest
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HAS_RUNTIME_DEPS = all(
    importlib.util.find_spec(name) is not None
    for name in ("_tkinter", "cryptography", "PIL", "psutil", "pymodbus")
)


class _Value:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


@unittest.skipUnless(HAS_RUNTIME_DEPS, "portable runtime dependencies are missing")
class RuntimeSafetyTest(unittest.TestCase):
    def test_audio_configuration_failure_cannot_suppress_alarm_path(self):
        import main

        music = SimpleNamespace(load=mock.Mock(), play=mock.Mock())
        fake_pygame = SimpleNamespace(mixer=SimpleNamespace(music=music))
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(main, "pygame", fake_pygame))
            stack.enter_context(mock.patch.object(main, "audio_available", True))
            stack.enter_context(mock.patch.object(main, "audio_playing", False))
            stack.enter_context(mock.patch.object(main, "system_faults", set()))
            stack.enter_context(
                mock.patch.object(
                    main.settings_ui,
                    "load_settings",
                    side_effect=RuntimeError("damaged settings"),
                )
            )
            main.play_alarm_sound()
            music.load.assert_not_called()
            music.play.assert_not_called()

    def test_audio_is_decoded_at_startup_and_retried_when_invalid(self):
        import main

        music = SimpleNamespace(load=mock.Mock(side_effect=ValueError("bad audio")))
        mixer = SimpleNamespace(init=mock.Mock(), music=music)
        fake_pygame = SimpleNamespace(mixer=mixer)
        fake_root = SimpleNamespace(
            after=mock.Mock(return_value="audio-retry"),
            after_cancel=mock.Mock(),
        )
        report = mock.Mock()
        with (
            tempfile.NamedTemporaryFile(suffix=".mp3") as audio_file,
            ExitStack() as stack,
        ):
            stack.enter_context(mock.patch.object(main, "pygame", fake_pygame))
            stack.enter_context(mock.patch.object(main, "root", fake_root))
            stack.enter_context(mock.patch.object(main, "closing", False))
            stack.enter_context(mock.patch.object(main, "audio_available", False))
            stack.enter_context(mock.patch.object(main, "audio_retry_after_id", None))
            stack.enter_context(mock.patch.object(main, "report_system_fault", report))

            main.setup_audio(audio_file.name)

            mixer.init.assert_called_once_with()
            music.load.assert_called_once_with(audio_file.name)
            report.assert_called_with("audio", True)
            fake_root.after.assert_called_once_with(10000, main.setup_audio)

    def test_stopped_alarm_loop_is_reported_and_retried(self):
        import main

        music = SimpleNamespace(get_busy=mock.Mock(return_value=False))
        fake_pygame = SimpleNamespace(mixer=SimpleNamespace(music=music))
        report = mock.Mock()
        schedule = mock.Mock()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(main, "pygame", fake_pygame))
            stack.enter_context(mock.patch.object(main, "audio_available", True))
            stack.enter_context(mock.patch.object(main, "audio_playing", True))
            stack.enter_context(mock.patch.object(main, "report_system_fault", report))
            stack.enter_context(
                mock.patch.object(main, "_schedule_audio_retry", schedule)
            )

            main.check_alarm_audio_health()

            self.assertFalse(main.audio_playing)
            report.assert_called_once_with("audio", True)
            schedule.assert_called_once_with()

    def test_gpio_output_failure_becomes_a_system_fault(self):
        import main

        fake_gpio = SimpleNamespace(
            HIGH=1,
            LOW=0,
            output=mock.Mock(side_effect=RuntimeError("GPIO failed")),
        )
        report = mock.Mock()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(main, "GPIO", fake_gpio))
            stack.enter_context(mock.patch.object(main, "gpio_available", True))
            stack.enter_context(mock.patch.object(main, "report_system_fault", report))
            main.gpio_write(20, True)
            self.assertFalse(main.gpio_available)
            report.assert_called_once_with("gpio", True)

    def test_ups_sensor_fault_needs_two_healthy_reads_to_clear(self):
        from ups_monitor_ui import UPSMonitorUI

        calls: list[tuple[bool, str, bool]] = []
        ui = UPSMonitorUI.__new__(UPSMonitorUI)
        ui.box_data = [{}]
        ui.alarm_callback = lambda active, box_id, fut: calls.append(
            (active, box_id, fut)
        )
        ui._fault_active = False
        ui._sensor_fault_active = True
        ui._sensor_healthy_samples = 0
        ui._battery_fault_active = False

        ui._update_fault_state(80, "I2C error")
        ui._update_fault_state(80, None)
        self.assertTrue(ui._fault_active)
        ui._update_fault_state(80, None)
        self.assertFalse(ui._fault_active)
        self.assertEqual(calls, [(False, "ups_0", True), (False, "ups_0", False)])

    def test_ups_sensor_error_does_not_create_a_fake_low_battery_latch(self):
        from ups_monitor_ui import UPSMonitorUI

        ui = UPSMonitorUI.__new__(UPSMonitorUI)
        ui.box_data = [{}]
        ui.alarm_callback = mock.Mock()
        ui._fault_active = False
        ui._sensor_fault_active = False
        ui._sensor_healthy_samples = 2
        ui._battery_fault_active = False

        ui._update_fault_state(0, "I2C error")
        self.assertFalse(ui._battery_fault_active)
        ui._update_fault_state(22, None)
        ui._update_fault_state(22, None)
        self.assertFalse(ui._battery_fault_active)
        self.assertFalse(ui._fault_active)

    def test_analog_fault_keeps_alarm_until_two_healthy_samples(self):
        from analog_ui import AnalogUI

        ui = AnalogUI.__new__(AnalogUI)
        ui.num_boxes = 1
        ui.gas_types = {"analog_box_0": _Value("ORG")}
        ui.box_states = [
            {
                "fault": True,
                "fault_reason": "ADC unavailable",
                "healthy_samples": 0,
                "alarm1_on": True,
                "alarm2_on": False,
                "pwr_on": True,
            }
        ]
        ui._render_box = mock.Mock()
        ui.maybe_log_event = mock.Mock()

        ui._apply_sample(0, 4.0)
        self.assertTrue(ui.box_states[0]["fault"])
        self.assertTrue(ui.box_states[0]["alarm1_on"])

        ui._apply_sample(0, 4.0)
        self.assertFalse(ui.box_states[0]["fault"])
        self.assertFalse(ui.box_states[0]["alarm1_on"])

        ui.box_states[0]["alarm1_on"] = True
        ui._apply_box_fault(0, "ADC read failed")
        self.assertTrue(ui.box_states[0]["alarm1_on"])
        self.assertTrue(ui.box_states[0]["fault"])

        ui.box_states[0].update(
            {
                "fault": False,
                "healthy_samples": 2,
                "alarm1_on": True,
                "alarm2_on": False,
                "pwr_on": True,
            }
        )
        ui._apply_sample(0, 15.0, signal_milliamp=0.0)
        self.assertTrue(ui.box_states[0]["fault"])
        self.assertTrue(ui.box_states[0]["alarm1_on"])

    def test_analog_samples_coalesce_without_evicting_adc_faults(self):
        from analog_ui import AnalogUI

        ui = AnalogUI.__new__(AnalogUI)
        ui.sample_queue = queue.Queue(maxsize=2)
        ui._sample_lock = threading.Lock()
        ui._pending_adc_samples = {}

        for value in range(100):
            ui._put_sample(("sample", 0, float(value)))
        ui._put_sample(("error", 0, "ADC 0 failed"))
        ui._put_sample(("error", 1, "ADC 1 failed"))
        ui._put_sample(("sample", 1, 12.0))

        self.assertEqual(ui._pending_adc_samples, {0: 99.0, 1: 12.0})
        self.assertEqual(
            list(ui.sample_queue.queue),
            [
                ("error", 0, "ADC 0 failed"),
                ("error", 1, "ADC 1 failed"),
            ],
        )

    def test_analog_log_pressure_evicts_only_routine_values(self):
        from analog_ui import AnalogUI

        ui = AnalogUI.__new__(AnalogUI)
        ui.LOG_QUEUE_MAX = 2
        ui.log_queue = deque()
        ui._log_condition = threading.Condition()
        ui.log_dropped_count = 0
        ui.log_dropped_critical_count = 0
        ui._logging_fault_active = False
        routine1 = ("row", 0, ["t1", "VALUE_CHANGED"])
        routine2 = ("row", 0, ["t2", "VALUE_CHANGED"])
        critical = ("row", 0, ["t3", "AL1_ON"])

        self.assertTrue(ui._enqueue_log(routine1, critical=False))
        self.assertTrue(ui._enqueue_log(routine2, critical=False))
        self.assertFalse(ui._enqueue_log(routine1, critical=False))
        self.assertTrue(ui._enqueue_log(critical, critical=True))
        self.assertEqual(list(ui.log_queue), [routine2, critical])
        self.assertEqual(ui.log_dropped_count, 2)
        self.assertEqual(ui.log_dropped_critical_count, 0)

    def test_analog_critical_log_backlog_is_bounded_and_sets_fut(self):
        from analog_ui import AnalogUI

        calls: list[tuple[bool, str, bool]] = []
        ui = AnalogUI.__new__(AnalogUI)
        ui.LOG_QUEUE_MAX = 2
        ui.log_queue = deque()
        ui._log_condition = threading.Condition()
        ui.log_dropped_count = 0
        ui.log_dropped_critical_count = 0
        ui._logging_fault_active = False
        ui.alarm_callback = lambda active, box_id, fut: calls.append(
            (active, box_id, fut)
        )
        box0_on = ("row", 0, ["t1", "AL1_ON"])
        box1_fault = ("row", 1, ["t2", "FUT_ON"])
        box0_off = ("row", 0, ["t3", "AL1_OFF"])

        self.assertTrue(ui._enqueue_log(box0_on, critical=True))
        self.assertTrue(ui._enqueue_log(box1_fault, critical=True))
        self.assertTrue(ui._enqueue_log(box0_off, critical=True))

        self.assertEqual(len(ui.log_queue), 2)
        self.assertEqual(list(ui.log_queue), [box1_fault, box0_off])
        self.assertEqual(ui.log_dropped_count, 1)
        self.assertEqual(ui.log_dropped_critical_count, 1)
        self.assertEqual(calls, [(False, "analog_logging", True)])

    def test_tracked_git_worker_and_mutation_gate(self):
        import utils

        started = threading.Event()
        release = threading.Event()

        def worker() -> None:
            started.set()
            release.wait(3)

        thread = utils.start_git_worker(worker, name="test-git-worker")
        self.assertTrue(started.wait(1))
        self.assertTrue(utils.git_operations_in_progress())
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(utils.git_operations_in_progress())

        self.assertTrue(utils.begin_git_mutation())
        try:
            self.assertFalse(utils.begin_git_mutation())
            self.assertTrue(utils.git_operations_in_progress())
        finally:
            utils.end_git_mutation()
        self.assertFalse(utils.git_operations_in_progress())

    def test_manual_modbus_disconnect_invalidates_pending_commands(self):
        from modbus_ui import ModbusUI

        stop_flag = threading.Event()
        ui = ModbusUI.__new__(ModbusUI)
        ui.num_boxes = 1
        ui._objects_lock = threading.RLock()
        ui.stop_flags = {0: stop_flag}
        ui._connection_generation = [7]
        ui._sample_accepting = [True]
        ui._put_ui = mock.Mock()

        ui.disconnect(0, manual=True)

        self.assertTrue(stop_flag.is_set())
        self.assertEqual(ui._connection_generation, [8])
        self.assertEqual(ui._sample_accepting, [False])
        ui._put_ui.assert_called_once_with("manual_disconnect", 0, None, 8)

    def test_modbus_sample_queue_preserves_safety_transitions(self):
        from modbus_ui import ModbusUI

        ui = ModbusUI.__new__(ModbusUI)
        ui.num_boxes = 1
        ui._stop_event = threading.Event()
        ui._connection_generation = [3]
        ui._sample_accepting = [True]
        ui.ui_queue = queue.Queue()
        ui._sample_lock = threading.Lock()
        ui._pending_samples = {}
        ui._last_enqueued_safety = {}
        healthy = {"value": 1, "alarm1": False, "alarm2": False, "error_reg": 0}
        latest_healthy = dict(healthy, value=2)
        alarm = dict(latest_healthy, alarm1=True)

        ui._put_ui("sample", 0, healthy, 3)
        ui._put_ui("sample", 0, latest_healthy, 3)
        self.assertEqual(ui._pending_samples[(0, 3)], latest_healthy)
        ui._put_ui("sample", 0, alarm, 3)

        queued = list(ui.ui_queue.queue)
        self.assertEqual([item[2] for item in queued], [healthy, alarm])
        self.assertEqual(ui._pending_samples, {})
        ui._put_ui("sample", 0, dict(alarm, value=99), 2)
        self.assertEqual(len(ui.ui_queue.queue), 2)

    def test_firmware_staging_is_hashed_atomic_and_private_source_only(self):
        import modbus_ui
        from modbus_ui import ModbusUI

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "release.bin"
            source.write_bytes(b"A" * 1024)
            info = ModbusUI._firmware_file_info(str(source))
            ui = ModbusUI.__new__(ModbusUI)
            ui._firmware_stage_lock = threading.Lock()
            ui._staged_fw_digest = None

            with mock.patch.object(modbus_ui, "TFTP_ROOT_DIR", root / "tftp"):
                destination = ui._stage_firmware(str(source), info["digest"])
                second_destination = (
                    root / "tftp" / "GDS" / "ASGD-3210" / "asgd3210.bin"
                )
                self.assertEqual(destination.read_bytes(), source.read_bytes())
                self.assertEqual(second_destination.read_bytes(), source.read_bytes())
                self.assertEqual(os.stat(destination).st_mode & 0o777, 0o644)
                self.assertEqual(os.stat(second_destination).st_mode & 0o777, 0o644)

                source.write_bytes(b"B" * 1024)
                destination.unlink()
                with self.assertRaises(RuntimeError):
                    ui._stage_firmware(str(source), info["digest"])

            link = root / "linked.bin"
            link.symlink_to(source)
            with self.assertRaises(ValueError):
                ModbusUI._firmware_file_info(str(link))

    def test_firmware_staging_rejects_symlinked_tftp_root(self):
        import modbus_ui
        from modbus_ui import ModbusUI

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "release.bin"
            source.write_bytes(b"A" * 1024)
            info = ModbusUI._firmware_file_info(str(source))
            real_tftp = root / "real-tftp"
            real_tftp.mkdir()
            linked_tftp = root / "linked-tftp"
            linked_tftp.symlink_to(real_tftp, target_is_directory=True)
            ui = ModbusUI.__new__(ModbusUI)
            ui._firmware_stage_lock = threading.Lock()
            ui._staged_fw_digest = None

            with mock.patch.object(modbus_ui, "TFTP_ROOT_DIR", linked_tftp):
                with self.assertRaises(RuntimeError):
                    ui._stage_firmware(str(source), info["digest"])


if __name__ == "__main__":
    unittest.main()
