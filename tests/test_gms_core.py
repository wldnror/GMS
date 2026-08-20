import unittest

from core_utils import display_engineering_value, format_engineering_value, scale_4_20ma
from gms_core import normalize_box_id, parse_numeric, register_offset, register_value, validate_ipv4


class CoreHelpersTest(unittest.TestCase):
    def test_register_offsets(self):
        self.assertEqual(register_offset(40001), 0)
        self.assertEqual(register_offset(40007), 6)
        self.assertEqual(register_value(list(range(24)), 40007), 6)

    def test_ipv4_validation(self):
        self.assertEqual(validate_ipv4(" 192.168.0.10 "), "192.168.0.10")
        with self.assertRaises(ValueError):
            validate_ipv4("999.1.1.1")

    def test_analog_conversion_and_hmds_display(self):
        self.assertEqual(scale_4_20ma(4.0, 3000.0), 0.0)
        self.assertEqual(scale_4_20ma(20.0, 3000.0), 3000.0)
        self.assertEqual(display_engineering_value("HMDS", 2640.0), 264.0)
        self.assertEqual(format_engineering_value("HMDS", 2640.0).strip(), "264.0")

    def test_log_number_parser(self):
        self.assertEqual(parse_numeric("123.4 (4.123mA, HMDS)"), 123.4)
        self.assertIsNone(parse_numeric("VALUE_CHANGED"))

    def test_box_id_normalization(self):
        self.assertEqual(normalize_box_id("modbus", "modbus_modbus_0"), "modbus_0")
        self.assertEqual(normalize_box_id("analog", 2), "analog_2")


if __name__ == "__main__":
    unittest.main()
