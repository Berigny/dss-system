"""Hardware and accelerator profiling for benchmark reproducibility."""

from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HardwareProfile:
    """Neutral, dependency-light view of the runtime hardware environment."""

    platform: str
    processor: str | None = None
    cpu_count: int | None = None
    memory_gb: float | None = None
    gpu_name: str | None = None
    gpu_count: int | None = None
    cuda_version: str | None = None
    mps_available: bool = False
    backend_accelerator: str = "cpu"

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "processor": self.processor,
            "cpu_count": self.cpu_count,
            "memory_gb": self.memory_gb,
            "gpu_name": self.gpu_name,
            "gpu_count": self.gpu_count,
            "cuda_version": self.cuda_version,
            "mps_available": self.mps_available,
            "backend_accelerator": self.backend_accelerator,
        }


def _cpu_count() -> int | None:
    try:
        return os.cpu_count()
    except Exception:
        return None


def _processor_brand() -> str | None:
    try:
        brand = platform.processor()
        return brand if brand else None
    except Exception:
        return None


def _memory_gb() -> float | None:
    try:
        import psutil

        return round(psutil.virtual_memory().total / (1024**3), 2)
    except Exception:
        return None


def _torch_cuda_info() -> tuple[str | None, int, str | None]:
    """Return (gpu_name, gpu_count, cuda_version) when torch.cuda is available."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None, 0, None
        gpu_name = torch.cuda.get_device_name(0) or None
        gpu_count = torch.cuda.device_count()
        cuda_version = torch.version.cuda
        return gpu_name, gpu_count, cuda_version
    except Exception:
        return None, 0, None


def _torch_mps_available() -> bool:
    try:
        import torch

        return bool(torch.backends.mps.is_available())
    except Exception:
        return False


def _git_commit_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent.parent.parent)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def detect_hardware_profile() -> HardwareProfile:
    """Detect the current hardware and accelerator environment.

    The implementation is defensive: optional dependencies (``psutil``, ``torch``)
    are used when present, but the function always returns a usable profile.
    """
    gpu_name, gpu_count, cuda_version = _torch_cuda_info()
    mps_available = _torch_mps_available()

    if gpu_name:
        backend_accelerator = "cuda"
    elif mps_available:
        backend_accelerator = "mps"
    else:
        backend_accelerator = "cpu"

    return HardwareProfile(
        platform=f"{platform.system()}-{platform.release()}-{platform.machine()}",
        processor=_processor_brand(),
        cpu_count=_cpu_count(),
        memory_gb=_memory_gb(),
        gpu_name=gpu_name,
        gpu_count=gpu_count if gpu_count > 0 else None,
        cuda_version=cuda_version,
        mps_available=mps_available,
        backend_accelerator=backend_accelerator,
    )
