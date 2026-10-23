"""Typed reads out of numpy arrays.

numpy's own stubs type a scalar read (`a[i, j]`), a row read (`a[i]`) and an
array's shape as `Any`, so code that does arithmetic on single values loses
its types there. The engine's few such reads go through these functions, each
with its suppression and the reason, so the code around them stays typed.
"""

import numpy as np
import numpy.typing as npt

type Floats = npt.NDArray[np.float64]


def number(array: Floats, *index: int) -> float:
    """One element of a float array."""
    return float(array[index])  # pyright: ignore[reportAny]  # numpy types a scalar read as Any


def whole(array: npt.NDArray[np.int64], *index: int) -> int:
    """One element of an integer array."""
    return int(array[index])  # pyright: ignore[reportAny]  # numpy types a scalar read as Any


def flag(array: npt.NDArray[np.bool_], *index: int) -> bool:
    """One element of a boolean array."""
    return bool(array[index])  # pyright: ignore[reportAny]  # numpy types a scalar read as Any


def row(array: Floats, index: int) -> Floats:
    """One row of a 2-D float array."""
    found: Floats = array[index]  # pyright: ignore[reportAny]  # numpy types a row read as Any
    return found


def size(array: npt.NDArray[np.generic], axis: int) -> int:
    """The array's length along `axis`."""
    return int(array.shape[axis])  # pyright: ignore[reportAny]  # numpy types a shape's lengths as Any


def float_list(array: npt.NDArray[np.float32]) -> list[float]:
    """A 1-D float array as a list of Python floats."""
    return array.tolist()  # pyright: ignore[reportAny]  # numpy types tolist() as Any


def int_list(
    array: npt.NDArray[np.int64] | npt.NDArray[np.int32] | npt.NDArray[np.uint32],
) -> list[int]:
    """A 1-D integer array as a list of Python ints."""
    return array.tolist()  # pyright: ignore[reportAny]  # numpy types tolist() as Any
