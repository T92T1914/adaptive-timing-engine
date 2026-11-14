"""Many threads against the mailbox and the adoption gate.

The generation check in the worker is what keeps an older completion from
replacing the current answer. These tests drive it from several submitting
threads at once, and a mutant with the check removed must fail the same
invariants. They are meant to run on free threaded builds too, where the GIL
no longer hides a missing lock.
"""
import inspect
import sys
import textwrap
import threading
import unittest

from adaptive_timing import worker as worker_module
from adaptive_timing.worker import LatestWorker

SUBMITTERS = 4
REQUESTS_EACH = 400


def _mutant_class():
    """LatestWorker with the stale completion check removed."""
    source = textwrap.dedent(inspect.getsource(LatestWorker))
    needle = "if not self._closed and generation == self._generation:"
    assert source.count(needle) == 1, "the generation check moved, update the mutant"
    source = source.replace(needle, "if not self._closed:")
    namespace = dict(vars(worker_module))
    exec(compile(source, "<mutant LatestWorker>", "exec"), namespace)
    return namespace["LatestWorker"]


def _slow_compute(payload):
    # Take a little time so that submitters replace requests mid computation.
    total = 0
    for step in range(200):
        total += step
    return payload


def drive(worker_class):
    """Run the stress and return the list of broken invariants."""
    problems = []
    tokens = {}
    tokens_lock = threading.Lock()
    completed = [0]
    completed_lock = threading.Lock()
    stop = threading.Event()
    taken = []

    with worker_class(_slow_compute) as worker:
        def submit(index):
            for number in range(REQUESTS_EACH):
                token = (index, number)
                generation = worker.request(token)
                with tokens_lock:
                    tokens[generation] = token
                with completed_lock:
                    completed[0] = max(completed[0], generation)

        def consume():
            while not stop.is_set():
                with completed_lock:
                    floor = completed[0]
                outcome = worker.take()
                if outcome is None:
                    continue
                if outcome.generation < floor:
                    problems.append(
                        f"took generation {outcome.generation} after request "
                        f"{floor} had returned")
                taken.append(outcome)

        consumer = threading.Thread(target=consume)
        submitters = [threading.Thread(target=submit, args=(index,))
                      for index in range(SUBMITTERS)]
        consumer.start()
        for thread in submitters:
            thread.start()
        for thread in submitters:
            thread.join()
        if not worker.wait_idle(30):
            problems.append("worker never went idle")
        stop.set()
        consumer.join()
        last = worker.take()
        if last is not None:
            taken.append(last)
        stats = worker.stats()
        total = SUBMITTERS * REQUESTS_EACH
        if stats.generation != total:
            problems.append(f"generation {stats.generation} is not {total}")
        if stats.computed + stats.replaced != stats.generation:
            problems.append(
                f"computed {stats.computed} plus replaced {stats.replaced} "
                f"is not {stats.generation}")
        if stats.queued or stats.running:
            problems.append("work left after idle")
        if not taken or taken[-1].generation != total:
            problems.append("the newest request never became the answer")
        generations = [outcome.generation for outcome in taken]
        if generations != sorted(set(generations)):
            problems.append("taken generations repeat or go backwards")
        for outcome in taken:
            if outcome.value != tokens.get(outcome.generation):
                problems.append(f"generation {outcome.generation} holds another request's value")
                break
    return problems


class AdoptionStressTests(unittest.TestCase):
    def setUp(self):
        self._interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        self.addCleanup(sys.setswitchinterval, self._interval)

    def test_current_answer_is_never_replaced_by_an_older_completion(self):
        self.assertEqual(drive(LatestWorker), [])

    def test_a_missing_generation_check_is_visible_to_the_same_invariant(self):
        # A fixed interleaving, so the result does not depend on scheduling.
        # Request 2 returns while request 1 is still computing.
        def interleave(worker_class):
            first, release_first = threading.Event(), threading.Event()
            second, release_second = threading.Event(), threading.Event()

            def compute(payload):
                if payload == 1:
                    first.set()
                    release_first.wait(10)
                else:
                    second.set()
                    release_second.wait(10)
                return payload

            with worker_class(compute) as worker:
                worker.request(1)
                self.assertTrue(first.wait(10))
                newest = worker.request(2)
                release_first.set()
                # Request 1 has finished and request 2 is computing.
                self.assertTrue(second.wait(10))
                outcome = worker.take()
                release_second.set()
                self.assertTrue(worker.wait_idle(10))
                return newest, outcome

        newest, outcome = interleave(LatestWorker)
        self.assertIsNone(outcome)
        newest, outcome = interleave(_mutant_class())
        self.assertLess(outcome.generation, newest,
                        "the mutant should publish the stale completion")


if __name__ == "__main__":
    unittest.main()
