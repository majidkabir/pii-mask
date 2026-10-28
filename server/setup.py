#!/usr/bin/env python3
"""One-time setup for pii-mask's local model, run by the mod on first use.

Creates a Python environment in the cache folder, installs PyTorch (the CPU
build where there is one) and GLiNER into it, and downloads the PII model.
Everything stays in that folder; delete it to start over.

    python3 server/setup.py --cache ~/.cache/pii-mask
    py -3 server\\setup.py --cache %LOCALAPPDATA%\\pii-mask      (Windows)

Prints, for the mod to show: "STEP 2/4 <what>" at each step, "SIZE <bytes>"
every few seconds (what has been downloaded and installed so far), and on
failure "ERROR <reason>" and, when there is one, "FIX <command to run>".
The details go to <cache>/setup.log.
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time

MODEL = "urchade/gliner_multi_pii-v1"
GLINER = "gliner>=0.2.16,<0.3"
# The PII model's tokenizer (mdeberta) is converted on first load, which needs
# protobuf ("DebertaV2Converter requires the protobuf library") and sentencepiece.
# Neither is a dependency of GLiNER.
TOKENIZER = ["protobuf", "sentencepiece"]
# Checked with transformers 4.57 (Intel Macs, Python 3.9) and 5.16 (everywhere
# else, Python 3.10-3.14); a major release later than that is not.
TRANSFORMERS_CAP = "transformers<6"
CPU_TORCH_INDEX = "https://download.pytorch.org/whl/cpu"


# Intel Macs: PyTorch 2.2.2 is the last release built for them. Its compiler
# (torch.compile) does not support Python 3.12, and transformers uses it when
# GLiNER is imported ("Dynamo is not supported on Python 3.12+"), so these Macs
# need Python 3.11 or older. It was built against numpy 1, and transformers
# 4.57 is the newest release checked to accept PyTorch 2.2.
INTEL_MAC_TORCH = ["torch==2.2.2", "numpy<2"]
INTEL_MAC_EXTRA = ["transformers>=4.51.3,<4.58", "numpy<2"]


def say(line):
    print(line, flush=True)


def folder_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def report_size(cache, stop):
    """Says how much is in the cache folder every few seconds, so the mod can show progress."""
    while not stop.wait(3):
        say(f"SIZE {folder_size(cache)}")


def last_error(log_path):
    """What went wrong, from the end of the log: pip's "ERROR: ..." line, or a
    Python exception. When one exception caused another, the first one is the
    real reason ("RuntimeError: Dynamo is not supported..." behind "Could not
    import module ..."), so it is named first."""
    try:
        lines = open(log_path, encoding="utf-8", errors="replace").read().splitlines()[-300:]
    except OSError:
        return ""
    return error_in(lines)


def error_in(lines):
    stripped = [l.strip() for l in lines]
    for s in reversed(stripped):
        if s.startswith("ERROR:"):
            return s[:300]
    exceptions = [i for i, s in enumerate(stripped) if re_exception(s)]
    if not exceptions:
        return ""
    last = exceptions[-1]
    # Walk back through "The above exception was the direct cause / During handling ..." to the first cause.
    root = last
    for i in reversed(exceptions[:-1]):
        between = " ".join(stripped[i + 1:root])
        if "direct cause of the following exception" in between or "During handling of the above exception" in between:
            root = i
        else:
            break
    if root == last:
        return message_at(stripped, last)[:300]
    return f"{message_at(stripped, root)[:200]} (then: {message_at(stripped, last)[:120]})"


def message_at(stripped, i):
    """The exception on line i with its message. Some messages start on the next
    line ("ImportError:" then "X requires the protobuf library ..."), so those
    lines are joined until the traceback goes on."""
    line = stripped[i]
    if line.split(":", 1)[1].strip():
        return line
    more = []
    for s in stripped[i + 1:]:
        if not s or s.startswith(("Traceback", "File ", "The above exception", "During handling")) or re_exception(s):
            break
        more.append(s)
        if len(" ".join(more)) > 300:
            break
    return (line + " " + " ".join(more)).strip()


def re_exception(line):
    head = line.split(":", 1)[0]
    return head.endswith(("Error", "Exception")) and " " not in head


def fix_for(reason, system, py="python"):
    """A command that fixes a known failure, or None. `py` is the environment's Python."""
    r = reason.lower()
    if ("ensurepip" in r or "venv" in r) and system == "Linux":
        return "sudo apt install python3-venv"
    if system == "Windows" and ("winerror 126" in r or "c10.dll" in r or "fbgemm.dll" in r or "vcruntime" in r):
        # PyTorch's DLLs need the Visual C++ runtime.
        return "winget install Microsoft.VCRedist.2015+.x64, then /pii-mask retry"
    if system == "Windows" and "long path" in r:
        return ("as administrator: reg add HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem "
                "/v LongPathsEnabled /t REG_DWORD /d 1 /f, then /pii-mask retry")
    if "dynamo is not supported on python 3.12" in r:
        return "install Python 3.11 (brew install python@3.11), then /pii-mask retry"
    if "no matching distribution found for torch" in r:
        return "install Python 3.11 (brew install python@3.11, or your package manager), then /pii-mask retry"
    missing = re.search(r"requires the (\w[\w.-]*) library", reason)
    if missing:
        return f"{py} -m pip install {missing.group(1)}, then /pii-mask retry"
    if "no space left" in r:
        return "free some disk space (about 3 GB), then /pii-mask retry"
    if "connection" in r or "timed out" in r or "temporary failure in name resolution" in r:
        return "check your internet connection, then /pii-mask retry"
    return None


def is_intel_mac(system=None, machine=None):
    return (system or platform.system()) == "Darwin" and (machine or platform.machine()) == "x86_64"


def plan(system, machine):
    """What to install where: the Python versions PyTorch has builds for, the
    PyTorch packages, and anything GLiNER's dependencies must be held to."""
    if is_intel_mac(system, machine):
        return {"python_max": (3, 11), "torch": INTEL_MAC_TORCH, "extra": INTEL_MAC_EXTRA, "cpu_index": False}
    return {"python_max": None, "torch": ["torch"], "extra": [], "cpu_index": system == "Linux"}


def pick_python(python_max, which=shutil.which, current=None):
    """The Python to build the environment with: this one, or on a machine
    whose PyTorch needs an older Python, the newest suitable one on the PATH."""
    current = current or (sys.executable, sys.version_info[:2])
    if python_max is None or tuple(current[1]) <= python_max:
        return current[0]
    for minor in range(python_max[1], 8, -1):
        found = which(f"python3.{minor}")
        if found:
            return found
    return None


def venv_python(venv):
    if os.name == "nt":
        return os.path.join(venv, "Scripts", "python.exe")
    return os.path.join(venv, "bin", "python")


def venv_version(py):
    try:
        out = subprocess.run([py, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                             capture_output=True, text=True, timeout=30).stdout.split()
        return (int(out[0]), int(out[1]))
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache", required=True, help="the folder to install into")
    p.add_argument("--fake", action="store_true", help="tests only: skip the install and use the fake model")
    args = p.parse_args()

    cache = os.path.abspath(os.path.expanduser(args.cache))
    os.makedirs(cache, exist_ok=True)
    marker = os.path.join(cache, "installed.json")

    if args.fake:
        say("STEP 1/1 test mode: nothing to install")
        write_marker(marker, {"model": "fake", "fake": True})
        say("DONE")
        return 0

    if sys.version_info < (3, 9):
        say("ERROR Python 3.9 or newer is needed.")
        return 1

    log_path = os.path.join(cache, "setup.log")
    log = open(log_path, "a", encoding="utf-8")
    log.write(f"\n--- setup {time.strftime('%Y-%m-%d %H:%M:%S')} on {platform.platform()}, Python {platform.python_version()}\n")
    log.flush()
    venv = os.path.join(cache, "venv")
    py = venv_python(venv)

    # Downloads go through a temporary folder inside the cache, so its size shows progress.
    tmp = os.path.join(cache, "tmp")
    os.makedirs(tmp, exist_ok=True)
    base_env = dict(os.environ, TMPDIR=tmp, TMP=tmp, TEMP=tmp, PIP_PROGRESS_BAR="off", PYTHONUTF8="1",
                    HF_HOME=os.path.join(cache, "hf"), HF_HUB_DISABLE_SYMLINKS_WARNING="1")

    def run(cmd, what):
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=base_env)
        log.flush()
        if r.returncode != 0:
            detail = last_error(log_path)
            raise RuntimeError(f"{what} failed" + (f": {detail}" if detail else f" (exit {r.returncode}).") )

    stop = threading.Event()
    threading.Thread(target=report_size, args=(cache, stop), daemon=True).start()

    how = plan(platform.system(), platform.machine())
    log.write(f"plan: {how}\n")
    try:
        say("STEP 1/4 creating a Python environment")
        base = pick_python(how["python_max"])
        if base is None:
            major, minor = how["python_max"]
            say(f"FIX brew install python@{major}.{minor}")
            raise RuntimeError(
                f"this Intel Mac needs Python {major}.{minor} or older for PyTorch, and only {platform.python_version()} was found."
            )
        have = venv_version(py) if os.path.exists(py) else None
        if have and how["python_max"] and have > how["python_max"]:
            log.write(f"removing an environment built with Python {have}, too new for PyTorch here\n")
            shutil.rmtree(venv)
        if not os.path.exists(py):
            run([base, "-m", "venv", venv], "creating the Python environment")
            run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], "updating pip")

        say("STEP 2/4 installing PyTorch (the largest download)")
        if subprocess.run([py, "-c", "import torch"], stdout=log, stderr=subprocess.STDOUT).returncode != 0:
            installed = False
            if how["cpu_index"]:
                # The CPU build is a fraction of the default one, which bundles CUDA.
                try:
                    run([py, "-m", "pip", "install", "--quiet", *how["torch"], "--index-url", CPU_TORCH_INDEX], "installing CPU PyTorch")
                    installed = True
                except RuntimeError:
                    log.write("CPU index not reachable; using the default PyTorch package\n")
            if not installed:
                run([py, "-m", "pip", "install", "--quiet", *how["torch"]], "installing PyTorch")

        say("STEP 3/4 installing GLiNER")
        run([py, "-m", "pip", "install", "--quiet", GLINER, *TOKENIZER, TRANSFORMERS_CAP, *how["extra"]], "installing GLiNER")

        say("STEP 4/4 downloading the PII model")
        run([py, "-c", f"from gliner import GLiNER; GLiNER.from_pretrained({MODEL!r})"], "downloading the model")
    except RuntimeError as err:
        stop.set()
        fix = fix_for(str(err), platform.system(), py)
        if fix:
            say(f"FIX {fix}")
        say(f"ERROR {err}")
        return 1
    stop.set()
    shutil.rmtree(tmp, ignore_errors=True)
    say(f"SIZE {folder_size(cache)}")

    write_marker(marker, {"model": MODEL, "python": platform.python_version()})
    say("DONE")
    return 0


def write_marker(path, data):
    data = dict(data, installed=time.strftime("%Y-%m-%dT%H:%M:%S"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
