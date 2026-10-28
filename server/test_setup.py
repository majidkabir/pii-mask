"""Tests of setup.py's choices per machine: python3 -m unittest discover -s server"""

import unittest

from setup import error_in, fix_for, pick_python, plan


class Plan(unittest.TestCase):
    def test_intel_mac_pins_the_last_pytorch_built_for_it(self):
        p = plan("Darwin", "x86_64")
        self.assertEqual(p["python_max"], (3, 11))  # PyTorch 2.2's compiler doesn't run on 3.12
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
        self.assertEqual(pick_python((3, 11), which, ("/usr/bin/python3", (3, 11))), "/usr/bin/python3")
        self.assertEqual(pick_python(None, which, ("/usr/bin/python3", (3, 13))), "/usr/bin/python3")
        # Too new: the newest suitable one on the PATH (3.12 is skipped on an Intel Mac).
        self.assertEqual(pick_python((3, 11), which, ("/usr/bin/python3", (3, 12))), "/opt/py311")
        self.assertEqual(pick_python((3, 11), {"python3.10": "/opt/py310"}.get, ("/x", (3, 13))), "/opt/py310")
        # None found.
        self.assertIsNone(pick_python((3, 11), lambda _: None, ("/x", (3, 12))))


class Errors(unittest.TestCase):
    def test_names_the_root_cause_of_a_chained_exception(self):
        log = """Traceback (most recent call last):
  File "modeling_modernbert.py", line 207, in ModernBertEmbeddings
    @torch.compile(dynamic=True)
RuntimeError: Dynamo is not supported on Python 3.12+

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: Could not import module 'ModernBertModel'. Are this object's requirements defined correctly?""".splitlines()
        msg = error_in(log)
        self.assertTrue(msg.startswith("RuntimeError: Dynamo is not supported on Python 3.12+"), msg)
        self.assertIn("ModernBertModel", msg)
        self.assertIn("python@3.11", fix_for(msg, "Darwin"))

    def test_message_on_the_lines_after_the_exception(self):
        # What transformers raises when the tokenizer's converter lacks protobuf (seen on an Intel Mac).
        log = """Traceback (most recent call last):
  File "convert_slow_tokenizer.py", line 553, in __init__
    requires_backends(self, "protobuf")
  File "import_utils.py", line 2143, in requires_backends
    raise ImportError("".join(failed))
ImportError:
DebertaV2Converter requires the protobuf library but it was not found in your environment. Check out the instructions on the
installation page of its repo: https://github.com/protocolbuffers/protobuf/tree/master/python#installation and follow the ones

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "<string>", line 1, in <module>
ImportError:
GLiNER could not load the tokenizer.""".splitlines()
        msg = error_in(log)
        self.assertIn("DebertaV2Converter requires the protobuf library", msg)
        self.assertIn("(then: ImportError: GLiNER could not load the tokenizer.)", msg)
        self.assertEqual(fix_for(msg, "Darwin"), "~/.cache/pii-mask/venv/bin/pip install protobuf, then /pii-mask retry")

    def test_tokenizer_libraries_are_installed(self):
        from setup import TOKENIZER
        self.assertIn("protobuf", TOKENIZER)
        self.assertIn("sentencepiece", TOKENIZER)

    def test_pip_error_line_wins(self):
        self.assertEqual(error_in(["Collecting torch", "ERROR: No matching distribution found for torch"]),
                         "ERROR: No matching distribution found for torch")


if __name__ == "__main__":
    unittest.main()
