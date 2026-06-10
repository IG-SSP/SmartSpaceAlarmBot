import tarfile
import tempfile
import unittest
from pathlib import Path

from install import add_tree, format_env


class InstallTests(unittest.TestCase):
    def test_format_env_quotes_values_with_spaces(self):
        self.assertEqual(format_env({"A": "one two", "B": "plain"}), 'A="one two"\nB=plain\n')

    def test_add_tree_skips_state_and_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            (root / ".state").mkdir()
            (root / ".state" / "controllers.json").write_text("{}", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "x.pyc").write_bytes(b"cache")
            backup_path = Path(tmp) / "backup.tar.gz"

            with tarfile.open(backup_path, "w:gz") as archive:
                add_tree(archive, root, "wb-cloud-watcher")

            with tarfile.open(backup_path, "r:gz") as archive:
                names = archive.getnames()

        self.assertIn("wb-cloud-watcher/.env", names)
        self.assertNotIn("wb-cloud-watcher/.state/controllers.json", names)
        self.assertNotIn("wb-cloud-watcher/__pycache__/x.pyc", names)


if __name__ == "__main__":
    unittest.main()
