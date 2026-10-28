"""Tests of server.py's chunking, cache and queue: python3 -m unittest discover -s server"""

import threading
import time
import unittest

from server import BATCH, Detector, Gate


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
