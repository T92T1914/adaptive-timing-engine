"""Owner-thread adoption with background computation and a virtual clock."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adaptive_timing import Controller, PlanningRequest
from adaptive_timing.experiment import workload


def main():
    controller = Controller()
    try:
        revision = controller.request(PlanningRequest(workload("burst", 120)))
        # A command-line example can wait; an interactive owner loop would keep
        # polling its previously installed plan while this calculation runs.
        if not controller.worker.wait_idle(2):
            raise RuntimeError("planning did not finish")
        results = []
        for tick in range(3000):
            results.extend(controller.poll(tick * 0.01, budget=32))
        if controller.last_error:
            raise RuntimeError(controller.last_error)
        print(f"Requested revision {revision}; adopted revision {controller.installed_revision}")
        print(f"Dispatched {sum(r.status == 'dispatched' for r in results)} tasks; "
              f"expired {sum(r.status == 'expired' for r in results)}")
    finally:
        controller.close(2)


if __name__ == "__main__":
    main()
