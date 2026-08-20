import unittest

from core_utils import (
    battery_percentage,
    decode_error_register,
    format_log_extra,
    ip_to_register_words,
    numeric_from_log_value,
    register_index,
    register_value,
    registers_to_ipv4,
)


class CoreUtilsTest(unittest.TestCase):
    def test_documented_register_lookup(self):
        self.assertEqual(register_index(40007), 6)
        self.assertEqual(register_value(list(range(24)), 40007), 6)

    def test_error_decode_uses_documented_error_bits(self):
        self.assertEqual(decode_error_register(0), "")
        self.assertEqual(decode_error_register(1 << 2), "E-12")
        self.assertEqual(decode_error_register((1 << 3) | 1), "E-10")
        self.assertEqual(decode_error_register(1 << 4), "")

    def test_log_values_accept_numbers_and_decorated_text(self):
        self.assertEqual(numeric_from_log_value("264.0 (18.080mA, HMDS)"), 264.0)
        self.assertIsNone(numeric_from_log_value("PWR OFF"))
        self.assertEqual(format_log_extra(7), "0x0007")
        self.assertEqual(format_log_extra("VALUE_CHANGED"), "VALUE_CHANGED")

    def test_ip_register_round_trip(self):
        words = ip_to_register_words("192.168.0.10")
        self.assertEqual(words, (0xC0A8, 0x000A))
        self.assertEqual(registers_to_ipv4(words), "192.168.0.10")

    def test_battery_percentage_is_clamped(self):
        self.assertEqual(battery_percentage(25.2), 100)
        self.assertEqual(battery_percentage(22.2), 50)
        self.assertEqual(battery_percentage(18.0), 0)


if __name__ == "__main__":
    unittest.main()
