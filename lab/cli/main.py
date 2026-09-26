"""``commerce-lab`` command line interface (plan §18)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from lab import paths
from lab.runner.manifests import (
    ManifestError,
    all_scenarios,
    list_profiles,
    list_suites,
    list_targets,
    load_profile,
    load_requirements,
    load_suite,
    load_target,
    verify_pinned_schemas,
)


def _print(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
        return
    if isinstance(obj, list):
        for row in obj:
            print(row if isinstance(row, str) else "  ".join(f"{k}={v}" for k, v in row.items()))
    else:
        print(obj)


# ------------------------------------------------------------------ commands
def cmd_profiles(args) -> int:
    if args.action == "list":
        rows = [{"id": p.id, "protocol": p.protocol, "release": p.release, "status": p.status, "revision": p.specification["revision"][:12],
                 "required": len(p.required_capabilities), "optional": len(p.optional_capabilities)} for p in list_profiles()]
        _print(rows, args.json)
        return 0
    p = load_profile(args.id)
    data = dict(p.raw)
    data["pinned_schema_integrity"] = verify_pinned_schemas(p)
    print(json.dumps(data, indent=2))
    return 0


def cmd_targets(args) -> int:
    if args.action == "list":
        rows = [{"id": t.id, "kind": t.kind, "revision": t.revision, "profiles": ",".join(t.profiles), "observation": t.observation["kind"]}
                for t in list_targets()]
        _print(rows, args.json)
        return 0
    # inspect: start the environment and run capability discovery for each claimed profile
    import asyncio

    from drivers.base import load_driver
    from lab.runner.clock import LabClock, LabRng
    from lab.runner.environment import Environment, HarnessError
    from lab.runner.events import EventJournal

    target = load_target(args.target)
    clock = LabClock(frozen_at=float(args.frozen_clock)) if args.frozen_clock else LabClock()
    journal = EventJournal("inspect", clock)

    async def go():
        env = Environment(target=target, clock=clock, rng=LabRng(args.seed), journal=journal)
        try:
            await env.start()
        except HarnessError as exc:
            return {"target": target.id, "error": str(exc)}
        out = {"target": target.id, "kind": target.kind, "revision": target.revision, "environment": env.description, "profiles": {}}
        try:
            for pid in target.profiles:
                profile = load_profile(pid)
                driver = load_driver(profile)
                snap = await driver.inspect_target(env)
                advertised = set(snap.get("capabilities", []))
                out["profiles"][pid] = {
                    "advertised": sorted(advertised),
                    "required_missing": [c.id for c in profile.required_capabilities if c.id not in advertised],
                    "optional_missing": [c.id for c in profile.optional_capabilities if c.id not in advertised],
                    "error": snap.get("error"),
                    "details": {k: v for k, v in snap.items() if k not in ("raw", "capabilities")},
                }
        finally:
            await env.stop()
        return out

    result = asyncio.run(go())
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"target {result['target']} ({result.get('kind')}, {result.get('revision')})")
        if result.get("error"):
            print(f"  ERROR: {result['error']}")
            return 2
        for pid, info in result["profiles"].items():
            print(f"  profile {pid}")
            print(f"    advertised:       {', '.join(info['advertised']) or '-'}")
            print(f"    required missing: {', '.join(info['required_missing']) or '- (none)'}")
            print(f"    optional missing: {', '.join(info['optional_missing']) or '- (none)'}")
            if info.get("error"):
                print(f"    discovery error:  {info['error']}")
    return 0


def cmd_suites(args) -> int:
    profiles = {p.id: p for p in list_profiles()}
    reqs = load_requirements()
    rows = []
    for sid in list_suites():
        s = load_suite(sid, profiles=profiles, requirements=reqs)
        rows.append({"id": s.id, "version": s.version, "cases": len(s.scenarios), "description": s.description.strip()})
    _print(rows, args.json)
    return 0


def cmd_cases(args) -> int:
    profiles = {p.id: p for p in list_profiles()}
    reqs = load_requirements()
    scenarios = load_suite(args.suite, profiles=profiles, requirements=reqs).scenarios if args.suite else all_scenarios(profiles=profiles, requirements=reqs)
    rows = []
    for sc in scenarios:
        src = reqs.get(sc.requirement) if sc.requirement else None
        rows.append({
            "id": sc.id, "classification": sc.classification, "profile": sc.profile, "requirement": sc.requirement or sc.invariant,
            "kind": (src.kind if src else "application_invariant"), "keyword": (src.keyword if src else "invariant"),
            "source": (f"{src.source['document']} › {src.source['section']}" if src else "-"),
            "requires": ",".join(sc.requires_capabilities) or "-", "title": sc.title,
        })
    if args.format == "md":
        print("| Case | Class | Profile | Requirement / invariant | Kind | Source | Requires |")
        print("|---|---|---|---|---|---|---|")
        for r in rows:
            print(f"| `{r['id']}` | {r['classification']} | `{r['profile']}` | `{r['requirement']}` | {r['kind']} ({r['keyword']}) | {r['source']} | {r['requires']} |")
        return 0
    _print(rows, args.json)
    return 0


def cmd_validate(args) -> int:
    ok = True
    profiles = {p.id: p for p in list_profiles()}
    for p in profiles.values():
        for row in verify_pinned_schemas(p):
            if not row["ok"]:
                ok = False
                print(f"CHECKSUM MISMATCH {p.id}: {row['path']} (expected {row.get('expected')}, actual {row.get('actual', 'missing')})")
    reqs = load_requirements()
    for sc in all_scenarios(profiles=profiles, requirements=reqs):
        pass
    for sid in list_suites():
        load_suite(sid, profiles=profiles, requirements=reqs)
    for t in list_targets():
        for pid in t.profiles:
            if pid not in profiles:
                ok = False
                print(f"target {t.id} claims unknown profile {pid}")
    used = {sc.requirement for sc in all_scenarios(profiles=profiles, requirements=reqs) if sc.requirement}
    unused = sorted(set(reqs) - used)
    print(f"profiles={len(profiles)} requirements={len(reqs)} (unreferenced: {', '.join(unused) or 'none'}) suites={len(list_suites())} "
          f"scenarios={len(all_scenarios(profiles=profiles, requirements=reqs))} targets={len(list_targets())}")
    print("OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


def cmd_run(args) -> int:
    from lab.reports import write_reports
    from lab.runner.engine import RunOptions, run_sync
    from lab.runner.store import RunStore

    options = RunOptions(target_id=args.target, suite_id=args.suite, case_ids=args.case or [], seed=args.seed, run_id=args.run_id,
                         frozen_clock=None if args.wall_clock else args.frozen_clock, stop_on_harness_error=args.stop_on_harness_error)
    bundle = run_sync(options)
    store = RunStore(Path(args.runs_dir) if args.runs_dir else None)
    bundle_path = store.save(bundle)
    formats = tuple(f.strip() for f in args.format.split(",")) if args.format else ("json", "junit", "html")
    outputs = write_reports(bundle.to_dict(), bundle_path.parent, formats=formats)
    summary = bundle.summary()
    print(f"run {bundle.run.id}  target={bundle.run.target_id}@{bundle.run.target_revision}  suite={bundle.run.suite_id}  seed={bundle.run.fixture_seed}")
    for c in bundle.cases:
        flag = "" if c.result.value == "PASS" else f"  {c.reason[:150]}"
        print(f"  {c.result.value:14} {c.case_id}{flag}")
    counts = summary["counts"]
    pr = "n/a" if summary["pass_rate"] is None else f"{summary['pass_rate'] * 100:.1f}% of {summary['pass_rate_denominator']} executed applicable"
    print(f"counts: " + " ".join(f"{k}={v}" for k, v in counts.items()) + f"  pass_rate={pr}")
    print(f"bundle: {bundle_path}")
    for k, p in outputs.items():
        print(f"{k}: {p}")
    bad = counts["FAIL"] + counts["HARNESS_ERROR"] + (counts["INCONCLUSIVE"] if not args.allow_inconclusive else 0)
    return 1 if bad else 0


def cmd_report(args) -> int:
    from lab.reports import write_reports
    from lab.runner.store import RunStore

    store = RunStore(Path(args.runs_dir) if args.runs_dir else None)
    run_id = args.run or store.latest_run_id()
    if not run_id:
        print("no runs recorded", file=sys.stderr)
        return 2
    bundle = store.load_bundle(run_id)
    out_dir = Path(args.out) if args.out else store.root / run_id
    outputs = write_reports(bundle, out_dir, formats=(args.format,))
    for k, p in outputs.items():
        print(f"{k}: {p}")
    return 0


def cmd_compare(args) -> int:
    from lab.reports import compare_mod, html_mod
    from lab.runner.store import RunStore

    store = RunStore(Path(args.runs_dir) if args.runs_dir else None)
    base = store.load_bundle(args.baseline)
    cand = store.load_bundle(args.candidate)
    diff = compare_mod.compare(base, cand)
    if args.format == "json":
        print(json.dumps(diff, indent=2, default=str))
    elif args.format == "html":
        out = Path(args.out) if args.out else store.root / args.candidate / f"compare-{args.baseline}.html"
        out.write_text(html_mod.render(cand, diff), encoding="utf-8")
        print(f"html: {out}")
    else:
        print(compare_mod.render_text(diff))
    return 1 if diff["regressions"] and args.fail_on_regression else 0


def cmd_runs(args) -> int:
    from lab.runner.store import RunStore

    store = RunStore(Path(args.runs_dir) if args.runs_dir else None)
    _print(store.list_runs(), args.json)
    return 0


def cmd_proxy(args) -> int:
    """Network-mode fault proxy in front of an implementation under test."""
    import uvicorn

    from fault_proxy import FaultProxy
    from lab.runner.clock import LabClock
    from lab.runner.environment import resolve_protocol
    from lab.runner.events import EventJournal

    journal = EventJournal("proxy", LabClock())
    app = FaultProxy(journal, upstream_url=args.upstream, protocol_resolver=resolve_protocol)
    print(f"fault proxy → {args.upstream} on :{args.port}  (control API under /_lab/faults)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_serve_target(args) -> int:
    """Run a local reference target as a network service (for docker-compose / manual runs)."""
    import uvicorn

    from lab.runner.clock import LabClock, LabRng

    target = load_target(args.target)
    if target.kind != "local_reference":
        print("serve-target only works with local_reference targets", file=sys.stderr)
        return 2
    import importlib

    module_name, _, func_name = target.factory.rpartition(":")
    factory = getattr(importlib.import_module(module_name), func_name)
    clock = LabClock(frozen_at=float(args.frozen_clock)) if args.frozen_clock else LabClock()
    bundle = factory(clock, LabRng(args.seed), target.options)
    print(f"serving {target.id} on :{args.port} (seed {args.seed}); observation under /_lab/observe, admin under /_lab/admin")
    uvicorn.run(bundle.app, host=args.host, port=args.port, log_level="warning")
    return 0


# --------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="commerce-lab", description="Agentic Commerce Protocol Test Lab")
    p.add_argument("--runs-dir", help=f"where run bundles and lab.sqlite live (default {paths.RUNS})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("profiles", help="list or show supported protocol profiles")
    sp.add_argument("action", choices=["list", "show"])
    sp.add_argument("id", nargs="?")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_profiles)

    sp = sub.add_parser("targets", help="list targets or inspect one target's advertised capabilities")
    sp.add_argument("action", choices=["list", "inspect"])
    sp.add_argument("--target")
    sp.add_argument("--seed", type=int, default=42)
    sp.add_argument("--frozen-clock", type=int, default=1790294400)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_targets)

    sp = sub.add_parser("suites", help="list suites")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_suites)

    sp = sub.add_parser("cases", help="test-case inventory with requirement sources")
    sp.add_argument("--suite")
    sp.add_argument("--format", choices=["text", "md"], default="text")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_cases)

    sp = sub.add_parser("validate", help="validate manifests, scenarios, suites, and pinned schema checksums")
    sp.set_defaults(fn=cmd_validate)

    sp = sub.add_parser("run", help="execute a suite and/or cases against a target")
    sp.add_argument("--suite")
    sp.add_argument("--case", action="append", help="case id (repeatable)")
    sp.add_argument("--target", required=True)
    sp.add_argument("--seed", type=int, default=42)
    sp.add_argument("--run-id")
    sp.add_argument("--frozen-clock", type=int, default=1790294400, help="epoch seconds for the deterministic test clock")
    sp.add_argument("--wall-clock", action="store_true", help="use real time instead of the frozen clock")
    sp.add_argument("--format", default="json,junit,html", help="comma-separated report formats")
    sp.add_argument("--allow-inconclusive", action="store_true", help="exit 0 even when cases are INCONCLUSIVE")
    sp.add_argument("--stop-on-harness-error", action="store_true")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("report", help="re-export a report for a stored run")
    sp.add_argument("--run", help="run id (default: latest)")
    sp.add_argument("--format", choices=["json", "junit", "html"], default="html")
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("compare", help="compare a baseline run with a candidate run")
    sp.add_argument("--baseline", required=True)
    sp.add_argument("--candidate", required=True)
    sp.add_argument("--format", choices=["text", "json", "html"], default="text")
    sp.add_argument("--out")
    sp.add_argument("--fail-on-regression", action="store_true")
    sp.set_defaults(fn=cmd_compare)

    sp = sub.add_parser("runs", help="list stored runs")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_runs)

    sp = sub.add_parser("proxy", help="run the network fault proxy in front of an upstream target")
    sp.add_argument("--upstream", required=True)
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8080)
    sp.set_defaults(fn=cmd_proxy)

    sp = sub.add_parser("serve-target", help="serve a local reference target over HTTP")
    sp.add_argument("--target", default="local-merchant-corrected")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--seed", type=int, default=42)
    sp.add_argument("--frozen-clock", type=int, default=None)
    sp.set_defaults(fn=cmd_serve_target)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
