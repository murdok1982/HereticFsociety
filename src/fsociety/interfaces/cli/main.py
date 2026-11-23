# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""FSOCIETY AI command line interface.

The CLI is intentionally usable without PyTorch: inspection, planning,
experiments, plugins, security and the API work everywhere; model modification
and real model evaluation require the engine profile.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from ... import PLUGIN_API_VERSION, __platform_name__, __version__
from ...config import PlatformSettings, load_settings, write_default_config
from ...context import PlatformContext, software_versions
from ...errors import FSocietyError, ValidationError
from ...paths import safe_join
from ...security.redaction import redact

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _console():
    from rich.console import Console

    return Console(highlight=False)


def _emit_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    from rich.table import Table

    table = Table(title=title, header_style="bold")
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*row)
    _console().print(table)


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def _fmt_params(value: int | None) -> str:
    if not value:
        return "unknown"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    return str(value)


def _parse_json_argument(value: str) -> Any:
    if value == "-":
        return json.load(sys.stdin)
    if not value.lstrip().startswith(("{", "[")):
        path = Path(value)
        if not path.is_file():
            raise ValidationError(f"JSON file not found: {value}")
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _parse_key_values(pairs: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not key.strip():
            raise ValidationError(f"Expected key=value, got {pair!r}")
        result[key.strip()] = value.strip()
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_version(ctx: PlatformContext, args: argparse.Namespace) -> int:
    info = ctx.system_info().model_dump(mode="json")
    info["engine"] = ctx.engine_status()
    if args.json:
        _emit_json(info)
    else:
        console = _console()
        console.print(f"[bold]{__platform_name__}[/] v{__version__}")
        console.print(f"Plugin API: v{PLUGIN_API_VERSION}")
        console.print(f"Workspace : {ctx.workspace.root}")
        console.print(f"Engine    : {info['engine']['message']}")
    return EXIT_OK


def cmd_doctor(ctx: PlatformContext, args: argparse.Namespace) -> int:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    add(
        "python",
        "ok" if sys.version_info >= (3, 10) else "fail",
        sys.version.split()[0],
    )
    try:
        ctx.workspace.data
        probe = ctx.workspace.data / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("workspace", "ok", str(ctx.workspace.root))
    except OSError as exc:
        add("workspace", "fail", f"not writable: {exc}")
    try:
        stats = ctx.store.statistics()
        add("database", "ok", f"{sum(stats['experiments'].values())} experiments")
    except Exception as exc:  # noqa: BLE001 - diagnostics must report, not raise
        add("database", "fail", f"{type(exc).__name__}: {exc}")

    hardware = ctx.hardware(refresh=args.refresh)
    add(
        "hardware",
        "ok" if hardware.detection_mode == "measured" else "warn",
        hardware.summary(),
    )
    for warning in hardware.warnings[:3]:
        add("hardware-warning", "warn", warning)

    engine = ctx.engine_status()
    add(
        "engine",
        "ok" if engine["available"] else "warn",
        engine["message"]
        if not engine["available"]
        else f"heretic {engine.get('heretic_version')}",
    )

    dist = Path(__file__).resolve().parents[4] / "frontend" / "dist" / "index.html"
    add(
        "dashboard",
        "ok" if dist.is_file() else "warn",
        str(dist) if dist.is_file() else "frontend build not found (optional)",
    )
    add(
        "api_auth",
        "ok" if ctx.settings.api_key else "warn",
        "API key configured" if ctx.settings.api_key else "loopback only (no API key)",
    )
    add(
        "trust_policy",
        "ok" if ctx.settings.trust_remote_code != "always" else "fail",
        ctx.settings.trust_remote_code,
    )
    try:
        from ...plugins.registry import PluginRegistry

        plugins = PluginRegistry(
            [ctx.workspace.plugins], logger=ctx.logger, settings=ctx.settings
        ).list()
        invalid = [p.name for p in plugins if not p.valid]
        add(
            "plugins",
            "ok" if not invalid else "warn",
            f"{len(plugins)} discovered, {len(invalid)} invalid",
        )
    except Exception as exc:  # noqa: BLE001
        add("plugins", "warn", f"{type(exc).__name__}: {exc}")

    if args.json:
        _emit_json({"checks": checks, "software": software_versions()})
    else:
        _table(
            "FSOCIETY AI doctor",
            ["Check", "Status", "Detail"],
            [[c["check"], c["status"], c["detail"]] for c in checks],
        )
    return EXIT_FAILURE if any(c["status"] == "fail" for c in checks) else EXIT_OK


def cmd_hardware(ctx: PlatformContext, args: argparse.Namespace) -> int:
    profile = ctx.hardware(refresh=args.refresh)
    if args.json:
        _emit_json(profile.model_dump(mode="json"))
        return EXIT_OK
    rows = [
        ["Host", profile.hostname],
        ["OS", f"{profile.os} {profile.os_release} ({profile.architecture})"],
        ["CPU", profile.cpu.model or "unknown"],
        [
            "Cores",
            f"{profile.cpu.physical_cores or '?'} physical / {profile.cpu.logical_cores or '?'} logical",
        ],
        [
            "RAM",
            f"{_fmt_bytes(profile.ram_total_bytes)} total, {_fmt_bytes(profile.ram_available_bytes)} available",
        ],
        ["Python", profile.python_version],
        ["Torch", profile.torch.version or "not installed"],
        ["Fingerprint", profile.fingerprint],
        ["Detection", profile.detection_mode],
    ]
    for gpu in profile.gpus:
        rows.append(
            [
                f"GPU {gpu.index}",
                f"{gpu.name} · {gpu.backend} · {_fmt_bytes(gpu.total_memory_bytes)} "
                f"({_fmt_bytes(gpu.free_memory_bytes)} free) · {gpu.source}",
            ]
        )
    _table("Hardware", ["Field", "Value"], rows)
    if profile.warnings:
        _console().print("[yellow]Warnings:[/]")
        for warning in profile.warnings:
            _console().print(f"  • {warning}")
    return EXIT_OK


def cmd_models(ctx: PlatformContext, args: argparse.Namespace) -> int:
    from ...core.model_manager import ModelManager

    manager = ModelManager(ctx.settings, ctx.logger)

    if args.models_command == "list":
        items = manager.scan(
            roots=[ctx.settings.resolved_models_dir()],
            include_hf_cache=not args.no_hf_cache,
            limit=args.limit,
        )
        if args.json:
            _emit_json([item.model_dump(mode="json") for item in items])
            return EXIT_OK
        _table(
            "Models",
            ["Model", "Source", "Params", "Size", "Architecture", "Formats"],
            [
                [
                    item.model_id,
                    item.source,
                    _fmt_params(item.parameter_count),
                    _fmt_bytes(item.size_bytes),
                    item.architecture or item.model_type or "unknown",
                    ",".join(item.formats),
                ]
                for item in items
            ],
        )
        if not items:
            _console().print(
                "[yellow]No models found.[/] Place models in "
                f"{ctx.settings.resolved_models_dir()} or download with "
                "`fsociety models download org/name --revision main`."
            )
        return EXIT_OK

    if args.models_command == "scan":
        roots = []
        for raw in args.path:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = safe_join(ctx.workspace.models, raw)
            roots.append(path)
        items = manager.scan(
            roots=roots or [ctx.settings.resolved_models_dir()],
            include_hf_cache=not args.no_hf_cache,
            limit=args.limit,
        )
        if args.json:
            _emit_json([item.model_dump(mode="json") for item in items])
        else:
            _console().print(f"Found {len(items)} model(s).")
            for item in items:
                _console().print(
                    f"  • {item.model_id} ({_fmt_params(item.parameter_count)})"
                )
        return EXIT_OK

    if args.models_command == "inspect":
        info = manager.inspect(
            args.reference, revision=args.revision, hash_files=args.hash
        )
        if args.json:
            _emit_json(info.model_dump(mode="json"))
            return EXIT_OK
        rows = [
            ["Model", info.model_id],
            ["Source", info.source],
            ["Path", info.path or "—"],
            ["Architecture", info.architecture or "unknown"],
            ["Model type", info.model_type or "unknown"],
            ["Parameters", _fmt_params(info.parameter_count)],
            ["Size", _fmt_bytes(info.size_bytes)],
            ["Layers", str(info.num_hidden_layers or "unknown")],
            ["Hidden", str(info.hidden_size or "unknown")],
            ["Attention heads", str(info.num_attention_heads or "unknown")],
            ["KV heads", str(info.num_key_value_heads or "unknown")],
            ["Vocab", str(info.vocab_size or "unknown")],
            ["Context", str(info.max_position_embeddings or "unknown")],
            ["Dtype", info.torch_dtype or "unknown"],
            ["MoE", "yes" if info.is_mixture_of_experts else "no"],
            ["Multimodal", "yes" if info.is_multimodal else "no"],
            ["Remote code", "required" if info.requires_remote_code else "no"],
            ["Revision", info.revision or "—"],
            ["Commit", info.commit or "—"],
            ["Formats", ", ".join(info.formats) or "unknown"],
        ]
        _table("Model", ["Field", "Value"], rows)
        for note in info.compatibility:
            _console().print(f"[cyan]•[/] {note}")
        for warning in info.warnings:
            _console().print(f"[yellow]![/] {warning}")
        return EXIT_OK

    if args.models_command == "verify":
        manifest_path = Path(args.manifest).expanduser() if args.manifest else None
        validation = manager.verify(args.reference, manifest_path=manifest_path)
        created_only = validation.details.get("mode") == "manifest_created"
        if args.json:
            _emit_json(validation.model_dump(mode="json"))
        else:
            _table(
                "Integrity",
                ["Field", "Value"],
                [
                    ["Model", validation.model_id],
                    ["Files checked", str(validation.files_checked)],
                    ["Hashes verified", str(validation.hashes_verified)],
                    ["Mismatches", str(len(validation.mismatches))],
                    ["Missing", str(len(validation.missing))],
                    [
                        "Result",
                        "MANIFEST CREATED (no comparison)"
                        if created_only
                        else ("OK" if validation.ok else "FAILED"),
                    ],
                ],
            )
            for mismatch in validation.mismatches:
                _console().print(f"[red]mismatch:[/] {mismatch}")
            for missing in validation.missing:
                _console().print(f"[red]missing:[/] {missing}")
            if created_only:
                _console().print(
                    "No --manifest was supplied, so no integrity comparison was performed. "
                    "Pass --manifest with a signed/known manifest to verify."
                )
        if created_only:
            return EXIT_OK
        return EXIT_OK if validation.ok else EXIT_FAILURE

    if args.models_command == "estimate":
        from ...core.estimator import EstimationRequest, estimate_memory

        info = manager.inspect(args.reference, revision=args.revision)
        estimate = estimate_memory(
            info,
            EstimationRequest(
                batch_size=args.batch_size or 1,
                sequence_length=args.sequence_length,
                dtype=args.dtype,
                quantization=args.quantization,
            ),
            ctx.hardware(),
        )
        if args.json:
            _emit_json(estimate.model_dump(mode="json"))
            return EXIT_OK
        _table(
            "Memory estimate (analytical)",
            ["Component", "Bytes"],
            [
                [name, _fmt_bytes(value)]
                for name, value in estimate.breakdown.as_dict().items()
            ],
        )
        _console().print(
            f"Fit: {'yes' if estimate.fits else 'no' if estimate.fits is False else 'unknown'}"
            + (
                f" · headroom {_fmt_bytes(estimate.headroom_bytes)}"
                if estimate.headroom_bytes is not None
                else ""
            )
        )
        if estimate.recommended_batch_size:
            _console().print(
                f"Recommended: dtype={estimate.recommended_dtype}, "
                f"quantization={estimate.recommended_quantization}, "
                f"batch={estimate.recommended_batch_size}"
            )
        for alternative in estimate.alternatives:
            _console().print(f"  • {alternative}")
        return EXIT_OK

    if args.models_command == "download":
        result = manager.download(
            args.repo_id,
            revision=args.revision,
            allow_patterns=args.pattern or None,
            confirm_weights=args.weights,
        )
        if args.json:
            _emit_json(result)
        else:
            _console().print(f"Downloaded snapshot: {result.get('path')}")
            _console().print(
                f"Revision: {result.get('revision')} · commit: {result.get('commit') or 'unknown'}"
            )
        return EXIT_OK

    raise ValidationError(f"Unknown models subcommand: {args.models_command}")


def cmd_optimize(ctx: PlatformContext, args: argparse.Namespace) -> int:
    from ...core.model_manager import ModelManager
    from ...optimizer import OptimizationRequest, Optimizer, SearchSpace
    from ...optimizer.resource_objective import (
        build_resource_objective,
        validate_plan_space,
    )

    if args.optimize_command != "run":
        raise ValidationError(f"Unknown optimize subcommand: {args.optimize_command}")
    manager = ModelManager(ctx.settings, ctx.logger)
    info = manager.inspect(args.model)
    space = SearchSpace.model_validate(_parse_json_argument(args.space))
    validate_plan_space({dimension.name for dimension in space.dimensions})
    hardware = ctx.hardware()
    objective = build_resource_objective(info, hardware, ctx.settings, base={})
    request = OptimizationRequest(
        name=args.name,
        space=space,
        n_trials=args.trials,
        seed=args.seed,
        sampler=args.sampler,
        directions=["minimize", "minimize"],
        tags=["resource-plan", "estimate"],
        model_id=info.model_id,
    )

    last = {"value": 0.0}

    def progress(number: int, total: int, best: float) -> None:
        last["value"] = best
        if not args.json:
            print(
                f"\rTrial {number}/{total} · best estimated bytes {best:.0f}",
                end="",
                flush=True,
            )

    optimizer = Optimizer(
        store=ctx.store, settings=ctx.settings, logger=ctx.logger, hardware=hardware
    )
    result = optimizer.run(request, objective, progress=progress)
    if not args.json:
        print()
    payload = result.model_dump(mode="json")
    payload["model_id"] = info.model_id
    payload["estimates_are"] = "analytical estimates, not measurements"
    if args.json:
        _emit_json(payload)
    else:
        _console().print(f"Experiment: [bold]{result.experiment_id}[/]")
        _console().print(f"Best params: {json.dumps(result.best_params, default=str)}")
        _console().print(
            f"Estimated total: {_fmt_bytes(int(result.best_values[0]))} · "
            f"fits={result.best_values[1] == 0.0 if len(result.best_values) > 1 else 'unknown'}"
        )
        _console().print("Numbers are analytical estimates from model metadata.")
    return EXIT_OK


def cmd_evaluate(ctx: PlatformContext, args: argparse.Namespace) -> int:
    from ...evaluation.regression import compare_reports
    from ...evaluation.reporting import load_report
    from ...evaluation.runner import EvaluationConfig, EvaluationRunner
    from ...evaluation.suites import SuiteRegistry

    if args.evaluate_command == "suites":
        suites = SuiteRegistry().list_suites()
        if args.json:
            _emit_json([suite.model_dump(mode="json") for suite in suites])
            return EXIT_OK
        _table(
            "Evaluation suites",
            ["Suite", "Version", "Items", "Grader", "Description"],
            [
                [s.suite_id, s.version, str(s.item_count), s.grader, s.description]
                for s in suites
            ],
        )
        _console().print(
            "[dim]Bundled suites are small original smoke suites, not the official "
            "MMLU/GSM8K/HumanEval datasets.[/]"
        )
        return EXIT_OK

    if args.evaluate_command == "run":
        spec: dict[str, Any] = {"kind": args.backend}
        if args.backend == "transformers":
            if not args.model:
                raise ValidationError(
                    "--model is required for the transformers backend."
                )
            spec["model_id"] = args.model
            spec["device"] = args.device
            spec["dtype"] = args.dtype
        else:
            if not args.path:
                raise ValidationError("--path is required for the llama_cpp backend.")
            spec["path"] = args.path
            spec["n_ctx"] = args.n_ctx
            spec["n_gpu_layers"] = args.n_gpu_layers
        from ...evaluation.backends import load_backend

        backend = load_backend(spec, ctx.settings)
        config = EvaluationConfig(
            seed=args.seed,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            limit=args.limit,
            batch_size=args.batch_size or 1,
            system_prompt=args.system_prompt,
        )
        runner = EvaluationRunner(
            store=ctx.store, logger=ctx.logger, hardware=ctx.hardware()
        )

        def progress(done: int, total: int, item: Any = None) -> None:
            if not args.json:
                item_id = getattr(item, "item_id", "")
                print(f"\rEvaluating {done}/{total} {item_id}", end="", flush=True)

        report = runner.run(
            args.suite,
            backend,
            config,
            progress=progress,
            save_to=ctx.workspace.reports,
        )
        if not args.json:
            print()
        payload = report.model_dump(mode="json")
        if args.json:
            _emit_json(payload)
        else:
            _console().print(f"Report: [bold]{report.report_id}[/]")
            _console().print(f"Status: {report.status} · items: {len(report.results)}")
            for metric, value in report.metrics.items():
                _console().print(f"  {metric}: {value}")
            _console().print(f"Suite hash: {report.suite_hash}")
            _console().print(f"Reports directory: {ctx.workspace.reports}")
            for warning in report.warnings:
                _console().print(f"[yellow]![/] {warning}")
        return EXIT_OK if report.status == "succeeded" else EXIT_FAILURE

    if args.evaluate_command == "report":
        if args.file:
            report = load_report(Path(args.file))
        else:
            if not args.experiment:
                raise ValidationError("Provide --experiment or --file.")
            experiment = ctx.store.get_experiment(args.experiment)
            candidates = [
                Path(str(a.metadata.get("json_path") or a.path))
                for a in ctx.store.list_artifacts(experiment.experiment_id)
            ]
            candidates += list(
                ctx.workspace.reports.rglob(f"*{experiment.experiment_id}*.json")
            )
            existing = [path for path in candidates if path.is_file()]
            if not existing:
                raise ValidationError(
                    f"No stored report for experiment {experiment.experiment_id}.",
                    hint="Run `fsociety evaluate run` first.",
                )
            report = load_report(existing[0])
        if args.output:
            Path(args.output).write_text(
                json.dumps(report.model_dump(mode="json"), indent=2, default=str),
                encoding="utf-8",
            )
            _console().print(f"Wrote {args.output}")
        elif args.json:
            _emit_json(report.model_dump(mode="json"))
        else:
            _console().print(f"{report.model_id} · suite {report.suite.suite_id}")
            _console().print(json.dumps(report.metrics, indent=2, default=str))
        return EXIT_OK

    if args.evaluate_command == "compare":
        baseline = load_report(Path(args.baseline))
        candidate = load_report(Path(args.candidate))
        comparison = compare_reports(baseline, candidate, threshold=args.threshold)
        payload = comparison.model_dump(mode="json")
        if args.output:
            Path(args.output).write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
            _console().print(f"Wrote {args.output}")
        if args.json:
            _emit_json(payload)
        else:
            _table(
                "Metric deltas",
                ["Metric", "Baseline", "Candidate", "Delta", "Regressed"],
                [
                    [
                        delta.metric,
                        f"{delta.baseline}",
                        f"{delta.candidate}",
                        f"{delta.delta}" if delta.delta is not None else "—",
                        "yes" if delta.regressed else "no",
                    ]
                    for delta in comparison.metric_deltas
                ],
            )
            _console().print(f"Verdict: [bold]{comparison.verdict}[/]")
            for reason in comparison.incomparability_reasons:
                _console().print(f"[red]incomparable:[/] {reason}")
            for warning in comparison.warnings:
                _console().print(f"[yellow]![/] {warning}")
        return EXIT_OK

    raise ValidationError(f"Unknown evaluate subcommand: {args.evaluate_command}")


def cmd_experiments(ctx: PlatformContext, args: argparse.Namespace) -> int:
    if args.experiments_command == "list":
        items = ctx.store.list_experiments(limit=args.limit, kind=args.kind)
        if args.json:
            _emit_json([item.model_dump(mode="json") for item in items])
            return EXIT_OK
        _table(
            "Experiments",
            ["ID", "Kind", "Status", "Model", "Created", "Name"],
            [
                [
                    e.experiment_id,
                    e.kind,
                    e.status,
                    e.model_id or "—",
                    e.created_at,
                    e.name,
                ]
                for e in items
            ],
        )
        return EXIT_OK
    if args.experiments_command == "show":
        experiment = ctx.store.get_experiment(args.experiment_id)
        _emit_json(experiment.model_dump(mode="json"))
        return EXIT_OK
    if args.experiments_command == "trials":
        trials = ctx.store.list_trials(args.experiment_id)
        if args.json:
            _emit_json([trial.model_dump(mode="json") for trial in trials])
            return EXIT_OK
        _table(
            "Trials",
            ["#", "State", "Values", "Params"],
            [
                [
                    str(t.number),
                    t.state,
                    ", ".join(f"{v:.4g}" for v in t.values),
                    json.dumps(t.params, default=str),
                ]
                for t in trials
            ],
        )
        return EXIT_OK
    if args.experiments_command == "export":
        payload = ctx.store.export_json()
        destination = (
            Path(args.output)
            if args.output
            else ctx.workspace.data / "experiments-export.json"
        )
        destination.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        _console().print(f"Wrote {destination}")
        return EXIT_OK
    raise ValidationError(f"Unknown experiments subcommand: {args.experiments_command}")


def cmd_plugins(ctx: PlatformContext, args: argparse.Namespace) -> int:
    from ...plugins.registry import PluginRegistry

    registry = PluginRegistry(
        [ctx.workspace.plugins], logger=ctx.logger, settings=ctx.settings
    )
    if args.plugins_command == "list":
        items = registry.list()
        if args.json:
            _emit_json([item.model_dump(mode="json") for item in items])
            return EXIT_OK
        _table(
            "Plugins",
            ["Name", "Version", "Kind", "Valid", "Permissions", "Errors"],
            [
                [
                    item.name,
                    item.version,
                    item.kind,
                    "yes" if item.valid else "no",
                    ",".join(item.permissions) or "—",
                    "; ".join(item.errors) or "—",
                ]
                for item in items
            ],
        )
        return EXIT_OK
    if args.plugins_command == "run":
        payload = _parse_json_argument(args.payload) if args.payload else {}
        result = registry.run(
            args.name,
            args.method,
            payload,
            allow_in_process=args.in_process,
        )
        _emit_json(result)
        return EXIT_OK
    if args.plugins_command == "validate":
        from ...plugins.isolation import discover_plugins_isolated

        discovered = discover_plugins_isolated(
            str(Path(args.path).expanduser()),
            timeout_seconds=ctx.settings.plugin_timeout_seconds,
            max_output_bytes=ctx.settings.plugin_max_output_bytes,
            allow_network=False,
        )
        if not discovered:
            raise ValidationError("No PluginBase subclasses found in the file.")
        for info in discovered:
            _emit_json(info.model_dump(mode="json"))
        return EXIT_OK
    raise ValidationError(f"Unknown plugins subcommand: {args.plugins_command}")


def cmd_security(ctx: PlatformContext, args: argparse.Namespace) -> int:
    from ...security.findings import configuration_findings
    from ...security.policy import TrustPolicy
    from ...security.redaction import redact_tree
    from ...security.sbom import dependency_audit, generate_cyclonedx

    if args.security_command == "policy":
        summary = TrustPolicy(ctx.settings).summary()
        if args.json:
            _emit_json(summary)
        else:
            _table(
                "Security policy",
                ["Field", "Value"],
                [[k, str(v)] for k, v in summary.items()],
            )
        return EXIT_OK
    if args.security_command == "findings":
        payload = configuration_findings(ctx)
        if args.json:
            _emit_json(payload)
        else:
            _table(
                "Configuration findings",
                ["Severity", "Title", "Status", "Detail"],
                [
                    [f["severity"], f["title"], f["status"], f["detail"]]
                    for f in payload["items"]
                ],
            )
        return EXIT_OK
    if args.security_command == "sbom":
        payload = generate_cyclonedx()
        destination = (
            Path(args.output) if args.output else ctx.workspace.audits / "sbom.cdx.json"
        )
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if args.json:
            _emit_json(payload)
        else:
            _console().print(
                f"Wrote {destination} ({len(payload.get('components', []))} components)."
            )
        return EXIT_OK
    if args.security_command == "audit":
        paths = [Path(p).expanduser() for p in args.path] or [
            ctx.workspace.root,
            Path.cwd(),
        ]
        findings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root in paths:
            for hit in redact_tree(root):
                if hit not in seen:
                    seen.add(hit)
                    findings.append({"type": "possible_secret", "path": hit})
        test_fixture_findings = [
            item
            for item in findings
            if "/tests/" in item["path"] or "\\tests\\" in item["path"]
        ]
        production_findings = [
            item for item in findings if item not in test_fixture_findings
        ]
        payload = {
            "generated_at": __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(),
            "secret_findings": production_findings,
            "test_fixture_findings": test_fixture_findings,
            "configuration_findings": configuration_findings(ctx)["items"],
            "dependency_audit": dependency_audit(),
            "software": software_versions(),
        }
        destination = (
            Path(args.output)
            if args.output
            else ctx.workspace.audits
            / f"security-audit-{int(__import__('time').time())}.json"
        )
        destination.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        if args.json:
            _emit_json(payload)
        else:
            _console().print(f"Wrote {destination}")
            _console().print(
                f"Production secret-like findings: {len(production_findings)} · "
                f"test fixtures: {len(test_fixture_findings)} · "
                f"configuration findings: {len(payload['configuration_findings'])}"
            )
        return EXIT_FAILURE if production_findings else EXIT_OK
    if args.security_command == "deps":
        payload = dependency_audit()
        if args.json:
            _emit_json(payload)
        else:
            _console().print(json.dumps(payload, indent=2, default=str))
        return EXIT_OK
    raise ValidationError(f"Unknown security subcommand: {args.security_command}")


def cmd_export(ctx: PlatformContext, args: argparse.Namespace) -> int:
    from ...core.models import ArtifactRecord, new_id, utcnow_iso
    from ...exporters.manifest import create_export_bundle
    from ...utils.hashing import sha256_dir

    if args.export_command != "bundle":
        raise ValidationError(f"Unknown export subcommand: {args.export_command}")
    files: list[Path] = []
    for raw in args.file:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = safe_join(ctx.workspace.root, raw)
        if not path.is_file():
            raise ValidationError(f"File not found: {raw}")
        files.append(path)
    destination = Path(args.destination).expanduser()
    if not destination.is_absolute():
        destination = safe_join(ctx.workspace.exports, args.destination)
    metadata = _parse_key_values(args.meta)
    result = create_export_bundle(destination, files=files, metadata=metadata)
    artifact = ArtifactRecord(
        artifact_id=new_id("artifact"),
        kind="export_bundle",
        path=str(destination),
        sha256=sha256_dir(destination),
        size_bytes=sum(f.stat().st_size for f in files),
        created_at=utcnow_iso(),
        metadata={"result": result},
    )
    ctx.store.add_artifact(artifact)
    _console().print(f"Bundle: {destination}")
    _console().print(f"Artifact: {artifact.artifact_id}")
    if args.json:
        _emit_json(artifact.model_dump(mode="json"))
    return EXIT_OK


def cmd_serve(ctx: PlatformContext, args: argparse.Namespace) -> int:
    import uvicorn

    from ..api.app import create_app

    settings = ctx.settings.model_copy(
        update={
            "api_host": args.host or ctx.settings.api_host,
            "api_port": args.port or ctx.settings.api_port,
        }
    )
    server_ctx = PlatformContext(settings)
    application = create_app(context=server_ctx)
    _console().print(
        f"{__platform_name__} API on http://{settings.api_host}:{settings.api_port} "
        f"(docs: /api/docs)"
    )
    if not settings.api_key:
        _console().print(
            "[yellow]No API key configured: non-loopback requests will be refused.[/]"
        )
    uvicorn.run(
        application,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )
    return EXIT_OK


def cmd_config(ctx: PlatformContext, args: argparse.Namespace) -> int:
    if args.config_command == "init":
        path = Path(args.path).expanduser()
        if path.exists() and not args.force:
            raise ValidationError(
                f"Configuration already exists: {path}",
                hint="Pass --force to overwrite.",
            )
        written = write_default_config(path, ctx.settings)
        _console().print(f"Wrote {written}")
        return EXIT_OK
    if args.config_command == "show":
        payload = ctx.settings.redacted()
        payload["workspace_layout"] = ctx.workspace.describe()
        if args.json:
            _emit_json(payload)
        else:
            _table(
                "Settings",
                ["Key", "Value"],
                [[k, str(v)] for k, v in sorted(payload.items())],
            )
        return EXIT_OK
    raise ValidationError(f"Unknown config subcommand: {args.config_command}")


def cmd_engine(ctx: PlatformContext, args: argparse.Namespace) -> int:
    from ...core.engine import HereticEngineAdapter

    adapter = HereticEngineAdapter(ctx.settings, logger=ctx.logger)
    if args.engine_command == "status":
        payload = adapter.status()
        if args.json:
            _emit_json(payload)
        else:
            _table(
                "Engine", ["Field", "Value"], [[k, str(v)] for k, v in payload.items()]
            )
        return EXIT_OK if payload.get("available") else EXIT_FAILURE
    if args.engine_command == "run":
        command = list(args.args)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            raise ValidationError(
                "Provide arguments for Heretic after `--`.",
                hint="Example: fsociety engine run -- Qwen/Qwen3-4B-Instruct-2507",
            )
        result = adapter.run(command)
        return int(result.returncode)
    raise ValidationError(f"Unknown engine subcommand: {args.engine_command}")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )
    common.add_argument(
        "--workspace", default=None, help="Workspace directory override."
    )
    common.add_argument(
        "--config", default=None, help="Platform TOML configuration file."
    )

    parser = argparse.ArgumentParser(
        prog="fsociety",
        description=f"{__platform_name__} — model engineering platform (v{__version__})",
        parents=[common],
    )
    parser.add_argument(
        "--version", action="version", version=f"{__platform_name__} {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Show platform and engine versions.")
    p = sub.add_parser("doctor", help="Validate the installation.")
    p.add_argument("--refresh", action="store_true", help="Re-run hardware detection.")

    p = sub.add_parser("hardware", help="Detect and display hardware.")
    p.add_argument("--refresh", action="store_true")

    models = sub.add_parser("models", help="Model discovery and management.")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    p = models_sub.add_parser("list", help="List local and cached models.")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--no-hf-cache", action="store_true")
    p = models_sub.add_parser("scan", help="Scan directories for models.")
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--no-hf-cache", action="store_true")
    p = models_sub.add_parser("inspect", help="Inspect model metadata.")
    p.add_argument("reference")
    p.add_argument("--revision", default=None)
    p.add_argument("--hash", action="store_true", help="Hash every weight file (slow).")
    p = models_sub.add_parser("verify", help="Verify model integrity.")
    p.add_argument("reference")
    p.add_argument("--manifest", default=None)
    p = models_sub.add_parser("estimate", help="Estimate memory footprint.")
    p.add_argument("reference")
    p.add_argument("--revision", default=None)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--sequence-length", type=int, default=2048)
    p.add_argument("--dtype", default="bfloat16")
    p.add_argument("--quantization", default="none")
    p = models_sub.add_parser("download", help="Download a HF snapshot.")
    p.add_argument("repo_id")
    p.add_argument("--revision", default="main")
    p.add_argument("--pattern", action="append", default=[])
    p.add_argument(
        "--weights", action="store_true", help="Allow weight files in the download."
    )

    optimize = sub.add_parser("optimize", help="Resource-planning optimization.")
    optimize_sub = optimize.add_subparsers(dest="optimize_command", required=True)
    p = optimize_sub.add_parser("run", help="Run a real Optuna search.")
    p.add_argument("--model", required=True)
    p.add_argument(
        "--space", required=True, help="Search space JSON (inline or @ file path)."
    )
    p.add_argument("--trials", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--sampler", choices=["tpe", "random"], default="tpe")
    p.add_argument("--name", default="resource-plan")

    evaluate = sub.add_parser("evaluate", help="Benchmarking and comparison.")
    evaluate_sub = evaluate.add_subparsers(dest="evaluate_command", required=True)
    evaluate_sub.add_parser("suites", help="List bundled suites.")
    p = evaluate_sub.add_parser("run", help="Run a suite with a real backend.")
    p.add_argument("--suite", required=True)
    p.add_argument(
        "--backend", choices=["transformers", "llama_cpp"], default="transformers"
    )
    p.add_argument("--model", default=None)
    p.add_argument("--path", default=None)
    p.add_argument("--device", default=None)
    p.add_argument("--dtype", default=None)
    p.add_argument("--n-ctx", type=int, default=2048)
    p.add_argument("--n-gpu-layers", type=int, default=0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-new-tokens", type=int, default=256)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--batch-size", type=int, default=None)
    p.add_argument("--system-prompt", default=None)
    p = evaluate_sub.add_parser("report", help="Show a stored report.")
    p.add_argument("--experiment", default=None)
    p.add_argument("--file", default=None)
    p.add_argument("--output", default=None)
    p = evaluate_sub.add_parser("compare", help="Compare baseline and candidate.")
    p.add_argument("baseline")
    p.add_argument("candidate")
    p.add_argument("--threshold", type=float, default=0.02)
    p.add_argument("--output", default=None)

    experiments = sub.add_parser("experiments", help="Experiment history.")
    experiments_sub = experiments.add_subparsers(
        dest="experiments_command", required=True
    )
    p = experiments_sub.add_parser("list")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--kind", default=None)
    p = experiments_sub.add_parser("show")
    p.add_argument("experiment_id")
    p = experiments_sub.add_parser("trials")
    p.add_argument("experiment_id")
    p = experiments_sub.add_parser("export")
    p.add_argument("--output", default=None)

    plugins = sub.add_parser("plugins", help="Plugin ecosystem.")
    plugins_sub = plugins.add_subparsers(dest="plugins_command", required=True)
    plugins_sub.add_parser("list")
    p = plugins_sub.add_parser("run")
    p.add_argument("name")
    p.add_argument("--method", default="score")
    p.add_argument("--payload", default=None)
    p.add_argument("--in-process", action="store_true")
    p = plugins_sub.add_parser("validate")
    p.add_argument("path")

    security = sub.add_parser("security", help="Security posture and auditing.")
    security_sub = security.add_subparsers(dest="security_command", required=True)
    security_sub.add_parser("policy")
    security_sub.add_parser("findings")
    p = security_sub.add_parser("audit")
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--output", default=None)
    p = security_sub.add_parser("sbom")
    p.add_argument("--output", default=None)
    security_sub.add_parser("deps")

    export = sub.add_parser("export", help="Create verifiable export bundles.")
    export_sub = export.add_subparsers(dest="export_command", required=True)
    p = export_sub.add_parser("bundle")
    p.add_argument("--file", action="append", required=True)
    p.add_argument("--destination", required=True)
    p.add_argument("--meta", action="append", default=[])

    p = sub.add_parser("serve", help="Run the API and dashboard.")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)

    config = sub.add_parser("config", help="Configuration files.")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    p = config_sub.add_parser("init")
    p.add_argument("--path", default="fsociety.toml")
    p.add_argument("--force", action="store_true")
    config_sub.add_parser("show")

    engine = sub.add_parser("engine", help="Preserved Heretic engine adapter.")
    engine_sub = engine.add_subparsers(dest="engine_command", required=True)
    engine_sub.add_parser("status")
    p = engine_sub.add_parser("run")
    p.add_argument("args", nargs=argparse.REMAINDER)

    _allow_trailing_json(parser)
    return parser


