"""Tests of server.py's chunking, cache and queue: python3 -m unittest discover -s server"""

import threading
import time
import unittest

from server import BATCH, Detector, Fake, Gate, copy_path, load_key, make_handler


class Counting:
    """A model that finds every "Anna" and counts the chunks it is given."""

    name = "counting"

    def __init__(self, delay=0.0):
        self.chunks = 0
        self.delay = delay

    def predict(self, chunks, labels, threshold):
        self.chunks += len(chunks)
        time.sleep(self.delay)
        out = []
        for c in chunks:
            i = c.find("Anna")
            out.append([] if i < 0 else [{"text": "Anna", "label": "person", "start": i, "end": i + 4, "score": 0.9}])
        return out


class DetectorTests(unittest.TestCase):
    def test_offsets_are_in_the_whole_text(self):
        d = Detector(Counting())
        text = ("x" * 900 + "\n") * 3 + "hello Anna\n"
        [found] = d.detect([text], ["person"], 0.5)
        self.assertEqual(len(found), 1)
        self.assertEqual(text[found[0]["start"]:found[0]["end"]], "Anna")

    def test_a_chunk_seen_before_is_not_run_again(self):
        model = Counting()
        d = Detector(model)
        system = "The system prompt, the same every turn. " * 60
        d.detect([system], ["person"], 0.5)
        first = model.chunks
        found = d.detect([system + "\nNow ask Anna."], ["person"], 0.5)
        self.assertEqual(model.chunks - first, 1)  # only the new chunk
        self.assertEqual(found[0][0]["text"], "Anna")

    def test_other_labels_are_not_answered_from_the_cache(self):
        model = Counting()
        d = Detector(model)
        d.detect(["Anna"], ["person"], 0.5)
        d.detect(["Anna"], ["person", "address"], 0.5)
        self.assertEqual(model.chunks, 2)


class GateTests(unittest.TestCase):
    def test_a_small_request_goes_before_a_long_one_that_is_waiting(self):
        model = Counting(delay=0.05)
        d = Detector(model)
        order = []
        long_text = "\n".join(f"line {i} " + "y" * 990 for i in range(BATCH * 4))

        def long_job():
            d.detect([long_text], ["person"], 0.5)
            order.append("long")

        def short_job():
            d.detect(["Write to Anna"], ["person"], 0.5)
            order.append("short")

        t1 = threading.Thread(target=long_job)
        t1.start()
        time.sleep(0.02)  # the long one is running its first batch
        t2 = threading.Thread(target=short_job)
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(order, ["short", "long"])

    def test_gate_runs_one_at_a_time(self):
        gate = Gate()
        running = []
        peak = []

        def job():
            running.append(1)
            peak.append(len(running))
            time.sleep(0.01)
            running.pop()

        threads = [threading.Thread(target=gate.run, args=(1, job)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(max(peak), 1)


if __name__ == "__main__":
    unittest.main()


class InkBoxTest(unittest.TestCase):
    def test_the_box_is_around_the_ink_with_a_margin(self):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest("Pillow is not installed")
        from media import ink_box

        img = Image.new("RGB", (800, 1100), "white")
        ImageDraw.Draw(img).rectangle([100, 200, 300, 260], fill="black")
        self.assertEqual(ink_box(img), (84, 184, 317, 277))
        self.assertEqual(ink_box(Image.new("RGB", (50, 40), "white")), (0, 0, 50, 40))


class JobsTest(unittest.TestCase):
    def test_a_long_request_runs_as_a_job_answered_by_a_long_poll(self):
        import json
        import urllib.request
        from http.server import ThreadingHTTPServer

        class Slow(Fake):
            def predict(self, texts, labels, threshold):
                time.sleep(0.3)
                return super().predict(texts, labels, threshold)

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Detector(Slow(None)), {"last": time.time()}, KEY))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        get = lambda path: urllib.request.urlopen(urllib.request.Request(base + path, headers={"x-pii-mask-key": KEY}))
        try:
            body = json.dumps({"texts": ["Write to Lars Holm"], "labels": ["person"], "async": True}).encode()
            with urllib.request.urlopen(urllib.request.Request(base + "/detect", body, {"content-type": "application/json", "x-pii-mask-key": KEY})) as r:
                self.assertEqual(r.status, 202)
                job = json.loads(r.read())["job"]
            with get(f"/job/{job}?wait=0") as r:
                self.assertEqual(json.loads(r.read()), {"done": False})
            t = time.time()
            with get(f"/job/{job}?wait=5") as r:
                answer = json.loads(r.read())
            self.assertLess(time.time() - t, 2)  # answered when done, not after the full wait
            self.assertEqual(answer["status"], 200)
            self.assertEqual(answer["body"]["results"][0][0]["text"], "Lars Holm")
            with self.assertRaises(urllib.error.HTTPError):  # answered once
                get(f"/job/{job}")
        finally:
            server.shutdown()


