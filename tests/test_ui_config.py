import os
import unittest
from unittest import mock

from ui_config import read_ui_scale


class UIConfigTest(unittest.TestCase):
    def test_explicit_scale_and_limits(self):
        self.assertEqual(read_ui_scale("1.0"), 1.0)
        self.assertEqual(read_ui_scale("0.1"), 0.65)
        self.assertEqual(read_ui_scale("9"), 2.25)

    def test_invalid_environment_uses_historical_default(self):
        with mock.patch.dict(os.environ, {"GMS_UI_SCALE": "not-a-number"}):
            self.assertEqual(read_ui_scale(), 1.65)


if __name__ == "__main__":
    unittest.main()
