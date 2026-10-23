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
import subprocess
import sys
import time

MODEL = "urchade/gliner_multi_pii-v1"
GLINER = "gliner>=0.2.16,<0.3"
CPU_TORCH_INDEX = "https://download.pytorch.org/whl/cpu"


def say(line):
    print(line, flush=True)


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

    try:
        say("STEP 1/4 creating a Python environment")
        if not os.path.exists(py):
            run([sys.executable, "-m", "venv", venv], "creating the Python environment")
            run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], "updating pip")

        say("STEP 2/4 installing PyTorch (the largest download)")
        if subprocess.run([py, "-c", "import torch"], stdout=log, stderr=subprocess.STDOUT).returncode != 0:
            installed = False
            if platform.system() == "Linux":
                # The CPU build is a fraction of the default one, which bundles CUDA.
                try:
                    run([py, "-m", "pip", "install", "--quiet", "torch", "--index-url", CPU_TORCH_INDEX], "installing CPU PyTorch")
                    installed = True
                except RuntimeError:
                    log.write("CPU index not reachable; using the default PyTorch package\n")
            if not installed:
                run([py, "-m", "pip", "install", "--quiet", "torch"], "installing PyTorch")

        say("STEP 3/4 installing GLiNER")
        run([py, "-m", "pip", "install", "--quiet", GLINER], "installing GLiNER")

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
