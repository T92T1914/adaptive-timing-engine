"""A pass that keeps at most `depth` chunks queued on its stream.

The caller gives the way to launch a chunk and to record an event after it, so this file
imports nothing from the card and its order of work can be tested without one.
"""
from collections import deque


def run_depth(chunk_list, value, depth, run_chunk, new_event, stale=None):
    """Run the chunks with at most `depth` of them not yet known to have finished.

    `depth` of None never waits and queues every chunk. Between two launches the pass
    first drops the events that an event query, which does not wait, reports as done. It
    then waits for the oldest event only if the queue is full. After that it asks `stale`,
    and a stale pass launches nothing more. This is the calculation stopping itself. The
    chunks already queued stay on the stream and run to their end.

    `new_event` returns an event recorded on the stream after the launch just made, with
    `query()` and `synchronize()`. Returns the last value launched, the events in launch
    order and the number of waits. The caller closes the pass with its own synchronize.
    """
    if depth is not None and depth < 1:
        raise ValueError("the depth must be at least 1")
    pending = deque()
    events = []
    waits = 0
    for position, chunk in enumerate(chunk_list):
        while pending and pending[0].query():
            pending.popleft()
        if depth is not None and len(pending) >= depth:
            pending.popleft().synchronize()
            waits += 1
        if position and stale is not None and stale():
            break
        value = run_chunk(chunk, value)
        event = new_event()
        pending.append(event)
        events.append(event)
    return value, events, waits