def _allow_trailing_json(parser: argparse.ArgumentParser) -> None:
    """Accept ``--json`` after a subcommand without clobbering the global value."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                subparser.add_argument(
                    "--json",
                    action="store_true",
                    default=argparse.SUPPRESS,
                    help=argparse.SUPPRESS,
                )
                _allow_trailing_json(subparser)


COMMANDS: dict[str, Callable[[PlatformContext, argparse.Namespace], int]] = {
    "version": cmd_version,
    "doctor": cmd_doctor,
    "hardware": cmd_hardware,
    "models": cmd_models,
    "optimize": cmd_optimize,
    "evaluate": cmd_evaluate,
    "experiments": cmd_experiments,
    "plugins": cmd_plugins,
    "security": cmd_security,
    "export": cmd_export,
    "serve": cmd_serve,
    "config": cmd_config,
    "engine": cmd_engine,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings: PlatformSettings = load_settings(
            args.config, workspace=args.workspace
        )
        ctx = PlatformContext(settings)
    except FSocietyError as exc:
        print(f"fsociety: {redact(exc.message)}", file=sys.stderr)
        if exc.hint:
            print(f"hint: {redact(exc.hint)}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        return COMMANDS[args.command](ctx, args)
    except FSocietyError as exc:
        if args.json:
            _emit_json({"error": exc.to_dict()})
        else:
            print(f"fsociety: {redact(exc.message)}", file=sys.stderr)
            if exc.hint:
                print(f"hint: {redact(exc.hint)}", file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    finally:
        ctx.close()


def entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    entrypoint()
