# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Model backends for evaluation runs.

Three real backends are supported:

* :class:`CallableBackend` — an explicit integration point for callers that
  already own a model loop (plugins, tests, notebooks). The callable receives a
  list of prompts and must return one string per prompt.
* :class:`TransformersBackend` — Hugging Face ``transformers``/PyTorch with
  deterministic greedy generation and the platform trust policy enforced.
* :class:`LlamaCppBackend` — local GGUF inference through ``llama-cpp-python``.

Heavy imports (torch, transformers, llama_cpp) happen lazily inside
``__init__`` so importing :mod:`fsociety.evaluation` never requires them. There
is deliberately no "fake" backend in production code: fabricated model output
is never a valid evaluation result.
"""

from __future__ import annotations

import importlib.util
import random
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Union

from pydantic import Field, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from ..config import PlatformSettings
from ..core.models import StrictModel
from ..errors import (
    EngineUnavailableError,
    EvaluationError,
    ValidationError,
)

_INSTALL_HINT = (
    "Install the engine profile: `pip install fsociety-ai` (includes torch and "
    "transformers) or `uv sync` in a source checkout."
)
_LLAMA_HINT = "Install the llama.cpp extra: `pip install 'fsociety-ai[llama]'`."
_DTYPE_NAMES = ("auto", "float32", "float16", "bfloat16")


def _validate_prompts(prompts: list[str], max_new_tokens: int) -> list[str]:
    if not isinstance(prompts, (list, tuple)) or not prompts:
        raise ValidationError("generate() requires a non-empty list of prompts.")
    cleaned: list[str] = []
    for index, prompt in enumerate(prompts):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValidationError(
                "Every prompt must be a non-empty string.",
                details={"index": index, "type": type(prompt).__name__},
            )
        cleaned.append(prompt)
    if not isinstance(max_new_tokens, int) or isinstance(max_new_tokens, bool):
        raise ValidationError("max_new_tokens must be an integer.")
    if max_new_tokens < 1:
        raise ValidationError("max_new_tokens must be >= 1.")
    return cleaned


class ModelBackend(ABC):
    """Common contract for every evaluation backend."""

    name: str = "abstract"
    model_id: str = "unknown"

    @abstractmethod
    def generate(self, prompts: list[str], max_new_tokens: int) -> list[str]:
        """Return exactly one generated string per prompt, in order."""

    def metadata(self) -> dict[str, Any]:
        """Optional, JSON-serializable provenance about the backend."""
        return {"kind": self.name, "model_id": self.model_id}

    def close(self) -> None:
        """Release model resources. Safe to call more than once."""
        return None


class CallableBackend(ModelBackend):
    """Adapter around a caller-provided ``fn(prompts) -> responses`` callable.

    Exceptions raised by ``fn`` propagate to the runner, which isolates them per
    item. This backend does not synthesize responses: if the callable fails,
    the item is recorded as errored.
    """

    def __init__(
        self,
        fn: Callable[[list[str]], list[str]],
        name: str = "callable",
        model_id: str = "callable",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not callable(fn):
            raise ValidationError("CallableBackend requires a callable.")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("CallableBackend requires a non-empty name.")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValidationError("CallableBackend requires a non-empty model_id.")
        self._fn = fn
        self.name = name.strip()
        self.model_id = model_id.strip()
        self._metadata = dict(metadata or {})

    def generate(self, prompts: list[str], max_new_tokens: int) -> list[str]:
        cleaned = _validate_prompts(prompts, max_new_tokens)
        output = self._fn(list(cleaned))
        if not isinstance(output, (list, tuple)):
            raise EvaluationError(
                "CallableBackend callable must return a list of strings, got "
                f"{type(output).__name__}.",
                details={"model_id": self.model_id},
            )
        if len(output) != len(cleaned):
            raise EvaluationError(
                "CallableBackend returned the wrong number of responses.",
                details={"expected": len(cleaned), "received": len(output)},
            )
        responses: list[str] = []
        for index, value in enumerate(output):
            if not isinstance(value, str):
                raise EvaluationError(
                    "CallableBackend responses must be strings.",
                    details={"index": index, "type": type(value).__name__},
                )
            responses.append(value)
        return responses

    def metadata(self) -> dict[str, Any]:
        payload = {"kind": "callable", "model_id": self.model_id, "name": self.name}
        payload.update(self._metadata)
        return payload


def _require_modules(modules: tuple[str, ...], *, backend: str) -> dict[str, Any]:
    missing = [module for module in modules if importlib.util.find_spec(module) is None]
    if missing:
        raise EngineUnavailableError(
            f"Evaluation backend {backend!r} requires missing modules: {', '.join(missing)}.",
            details={"backend": backend, "missing": missing},
            hint=_INSTALL_HINT if "llama_cpp" not in missing else _LLAMA_HINT,
        )
    imported: dict[str, Any] = {}
    try:
        for module in modules:
            imported[module] = importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - broken installs must surface clearly
        raise EngineUnavailableError(
            f"Evaluation backend {backend!r} could not import {', '.join(modules)}: "
            f"{type(exc).__name__}: {exc}",
            details={"backend": backend, "missing": missing},
            hint=_INSTALL_HINT if "llama_cpp" not in modules else _LLAMA_HINT,
        ) from exc
    return imported


def _seed_everything(torch_module: Any, seed: int) -> None:
    torch_module.manual_seed(seed)
    if hasattr(torch_module, "cuda") and torch_module.cuda.is_available():
        try:
            torch_module.cuda.manual_seed_all(seed)
        except Exception:  # pragma: no cover - defensive for exotic builds
            pass
    random.seed(seed)
    if importlib.util.find_spec("numpy") is not None:
        import numpy

        numpy.random.seed(seed % (2**32))


def _resolve_device(torch_module: Any, requested: str | None) -> str:
    if requested:
        return requested
    if torch_module.cuda.is_available():
        return "cuda"
    mps = getattr(torch_module.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def _resolve_dtype(torch_module: Any, name: str | None) -> Any:
    resolved = (name or "auto").lower()
    if resolved not in _DTYPE_NAMES:
        raise ValidationError(
            f"Unsupported dtype {name!r}.",
            details={"allowed": list(_DTYPE_NAMES)},
        )
    if resolved == "auto":
        return None
    return getattr(torch_module, resolved)


def _model_load_kwargs(
    transformers_module: Any, dtype_value: Any, revision: str | None
) -> dict[str, Any]:
    """Handle the transformers 5 ``dtype`` rename without guessing blindly."""
    kwargs: dict[str, Any] = {}
    if revision:
        kwargs["revision"] = revision
    if dtype_value is None:
        return kwargs
    major = 0
    try:
        major = int(str(getattr(transformers_module, "__version__", "0")).split(".")[0])
    except (TypeError, ValueError):  # pragma: no cover - exotic version strings
        major = 0
    kwargs["dtype" if major >= 5 else "torch_dtype"] = dtype_value
    return kwargs


class TransformersBackend(ModelBackend):
    """Hugging Face causal-LM backend with deterministic generation."""

    name = "transformers"

    def __init__(
        self,
        model_id: str,
        settings: PlatformSettings | None = None,
        device: str | None = None,
        dtype: str | None = None,
        revision: str | None = None,
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValidationError("TransformersBackend requires a non-empty model_id.")
        self.model_id = model_id.strip()
        self._settings = settings or PlatformSettings()
        self._requested_device = device
        self._requested_dtype = dtype or self._settings.default_dtype
        self._revision = revision
        self._torch: Any = None
        self._tokenizer: Any = None
        self._model: Any = None
        self._device = "cpu"
        self._dtype = "auto"
        self._trust_remote_code = self._settings.remote_code_allowed(self.model_id)
        self._closed = False
        self._load()

    # ---- lifecycle ---------------------------------------------------------
    def _load(self) -> None:
        modules = _require_modules(("torch", "transformers"), backend="transformers")
        torch_module = modules["torch"]
        transformers_module = modules["transformers"]
        self._torch = torch_module

        seed = int(self._settings.evaluation_seed)
        _seed_everything(torch_module, seed)

        dtype_value = _resolve_dtype(torch_module, self._requested_dtype)
        self._dtype = self._requested_dtype
        self._device = _resolve_device(torch_module, self._requested_device)

        try:
            tokenizer = transformers_module.AutoTokenizer.from_pretrained(
                self.model_id,
                revision=self._revision,
                trust_remote_code=self._trust_remote_code,
            )
            tokenizer.padding_side = "left"
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token
            load_kwargs = _model_load_kwargs(
                transformers_module, dtype_value, self._revision
            )
            model = transformers_module.AutoModelForCausalLM.from_pretrained(
                self.model_id,
                trust_remote_code=self._trust_remote_code,
                **load_kwargs,
            )
            model.to(self._device)
            model.eval()
            if hasattr(model, "requires_grad_"):
                model.requires_grad_(False)
        except Exception as exc:  # noqa: BLE001 - load failures need actionable wrapping
            raise EngineUnavailableError(
                f"Could not load model {self.model_id!r} with the transformers backend: "
                f"{type(exc).__name__}: {str(exc)[:400]}",
                details={
                    "model_id": self.model_id,
                    "device": self._device,
                    "revision": self._revision,
                    "trust_remote_code": self._trust_remote_code,
                },
                hint=(
                    "Verify the model id/revision, that the weights are available offline, "
                    "and the trust policy if the model requires remote code."
                ),
            ) from exc

        self._tokenizer = tokenizer
        self._model = model

    def generate(self, prompts: list[str], max_new_tokens: int) -> list[str]:
        if self._closed or self._model is None or self._tokenizer is None:
            raise EvaluationError(
                "TransformersBackend is closed; create a new backend instance."
            )
        cleaned = _validate_prompts(prompts, max_new_tokens)
        torch_module = self._torch
        tokenizer = self._tokenizer
        model = self._model
        temperature = float(self._settings.temperature)
        try:
            encoded = tokenizer(
                cleaned,
                return_tensors="pt",
                padding=True,
                truncation=True,
            )
            encoded = {
                key: value.to(self._device) if hasattr(value, "to") else value
                for key, value in encoded.items()
            }
            input_length = int(encoded["input_ids"].shape[1])
            generation_kwargs: dict[str, Any] = {
                "max_new_tokens": int(max_new_tokens),
                "do_sample": temperature > 0,
                "pad_token_id": tokenizer.pad_token_id,
                "eos_token_id": tokenizer.eos_token_id,
            }
            if temperature > 0:
                generation_kwargs["temperature"] = temperature
            with torch_module.inference_mode():
                output = model.generate(**encoded, **generation_kwargs)
            new_tokens = output[:, input_length:]
            decoded = tokenizer.batch_decode(new_tokens, skip_special_tokens=True)
            return [str(text).strip() for text in decoded]
        finally:
            self._empty_cache()

    def _empty_cache(self) -> None:
        if self._torch is None:
            return
        try:
            if hasattr(self._torch, "cuda") and self._torch.cuda.is_available():
                self._torch.cuda.empty_cache()
        except Exception:  # pragma: no cover - driver dependent
            pass

    def close(self) -> None:
        self._model = None
        self._tokenizer = None
        self._closed = True
        self._empty_cache()

    def metadata(self) -> dict[str, Any]:
        transformers_version = None
        if importlib.util.find_spec("transformers") is not None:
            try:
                import transformers

                transformers_version = getattr(transformers, "__version__", None)
            except Exception:  # pragma: no cover - broken install
                transformers_version = None
        return {
            "kind": "transformers",
            "model_id": self.model_id,
            "revision": self._revision,
            "device": self._device,
            "dtype": self._dtype,
            "seed": int(self._settings.evaluation_seed),
            "temperature": float(self._settings.temperature),
            "deterministic": float(self._settings.temperature) == 0.0,
            "trust_remote_code": self._trust_remote_code,
            "torch_version": getattr(self._torch, "__version__", None),
            "transformers_version": transformers_version,
        }


class LlamaCppBackend(ModelBackend):
    """Local GGUF generation through ``llama-cpp-python``."""

    name = "llama_cpp"

    def __init__(
        self,
        model_path: str,
        n_ctx: int = 2048,
        n_gpu_layers: int = 0,
        seed: int = 42,
        temperature: float = 0.0,
    ) -> None:
        if not isinstance(model_path, str) or not model_path.strip():
            raise ValidationError("LlamaCppBackend requires a non-empty model_path.")
        path = Path(model_path).expanduser()
        if not path.is_file():
            raise ValidationError(
                f"GGUF model file not found: {path}",
                hint="Pass the path to an existing .gguf file.",
            )
        if not isinstance(n_ctx, int) or isinstance(n_ctx, bool) or n_ctx < 64:
            raise ValidationError("n_ctx must be an integer >= 64.")
        if not isinstance(n_gpu_layers, int) or isinstance(n_gpu_layers, bool):
            raise ValidationError("n_gpu_layers must be an integer.")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ValidationError("seed must be a non-negative integer.")
        if not isinstance(temperature, (int, float)) or temperature < 0:
            raise ValidationError("temperature must be a non-negative number.")

        self.model_id = str(path)
        self._model_path = str(path)
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers
        self._seed = seed
        self._temperature = float(temperature)
        self._llm: Any = None
        self._closed = False

        modules = _require_modules(("llama_cpp",), backend="llama_cpp")
        llama_cpp = modules["llama_cpp"]
        try:
            self._llm = llama_cpp.Llama(
                model_path=self._model_path,
                n_ctx=n_ctx,
                n_gpu_layers=n_gpu_layers,
                seed=seed,
                verbose=False,
            )
        except Exception as exc:  # noqa: BLE001 - load failures need actionable wrapping
            raise EngineUnavailableError(
                f"Could not load GGUF model {self._model_path!r}: "
                f"{type(exc).__name__}: {str(exc)[:400]}",
                details={"model_path": self._model_path, "n_ctx": n_ctx},
                hint="Verify the GGUF file is complete and compatible with llama-cpp-python.",
            ) from exc

    def generate(self, prompts: list[str], max_new_tokens: int) -> list[str]:
        if self._closed or self._llm is None:
            raise EvaluationError(
                "LlamaCppBackend is closed; create a new backend instance."
            )
        cleaned = _validate_prompts(prompts, max_new_tokens)
        responses: list[str] = []
        for prompt in cleaned:
            result = self._llm(
                prompt,
                max_tokens=int(max_new_tokens),
                temperature=self._temperature,
                top_p=1.0,
                echo=False,
            )
            try:
                text = result["choices"][0]["text"]
            except (KeyError, IndexError, TypeError) as exc:
                raise EvaluationError(
                    "llama.cpp returned an unexpected payload.",
                    details={
                        "keys": list(result) if isinstance(result, dict) else None
                    },
                ) from exc
            responses.append(str(text).strip())
        return responses

    def close(self) -> None:
        self._llm = None
        self._closed = True

    def metadata(self) -> dict[str, Any]:
        return {
            "kind": "llama_cpp",
            "model_id": self.model_id,
            "model_path": self._model_path,
            "n_ctx": self._n_ctx,
            "n_gpu_layers": self._n_gpu_layers,
            "seed": self._seed,
            "temperature": self._temperature,
            "deterministic": self._temperature == 0.0,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TransformersBackendSpec(StrictModel):
    kind: Literal["transformers"]
    model_id: str = Field(min_length=1, max_length=400)
    device: str | None = Field(default=None, max_length=64)
    dtype: Literal["auto", "float32", "float16", "bfloat16"] | None = None
    revision: str | None = Field(default=None, max_length=200)


class LlamaCppBackendSpec(StrictModel):
    kind: Literal["llama_cpp"]
    path: str = Field(min_length=1, max_length=4096)
    n_ctx: int = Field(default=2048, ge=64, le=1_048_576)
    n_gpu_layers: int = Field(default=0, ge=-1, le=4096)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)


BackendSpec = Annotated[
    Union[TransformersBackendSpec, LlamaCppBackendSpec],
    Field(discriminator="kind"),
]
_SPEC_ADAPTER = TypeAdapter(BackendSpec)


def load_backend(
    spec: dict[str, Any], settings: PlatformSettings | None = None
) -> ModelBackend:
    """Build a backend from a validated specification.

    ``spec`` must be ``{"kind": "transformers", "model_id": ...}`` or
    ``{"kind": "llama_cpp", "path": ...}``. Unknown kinds and unknown keys are
    rejected with a typed :class:`ValidationError`.
    """
    if not isinstance(spec, dict):
        raise ValidationError(
            "Backend specification must be a JSON object.",
            details={"received": type(spec).__name__},
        )
    try:
        parsed = _SPEC_ADAPTER.validate_python(spec)
    except PydanticValidationError as exc:
        raise ValidationError(
            "Invalid backend specification.",
            details={
                "errors": exc.errors(include_url=False, include_context=False),
                "allowed_kinds": ["transformers", "llama_cpp"],
            },
            hint=(
                'Use {"kind": "transformers", "model_id": "org/model"} or '
                '{"kind": "llama_cpp", "path": "/abs/model.gguf"}.'
            ),
        ) from exc

    if isinstance(parsed, TransformersBackendSpec):
        return TransformersBackend(
            parsed.model_id,
            settings=settings,
            device=parsed.device,
            dtype=parsed.dtype,
            revision=parsed.revision,
        )
    return LlamaCppBackend(
        parsed.path,
        n_ctx=parsed.n_ctx,
        n_gpu_layers=parsed.n_gpu_layers,
        seed=parsed.seed,
    )


def backend_display_name(backend: ModelBackend) -> str:
    """Human-readable backend label used in reports and logs."""
    return f"{getattr(backend, 'name', 'unknown')}:{getattr(backend, 'model_id', 'unknown')}"


__all__ = [
    "BackendSpec",
    "CallableBackend",
    "LlamaCppBackend",
    "LlamaCppBackendSpec",
    "ModelBackend",
    "TransformersBackend",
    "TransformersBackendSpec",
    "backend_display_name",
    "load_backend",
]
