"""Tests of setup.py's choices per machine: python3 -m unittest discover -s server"""

import unittest

from setup import pick_python, plan


class Plan(unittest.TestCase):
    def test_intel_mac_pins_the_last_pytorch_built_for_it(self):
        p = plan("Darwin", "x86_64")
        self.assertEqual(p["python_max"], (3, 12))
        self.assertIn("torch==2.2.2", p["torch"])
        self.assertIn("numpy<2", p["torch"])
        self.assertTrue(any(x.startswith("transformers") and "<4.58" in x for x in p["extra"]))

    def test_apple_silicon_and_linux_take_the_latest(self):
        self.assertEqual(plan("Darwin", "arm64")["torch"], ["torch"])
        self.assertEqual(plan("Darwin", "arm64")["python_max"], None)
        self.assertTrue(plan("Linux", "x86_64")["cpu_index"])

    def test_python_choice(self):
        which = {"python3.12": "/opt/py312", "python3.11": "/opt/py311"}.get
        # A suitable Python is used as it is.
        self.assertEqual(pick_python((3, 12), which, ("/usr/bin/python3", (3, 11))), "/usr/bin/python3")
        self.assertEqual(pick_python(None, which, ("/usr/bin/python3", (3, 13))), "/usr/bin/python3")
        # Too new: the newest suitable one on the PATH.
        self.assertEqual(pick_python((3, 12), which, ("/usr/bin/python3", (3, 13))), "/opt/py312")
        self.assertEqual(pick_python((3, 12), {"python3.10": "/opt/py310"}.get, ("/x", (3, 13))), "/opt/py310")
        # None found.
        self.assertIsNone(pick_python((3, 12), lambda _: None, ("/x", (3, 13))))


if __name__ == "__main__":
    unittest.main()