KEY = "k" * 64


class AccessTest(unittest.TestCase):
    """Only the mod may use the server: not a web page, not another program without the key."""

    def setUp(self):
        from http.server import ThreadingHTTPServer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Detector(Fake(None)), {"last": time.time()}, KEY))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def ask(self, path, body=None, headers=None):
        """(status, answer) for a request with exactly these headers (and Host 127.0.0.1:<port>)."""
        import http.client
        import json

        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        c.request("POST" if body is not None else "GET", path, body=body, headers=h)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_the_mod_with_its_key_is_answered(self):
        import json

        status, out = self.ask("/detect", json.dumps({"texts": ["Lars Holm"], "labels": ["person"]}), {"content-type": "application/json", "x-pii-mask-key": KEY})
        self.assertEqual(status, 200)
        self.assertEqual(out["results"][0][0]["text"], "Lars Holm")

    def test_without_the_key_nothing_but_health(self):
        import json

        body = json.dumps({"texts": ["Lars Holm"], "labels": ["person"]})
        self.assertEqual(self.ask("/detect", body, {"content-type": "application/json"})[0], 401)
        self.assertEqual(self.ask("/detect", body, {"content-type": "application/json", "x-pii-mask-key": "k" * 63 + "x"})[0], 401)
        self.assertEqual(self.ask("/stop", "{}", {"content-type": "application/json"})[0], 401)
        self.assertEqual(self.ask("/job/1")[0], 401)
        self.assertEqual(self.ask("/health")[0], 200)

    def test_a_web_page_is_refused_even_with_the_key(self):
        import json

        body = json.dumps({"texts": ["Lars Holm"], "labels": ["person"]})
        ok = {"content-type": "application/json", "x-pii-mask-key": KEY}
        self.assertEqual(self.ask("/detect", body, {**ok, "Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.ask("/detect", body, {**ok, "Host": f"evil.example:{self.port}"})[0], 403)  # DNS rebinding
        self.assertEqual(self.ask("/health", None, {"Host": f"evil.example:{self.port}"})[0], 403)
        self.assertEqual(self.ask("/detect", body, {**ok, "content-type": "text/plain"})[0], 415)

    def test_health_proves_the_key_without_giving_it(self):
        import hashlib

        status, out = self.ask("/health?nonce=abc123")
        self.assertEqual(status, 200)
        self.assertEqual(out["proof"], hashlib.sha256(f"{KEY}:abc123".encode()).hexdigest())
        self.assertNotIn(KEY, str(out))


class PathsTest(unittest.TestCase):
    def test_copies_go_only_to_a_pii_mask_folder_as_read_files(self):
        import os

        ok = os.path.join(os.path.abspath(os.sep), "proj", ".claude", "pii-mask", "read-1793834106395-0.png")
        self.assertEqual(copy_path(ok), ok)
        for bad in [os.path.join(os.path.abspath(os.sep), "home", "u", ".bashrc"),
                    os.path.join(os.path.abspath(os.sep), "proj", ".claude", "pii-mask", "evil.png"),
                    os.path.join(os.path.abspath(os.sep), "proj", ".claude", "pii-mask", "..", "settings.json"),
                    os.path.join(os.path.abspath(os.sep), "proj", "read-1-0.png"),
                    "read-1-0.png"]:
            with self.assertRaises(ValueError):
                copy_path(bad)

    def test_the_key_file_is_made_once_for_this_user_only(self):
        import os
        import stat
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            a = load_key(d)
            self.assertEqual(load_key(d), a)
            self.assertGreaterEqual(len(a), 64)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "token")).st_mode), 0o600)



class PixelateTest(unittest.TestCase):
    def test_a_face_becomes_a_few_flat_blocks_however_big_it_is(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        import random

        from media import FACE_BLOCKS, pixelate

        for side in (120, 500, 1400):
            rnd = random.Random(side)
            img = Image.new("RGB", (side, side))
            img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(side * side)])
            pixelate(img, (0, 0, side, side))
            self.assertLessEqual(len(set(img.getdata())), FACE_BLOCKS * FACE_BLOCKS)
