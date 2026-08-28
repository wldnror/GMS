import unittest

from core_utils import display_engineering_value, format_engineering_value, scale_4_20ma
from gms_core import (
    aggregate_alarm_mode,
    analog_signal_fault,
    hash_password,
    normalize_box_id,
    parse_numeric,
    password_is_hashed,
    reconnect_delay,
    register_offset,
    register_value,
    ups_fault_active,
    validate_ipv4,
    validate_tftp_ipv4,
    verify_password,
)


class CoreHelpersTest(unittest.TestCase):
    def test_register_offsets(self):
        self.assertEqual(register_offset(40001), 0)
        self.assertEqual(register_offset(40007), 6)
        self.assertEqual(register_value(list(range(24)), 40007), 6)

    def test_ipv4_validation(self):
        self.assertEqual(validate_ipv4(" 192.168.0.10 "), "192.168.0.10")
        with self.assertRaises(ValueError):
            validate_ipv4("999.1.1.1")

    def test_tftp_address_must_be_device_reachable(self):
        self.assertEqual(validate_tftp_ipv4("192.168.10.4"), "192.168.10.4")
        for address in ("0.0.0.0", "127.0.0.1", "224.0.0.1", "255.255.255.255"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                validate_tftp_ipv4(address)

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

    def test_alarm_and_fault_are_preserved_independently(self):
        self.assertEqual(aggregate_alarm_mode([]), "none")
        self.assertEqual(aggregate_alarm_mode([{"active": True}]), "alarm")
        self.assertEqual(aggregate_alarm_mode([{"fut": True}]), "fut")
        self.assertEqual(
            aggregate_alarm_mode([{"active": True}, {"fut": True}]),
            "alarm_fut",
        )

    def test_reconnect_backoff_is_capped(self):
        self.assertEqual([reconnect_delay(i) for i in range(1, 6)], [2, 4, 8, 16, 30])
        self.assertEqual(reconnect_delay(99), 30)

    def test_password_hash_round_trip_and_malformed_input(self):
        encoded = hash_password("1234", iterations=100_000, salt=b"0123456789abcdef")
        self.assertTrue(password_is_hashed(encoded))
        self.assertTrue(verify_password("1234", encoded))
        self.assertFalse(verify_password("9999", encoded))
        self.assertFalse(verify_password("x" * 65, encoded))
        self.assertFalse(verify_password("1234", "pbkdf2_sha256$999999999$x$y"))
        with self.assertRaises(ValueError):
            hash_password("x" * 65)

    def test_analog_signal_fault_limits(self):
        self.assertTrue(analog_signal_fault(float("nan")))
        self.assertTrue(analog_signal_fault(3.59))
        self.assertFalse(analog_signal_fault(3.6))
        self.assertFalse(analog_signal_fault(21.0))
        self.assertTrue(analog_signal_fault(21.01))
        with self.assertRaises(ValueError):
            analog_signal_fault(4.0, low=5.0, high=4.0)

    def test_ups_fault_hysteresis_and_sensor_failure(self):
        self.assertTrue(ups_fault_active(100, sensor_error=True, was_fault=False))
        self.assertTrue(ups_fault_active(20, sensor_error=False, was_fault=False))
        self.assertFalse(ups_fault_active(21, sensor_error=False, was_fault=False))
        self.assertTrue(ups_fault_active(24, sensor_error=False, was_fault=True))
        self.assertFalse(ups_fault_active(25, sensor_error=False, was_fault=True))
        with self.assertRaises(ValueError):
            ups_fault_active(
                50,
                sensor_error=False,
                was_fault=False,
                low_percent=30,
                clear_percent=20,
            )


if __name__ == "__main__":
    unittest.main()
