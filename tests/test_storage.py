from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HAS_RUNTIME_DEPS = (
    importlib.util.find_spec("cryptography") is not None
    and importlib.util.find_spec("_tkinter") is not None
)
REPOSITORY = Path(__file__).resolve().parents[1]


@unittest.skipUnless(HAS_RUNTIME_DEPS, "cryptography or Tkinter is not installed")
class EncryptedStorageTest(unittest.TestCase):
    def _environment(self, state_dir: Path) -> dict[str, str]:
        environment = dict(os.environ)
        environment["GMS_STATE_DIR"] = str(state_dir)
        environment["PYTHONPATH"] = str(REPOSITORY)
        return environment

    def _run(self, state_dir: Path, code: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPOSITORY,
            env=self._environment(state_dir),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def test_concurrent_first_start_creates_one_private_key(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            code = "import utils; print(utils.load_key().decode('ascii'))"
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", code],
                    cwd=REPOSITORY,
                    env=self._environment(state_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(8)
            ]
            results = [process.communicate(timeout=15) for process in processes]
            self.assertTrue(
                all(process.returncode == 0 for process in processes), results
            )
            keys = {stdout.strip() for stdout, _stderr in results}
            self.assertEqual(len(keys), 1)
            mode = stat.S_IMODE((state_dir / "secret.key").stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_settings_without_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "settings.json").write_bytes(b"encrypted-data")
            result = self._run(state_dir, "import utils; utils.load_key()")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("설정 파일은 있지만 암호화 키가 없습니다", result.stderr)
            generated = self._run(state_dir, "import utils; utils.generate_key()")
            self.assertNotEqual(generated.returncode, 0)
            self.assertIn("설정 파일은 있지만 암호화 키가 없습니다", generated.stderr)
            self.assertFalse((state_dir / "secret.key").exists())

    def test_encrypted_settings_round_trip_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            code = """
import json
import settings

value = settings.normalize_settings(
    {"modbus_boxes": 2, "audio_file": "audio/gms_k1.mp3"}
)
settings.save_settings(value)
print(json.dumps(settings.load_settings(), sort_keys=True))
"""
            result = self._run(state_dir, code)
            self.assertEqual(result.returncode, 0, result.stderr)
            loaded = json.loads(result.stdout)
            self.assertEqual(loaded["modbus_boxes"], 2)
            self.assertEqual(loaded["audio_file"], "audio/gms_k1.mp3")
            mode = stat.S_IMODE((state_dir / "settings.json").stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_importing_settings_does_not_create_state_files(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            result = self._run(state_dir, "import settings")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((state_dir / "secret.key").exists())
            self.assertFalse((state_dir / "settings.json").exists())

    def test_wrong_key_does_not_replace_encrypted_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            created = self._run(
                state_dir,
                "import settings; settings.save_settings({'modbus_boxes': 1})",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            original = (state_dir / "settings.json").read_bytes()
            replaced = self._run(
                state_dir,
                "import os; from pathlib import Path; "
                "from cryptography.fernet import Fernet; "
                "(Path(os.environ['GMS_STATE_DIR']) / 'secret.key').write_bytes("
                "Fernet.generate_key())",
            )
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            loaded = self._run(state_dir, "import settings; settings.load_settings()")
            self.assertNotEqual(loaded.returncode, 0)
            self.assertIn("복호화할 수 없습니다", loaded.stderr)
            self.assertEqual((state_dir / "settings.json").read_bytes(), original)
            self.assertEqual(list(state_dir.glob("settings.broken-*.json")), [])

    def test_authenticated_malformed_settings_fail_closed_on_every_start(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            settings_file = state_dir / "settings.json"
            created = self._run(
                state_dir,
                "import os, settings; "
                "settings.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True); "
                "settings.SETTINGS_FILE.write_bytes(settings.encrypt_data('[]')); "
                "os.chmod(settings.SETTINGS_FILE, 0o600)",
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            first = self._run(state_dir, "import settings; settings.load_settings()")
            second = self._run(state_dir, "import settings; settings.load_settings()")
            self.assertNotEqual(first.returncode, 0)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("기본값으로 초기화하지 않았습니다", first.stderr)

            self.assertTrue(settings_file.exists())
            backups = list(state_dir.glob("settings.broken-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), settings_file.read_bytes())


if __name__ == "__main__":
    unittest.main()
