import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class SourceContracts(unittest.TestCase):
    def test_production_sources_compile(self):
        for name in (
            "core_utils.py",
            "common.py",
            "virtual_keyboard.py",
            "log_viewer.py",
            "ups_monitor_ui.py",
            "utils.py",
            "settings.py",
            "analog_ui.py",
            "modbus_ui.py",
            "main.py",
            "test1.py",
        ):
            source = (ROOT / name).read_text(encoding="utf-8")
            compile(source, name, "exec")

    def test_pymodbus_reads_use_keyword_count(self):
        for name in ("modbus_ui.py", "test1.py"):
            tree = ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(
                    node.func, ast.Attribute
                ):
                    continue
                if node.func.attr != "read_holding_registers":
                    continue
                keywords = {kw.arg for kw in node.keywords}
                self.assertIn("address", keywords, f"{name}:{node.lineno}")
                self.assertIn("count", keywords, f"{name}:{node.lineno}")
                self.assertEqual(
                    node.args, [], f"{name}:{node.lineno} has positional args"
                )

    def test_modbus_register_access_is_not_hardcoded_to_wrong_index(self):
        source = (ROOT / "modbus_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("raw_regs[7]", source)
        self.assertIn("register_value(raw_regs, 40007)", source)


if __name__ == "__main__":
    unittest.main()
