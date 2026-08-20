import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SourceRegressionTest(unittest.TestCase):
    def test_all_python_sources_parse(self):
        for path in ROOT.rglob("*.py"):
            if any(part in {".venv", "venv", "myenv"} for part in path.parts):
                continue
            with self.subTest(path=path.relative_to(ROOT)):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_pymodbus_read_count_is_keyword_only(self):
        path = ROOT / "modbus_ui.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad_calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "read_holding_registers" and len(node.args) > 1:
                bad_calls.append(node.lineno)
        self.assertEqual(bad_calls, [], f"count must be keyword-only at lines {bad_calls}")

    def test_documented_40007_register_is_not_shifted(self):
        source = (ROOT / "modbus_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("value_40007 = raw_regs[7]", source)
        self.assertTrue(
            "register_value(raw_regs, 40007)" in source
            or "value_40007 = raw_regs[6]" in source
        )

    def test_alarm_ids_are_not_double_prefixed(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn('f"modbus_{idx}"', source)
        self.assertNotIn('f"analog_{idx}"', source)

    def test_ups_outer_frame_is_not_packed(self):
        source = (ROOT / "ups_monitor_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("box_frame.pack(", source)

    def test_analog_preserves_physical_adc_slots(self):
        source = (ROOT / "analog_ui.py").read_text(encoding="utf-8")
        self.assertIn("box_index = slot * 4 + channel", source)
        self.assertIn("def stop(self)", source)


if __name__ == "__main__":
    unittest.main()
