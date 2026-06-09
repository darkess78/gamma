from __future__ import annotations

import unittest

from gamma.system.torch_devices import resolve_torch_device


class _FakeCuda:
    def __init__(self, available: bool, count: int, free_bytes: list[int] | None = None) -> None:
        self._available = available
        self._count = count
        self._free_bytes = free_bytes or [1] * max(count, 1)

    def is_available(self) -> bool:
        return self._available

    def device_count(self) -> int:
        return self._count

    def mem_get_info(self, index: int) -> tuple[int, int]:
        return self._free_bytes[index], self._free_bytes[index] * 2


class _FakeTorch:
    def __init__(self, available: bool, count: int, free_bytes: list[int] | None = None) -> None:
        self.cuda = _FakeCuda(available, count, free_bytes)


class TorchDeviceResolutionTest(unittest.TestCase):
    def test_auto_picks_cpu_when_cuda_missing(self) -> None:
        resolved, warning = resolve_torch_device("auto", torch_module=_FakeTorch(False, 0))
        self.assertEqual(resolved, "cpu")
        self.assertIsNone(warning)

    def test_auto_picks_most_free_cuda_device(self) -> None:
        resolved, warning = resolve_torch_device("auto", torch_module=_FakeTorch(True, 3, [100, 500, 200]))
        self.assertEqual(resolved, "cuda:1")
        self.assertIsNone(warning)

    def test_requested_cuda_falls_back_to_cpu_without_gpu(self) -> None:
        resolved, warning = resolve_torch_device("cuda:1", torch_module=_FakeTorch(False, 0))
        self.assertEqual(resolved, "cpu")
        self.assertIn("falling back to CPU", warning or "")

    def test_requested_out_of_range_cuda_index_falls_back_to_best(self) -> None:
        resolved, warning = resolve_torch_device("cuda:9", torch_module=_FakeTorch(True, 2, [900, 100]))
        self.assertEqual(resolved, "cuda:0")
        self.assertIn("unavailable", warning or "")

    def test_preferred_index_is_used_for_generic_cuda_requests(self) -> None:
        resolved, warning = resolve_torch_device("cuda", preferred_index=1, torch_module=_FakeTorch(True, 2))
        self.assertEqual(resolved, "cuda:1")
        self.assertIsNone(warning)


if __name__ == "__main__":
    unittest.main()
