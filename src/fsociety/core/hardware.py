# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Hardware detection with explicit provenance.

Design rules:

* Never infer accelerator support from the operating system alone.
* Never raise because a tool is missing; record a warning instead.
* Distinguish *driver* availability (``nvidia-smi`` works) from *runtime*
  availability (torch was built with CUDA and sees devices).
* Keep every probe injectable so tests can simulate GPUs deterministically.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..utils.hashing import config_hash
from .models import CpuInfo, GpuInfo, HardwareProfile, TorchInfo, utcnow_iso

CommandRunner = Callable[[Sequence[str], float], "CommandResult"]


@dataclass
class CommandResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 1


def run_command(command: Sequence[str], timeout: float = 5.0) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        return CommandResult(
            ok=completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(ok=False, stderr=str(exc))


@dataclass
class DetectionOptions:
    probe_torch: bool = True
    probe_nvidia: bool = True
    probe_rocm: bool = True
    timeout_seconds: float = 5.0
    include_environment: bool = True
    extra_warnings: list[str] = field(default_factory=list)


def _read_first_line(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.readline().strip() or None
    except OSError:
        return None


def _meminfo_bytes(field_name: str) -> int | None:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(field_name + ":"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def _cpu_info() -> CpuInfo:
    model: str | None = None
    source = "unknown"
    try:
        import cpuinfo  # type: ignore[import-not-found]

        data = cpuinfo.get_cpu_info()
        model = data.get("brand_raw") or data.get("model") or data.get("brand") or None
        if model:
            source = "measured"
    except Exception:
        model = None

    if not model:
        if sys.platform.startswith("linux"):
            model = _read_first_line("/proc/cpuinfo")
        elif sys.platform == "darwin":
            model = _read_first_line("/proc/sys/kernel/osrelease")
    model = model or platform.processor() or None
    if model and source == "unknown":
        source = "reported"

    physical = logical = freq = None
    try:
        import psutil

        physical = psutil.cpu_count(logical=False)
        logical = psutil.cpu_count(logical=True)
        freq_data = psutil.cpu_freq()
        if freq_data:  # ty: ignore[redundant-condition] - psutil stubs omit the None case
            freq = round(float(freq_data.max or freq_data.current), 1)
    except Exception:
        logical = os.cpu_count()

    return CpuInfo(
        model=model,
        physical_cores=physical,
        logical_cores=logical,
        frequency_mhz=freq,
        architecture=platform.machine() or None,
        source="measured" if physical else source,  # type: ignore[arg-type]
        confidence="measured" if physical and model else "reported",
    )


def _nvidia_gpus(
    runner: CommandRunner, timeout: float
) -> tuple[list[GpuInfo], str | None]:
    if shutil.which("nvidia-smi") is None:
        return [], None
    query = "index,name,memory.total,memory.free,compute_cap,driver_version"
    result = runner(
        [
            "nvidia-smi",
            f"--query-gpu={query}",
            "--format=csv,noheader,nounits",
        ],
        timeout,
    )
    if not result.ok:
        return [], f"nvidia-smi failed: {result.stderr.strip()[:200]}"
    gpus: list[GpuInfo] = []
    for line in result.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            index = int(parts[0])
            total = int(float(parts[2])) * 1024 * 1024
            free = int(float(parts[3])) * 1024 * 1024
        except ValueError:
            continue
        gpus.append(
            GpuInfo(
                index=index,
                name=parts[1],
                backend="cuda",
                total_memory_bytes=total,
                free_memory_bytes=free,
                compute_capability=parts[4] if parts[4] not in {"", "N/A"} else None,
                driver_version=parts[5] or None,
                source="measured",
                confidence="measured",
            )
        )
    return gpus, None


def _rocm_gpus(
    runner: CommandRunner, timeout: float
) -> tuple[list[GpuInfo], str | None]:
    if shutil.which("rocm-smi") is None:
        return [], None
    result = runner(
        ["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--json"], timeout
    )
    if not result.ok:
        return [], f"rocm-smi failed: {result.stderr.strip()[:200]}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [], "rocm-smi returned non-JSON output"
    gpus: list[GpuInfo] = []
    for key, entry in sorted(payload.items()):
        if not isinstance(entry, dict) or not key.lower().startswith("card"):
            continue
        name = (
            entry.get("Card series")
            or entry.get("Card model")
            or entry.get("Card SKU")
            or key
        )
        total = entry.get("VRAM Total Memory (B)")
        used = entry.get("VRAM Total Used Memory (B)")
        try:
            total_int = int(total) if total is not None else None
            used_int = int(used) if used is not None else None
        except (TypeError, ValueError):
            total_int = used_int = None
        gpus.append(
            GpuInfo(
                index=len(gpus),
                name=str(name),
                backend="rocm",
                total_memory_bytes=total_int,
                free_memory_bytes=(
                    total_int - used_int
                    if total_int is not None and used_int is not None
                    else None
                ),
                source="measured",
                confidence="measured",
            )
        )
    return gpus, None


def _torch_probe() -> TorchInfo:
    info = TorchInfo()
    if importlib.util.find_spec("torch") is None:
        info.error = "torch is not installed"
        return info
    try:
        import torch

        info.installed = True
        info.version = getattr(torch, "__version__", None)
        info.cuda_available = bool(torch.cuda.is_available())
        info.cuda_version = getattr(torch.version, "cuda", None)
        info.device_count = int(torch.cuda.device_count()) if info.cuda_available else 0
        try:
            info.mps_available = bool(
                hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
            )
        except Exception:
            info.mps_available = False
        supported: list[str] = []
        for dtype in ("float32", "float16", "bfloat16"):
            try:
                getattr(torch, dtype)
                supported.append(dtype)
            except AttributeError:
                continue
        info.dtype_support = supported
    except Exception as exc:  # torch present but broken (driver/ABI mismatch)
        info.error = f"{type(exc).__name__}: {str(exc)[:300]}"
    return info


def _torch_gpu_enrichment(
    gpus: list[GpuInfo], timeout: float
) -> tuple[list[GpuInfo], str | None]:
    """Fill memory/compute info from torch when nvidia-smi was unavailable."""
    if gpus:
        return gpus, None
    if importlib.util.find_spec("torch") is None:
        return gpus, None
    try:
        import torch

        if not torch.cuda.is_available():
            return gpus, None
        for index in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(index)
            free = total = int(props.total_memory)
            try:
                free_info = torch.cuda.mem_get_info(index)
                free = int(free_info[0])
            except Exception:
                pass
            backend = "cuda"
            if getattr(torch.version, "hip", None):
                backend = "rocm"
            gpus.append(
                GpuInfo(
                    index=index,
                    name=props.name,
                    backend=backend,
                    total_memory_bytes=total,
                    free_memory_bytes=free,
                    compute_capability=(
                        f"{props.major}.{props.minor}"
                        if hasattr(props, "major")
                        else None
                    ),
                    source="measured",
                    confidence="measured",
                )
            )
        return gpus, None
    except Exception as exc:
        return gpus, f"torch GPU probe failed: {type(exc).__name__}: {str(exc)[:200]}"


def detect_hardware(
    options: DetectionOptions | None = None,
    *,
    runner: CommandRunner = run_command,
    torch_probe: Callable[[], TorchInfo] | None = None,
) -> HardwareProfile:
    """Detect the current machine's capabilities."""
    options = options or DetectionOptions()
    warnings: list[str] = list(options.extra_warnings)

    cpu = _cpu_info()
    ram_total = ram_available = swap_total = None
    try:
        import psutil

        vm = psutil.virtual_memory()
        ram_total = int(vm.total)
        ram_available = int(vm.available)
        swap = psutil.swap_memory()
        swap_total = int(swap.total)
    except Exception:
        if sys.platform.startswith("linux"):
            ram_total = _meminfo_bytes("MemTotal")
            ram_available = _meminfo_bytes("MemAvailable")
            swap_total = _meminfo_bytes("SwapTotal")
        if ram_total is None:
            warnings.append("RAM size could not be determined (psutil unavailable).")

    gpus: list[GpuInfo] = []
    driver_warnings: list[str] = []
    if options.probe_nvidia:
        nvidia, error = _nvidia_gpus(runner, options.timeout_seconds)
        gpus.extend(nvidia)
        if error:
            driver_warnings.append(error)
    if options.probe_rocm:
        rocm, error = _rocm_gpus(runner, options.timeout_seconds)
        if rocm:
            gpus.extend(rocm)
        if error:
            driver_warnings.append(error)

    torch_info = (torch_probe or _torch_probe)() if options.probe_torch else TorchInfo()
    if torch_info.installed:
        enriched, error = _torch_gpu_enrichment(gpus, options.timeout_seconds)
        gpus = enriched
        if error:
            driver_warnings.append(error)

    if torch_info.mps_available and platform.system() == "Darwin" and not gpus:
        # Unified memory: report available RAM as the accelerator budget.
        gpus.append(
            GpuInfo(
                index=0,
                name=f"Apple Silicon ({platform.machine()})",
                backend="mps",
                total_memory_bytes=ram_total,
                free_memory_bytes=ram_available,
                source="measured",
                confidence="measured",
            )
        )

    if not torch_info.installed:
        warnings.append(
            "PyTorch is not installed: accelerator runtime capabilities could not be verified. "
            "Detected GPUs reflect driver-level information only."
        )
    elif torch_info.error:
        warnings.append(f"PyTorch probe failed: {torch_info.error}")
    if driver_warnings:
        warnings.extend(driver_warnings)
    if not gpus:
        warnings.append(
            "No accelerator detected; the platform will operate in CPU-only mode."
        )

    backends = {
        "cpu": True,
        "cuda_driver": any(g.backend == "cuda" for g in gpus),
        "rocm_driver": any(g.backend == "rocm" for g in gpus),
        "cuda_torch": torch_info.cuda_available,
        "mps_torch": torch_info.mps_available,
        "xpu_torch": False,
    }
    try:
        if importlib.util.find_spec("torch") is not None:
            import torch

            if hasattr(torch, "xpu"):
                backends["xpu_torch"] = bool(torch.xpu.is_available())
    except Exception:
        pass

    profile = HardwareProfile(
        captured_at=utcnow_iso(),
        hostname=platform.node() or "unknown",
        os=platform.system() or "unknown",
        os_release=platform.release() or "unknown",
        architecture=platform.machine() or "unknown",
        python_version=platform.python_version(),
        cpu=cpu,
        ram_total_bytes=ram_total,
        ram_available_bytes=ram_available,
        swap_total_bytes=swap_total,
        gpus=gpus,
        backends=backends,
        torch=torch_info,
        detection_mode="measured" if ram_total and cpu.model else "partial",
        warnings=warnings,
    )
    profile.fingerprint = hardware_fingerprint(profile)
    return profile


def hardware_fingerprint(profile: HardwareProfile) -> str:
    """Stable identity of a machine's *capabilities* (volatile values excluded)."""
    stable = {
        "hostname": profile.hostname,
        "os": profile.os,
        "os_release": profile.os_release,
        "arch": profile.architecture,
        "cpu": profile.cpu.model,
        "physical_cores": profile.cpu.physical_cores,
        "ram_total": profile.ram_total_bytes,
        "gpus": [
            {
                "name": g.name,
                "backend": g.backend,
                "total": g.total_memory_bytes,
                "cc": g.compute_capability,
            }
            for g in profile.gpus
        ],
        "torch": profile.torch.version,
        "cuda": profile.torch.cuda_version,
    }
    return config_hash(stable)[:32]


def primary_device(profile: HardwareProfile) -> str:
    """Best device for a single-device workload."""
    if profile.backends.get("cuda_torch"):
        return "cuda"
    if profile.backends.get("mps_torch"):
        return "mps"
    return "cpu"


def accelerator_budget_bytes(profile: HardwareProfile) -> int | None:
    """Largest single accelerator memory pool, or None for CPU/unified memory."""
    accel = [g.total_memory_bytes or 0 for g in profile.gpus if g.backend != "cpu"]
    if accel:
        return max(accel)
    return None
