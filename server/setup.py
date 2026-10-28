#!/usr/bin/env python3
"""One-time setup for pii-mask's local model, run by the mod on first use.

Creates a Python environment in the cache folder, installs PyTorch (the CPU
build where there is one) and GLiNER into it, and downloads the PII model.
Everything stays in that folder; delete it to start over.

    python3 server/setup.py --cache ~/.cache/pii-mask

Prints one line per step for the mod's status line ("STEP 2/4 ..."), "ERROR
..." when a step fails, and writes the details to <cache>/setup.log.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time

MODEL = "urchade/gliner_multi_pii-v1"
GLINER = "gliner>=0.2.16,<0.3"
CPU_TORCH_INDEX = "https://download.pytorch.org/whl/cpu"


# Intel Macs: PyTorch 2.2.2 is the last release built for them, and only for
# Python 3.9-3.12. It was built against numpy 1, and transformers 4.57 is the
# newest release checked to accept PyTorch 2.2.
INTEL_MAC_TORCH = ["torch==2.2.2", "numpy<2"]
INTEL_MAC_EXTRA = ["transformers>=4.51.3,<4.58", "numpy<2"]


def say(line):
    print(line, flush=True)


def is_intel_mac(system=None, machine=None):
    return (system or platform.system()) == "Darwin" and (machine or platform.machine()) == "x86_64"


def plan(system, machine):
    """What to install where: the Python versions PyTorch has builds for, the
    PyTorch packages, and anything GLiNER's dependencies must be held to."""
    if is_intel_mac(system, machine):
        return {"python_max": (3, 12), "torch": INTEL_MAC_TORCH, "extra": INTEL_MAC_EXTRA, "cpu_index": False}
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

    log = open(os.path.join(cache, "setup.log"), "a", encoding="utf-8")
    log.write(f"\n--- setup {time.strftime('%Y-%m-%d %H:%M:%S')} on {platform.platform()}, Python {platform.python_version()}\n")
    log.flush()
    venv = os.path.join(cache, "venv")
    py = os.path.join(venv, "bin", "python")

    def run(cmd, what, env=None):
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"{what} failed (exit {r.returncode}).")

    how = plan(platform.system(), platform.machine())
    log.write(f"plan: {how}\n")
    try:
        say("STEP 1/4 creating a Python environment")
        base = pick_python(how["python_max"])
        if base is None:
            major, minor = how["python_max"]
            raise RuntimeError(
                f"PyTorch for this Intel Mac needs Python {major}.{minor} or older, and this is {platform.python_version()}. "
                f"Install it (brew install python@{major}.{minor}), then run /pii-mask retry."
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
        run([py, "-m", "pip", "install", "--quiet", GLINER, *how["extra"]], "installing GLiNER")

        say("STEP 4/4 downloading the PII model")
        env = dict(os.environ, HF_HOME=os.path.join(cache, "hf"))
        run([py, "-c", f"from gliner import GLiNER; GLiNER.from_pretrained({MODEL!r})"], "downloading the model", env)
    except RuntimeError as err:
        say(f"ERROR {err}")
        return 1

    write_marker(marker, {"model": MODEL, "python": platform.python_version()})
    say("DONE")
    return 0


def write_marker(path, data):
    data = dict(data, installed=time.strftime("%Y-%m-%dT%H:%M:%S"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
