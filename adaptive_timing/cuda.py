"""Explicit optional CUDA adapter. Default imports need neither NumPy nor CUDA."""
import numpy as np

from .execution import OwnerExecutor


class CudaHistogramExecutor(OwnerExecutor):
    """Own snapshots at submit return, then run one synchronous CUDA context.

    The caller must exclude all external native writers during the submission
    copy. Mutation after successful submit returns cannot change admitted data.
    Capacity covers running, queued and completed unreconciled jobs.
    """

    def __init__(self, rows, cols, tile_rows, tile_cols, *, device=0, capacity=4):
        from heterogeneous_batch_runtime.cuda import CudaHistogramContext
        self._shape = (rows, cols)
        super().__init__(lambda: CudaHistogramContext(rows, cols, tile_rows, tile_cols,
                                                     device=device), capacity=capacity)

    def submit_image(self, dispatch, generation, image, *, gate=None):
        self._assert_owner()
        if not isinstance(image, np.ndarray) or image.dtype != np.dtype("uint8"):
            raise TypeError("exact uint8 NumPy array required")
        if image.ndim != 2 or image.shape != self._shape or not image.flags.c_contiguous:
            raise ValueError("fixed C contiguous two-dimensional shape required")
        def execute(context, cancel):
            if gate is not None:
                # Optional deterministic host gate for inspection, not a device gate.
                gate(cancel)
            return context.run(owned)

        # Reject full or closed sessions before allocating a submission copy.
        # This check reserves nothing. Submit checks again after copying, since
        # validation or allocation can run owner-thread code before admission.
        self._validate_submission(dispatch, generation, execute)
        # Strip subclass overrides before copying. A subclass copy method may
        # return itself or a view sharing the caller's mutable storage.
        owned = np.asarray(image).copy(order="C")
        self.submit_owned(dispatch, generation, execute)
