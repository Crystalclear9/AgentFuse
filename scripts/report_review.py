"""Build a release report from complete test evidence; never turn skips into passes."""
import argparse
from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def suite(path):
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    if not cases or root.findall(".//failure") or root.findall(".//error"):
        raise ValueError(f"failed or empty test suite: {path}")
    skipped = [case for case in cases if case.find("skipped") is not None]
    return dict(total=len(cases), passed=len(cases)-len(skipped), skipped=len(skipped),
                skip_reasons=[case.find("skipped").get("message", "") for case in skipped]), cases


def source_digest():
    digest = hashlib.sha256()
    files = []
    for directory in ("agentfuse", "tests", "scripts", "config", "examples", ".github", "benchmarks"):
        files.extend(path for path in (ROOT / directory).rglob("*")
                     if path.suffix in (".py", ".js", ".mjs", ".html", ".css", ".toml", ".ps1", ".sh", ".json", ".yml", ".yaml"))
    files.extend([ROOT / "pyproject.toml", ROOT / "requirements-dev.lock", ROOT / "README.md", ROOT / "LICENSE", ROOT / ".gitattributes"])
    if (ROOT / 'requirements-p2.lock').exists():
        files.append(ROOT / 'requirements-p2.lock')
    for path in sorted(files, key=lambda item: item.relative_to(ROOT).as_posix()):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, default=ROOT / ".agentfuse/current-review")
    parser.add_argument("--output", type=Path, default=ROOT / ".agentfuse/verification/current")
    args = parser.parse_args()
    reports = args.reports.resolve()
    windows, cases = suite(reports / "windows-suite.xml")
    wsl, linux_cases = suite(reports / "wsl-suite.xml")
    py312, py312_cases = suite(reports / "py312-suite.xml")
    py313, py313_cases = suite(reports / "py313-suite.xml")
    cancellation_ack, _ = suite(reports / "cancellation-ack.xml")
    cancellation_review, _ = suite(reports / "cancellation-review.xml")
    guard_refresh, _ = suite(reports / "guard-rule-refresh.xml")
    close_capture, _ = suite(reports / "close-capture-final.xml")
    docker, _ = suite(reports / "docker-extensions.xml")
    resources, _ = suite(reports / "resource-final-recheck.xml")
    external, _ = suite(reports / "windows-external-recheck.xml")
    review_regressions, _ = suite(reports / "review-final.xml")
    wsl_recheck, _ = suite(reports / "wsl-final-recheck.xml")
    audit_recheck, _ = suite(reports / "audit-final.xml")
    wsl_audit_recheck, _ = suite(reports / "wsl-audit-final.xml")
    isolation = json.loads((reports / "mutation-isolation.json").read_text(encoding="utf-8"))
    if not all(isolation[key] for key in ("isolated_copy", "main_source_unchanged", "isolated_source_restored")):
        raise ValueError("mutation source restoration was not verified")
    soak = json.loads((reports / "soak.json").read_text(encoding="utf-8"))
    if soak["problems"] or soak["errors"] or soak["minutes"] < 10 or soak["iterations"] < 1:
        raise ValueError("ten-minute soak has not passed")
    wheel_smoke = json.loads((reports / "wheel-smoke.txt").read_text(encoding="utf-8"))
    if (wheel_smoke["shared_baseline_executions"], wheel_smoke["shared_online_executions"]) != (2, 1) or not wheel_smoke["offline_api_docs"]:
        raise ValueError("installed wheel has not passed its real execution smoke check")
    if not wheel_smoke.get("api_thread_handles_released"):
        raise ValueError("installed wheel did not release API database handles")
    collection = subprocess.run([sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
                                cwd=ROOT, text=True, capture_output=True, check=True)
    expected = {line.strip().replace("\\", "/") for line in collection.stdout.splitlines()
                if line.startswith("tests/") and "::" in line}
    actual = {case.get("classname").replace(".", "/") + ".py::" + case.get("name") for case in cases}
    if actual != expected:
        raise ValueError("Windows JUnit does not cover the current complete test collection")
    linux_actual = {case.get("classname").replace(".", "/") + ".py::" + case.get("name") for case in linux_cases}
    if linux_actual != expected:
        raise ValueError("WSL JUnit does not cover the current complete test collection")
    for label, version_cases in (("3.12", py312_cases), ("3.13", py313_cases)):
        version_actual = {case.get("classname").replace(".", "/") + ".py::" + case.get("name") for case in version_cases}
        if version_actual != expected:
            raise ValueError(f"Python {label} JUnit does not cover the current complete collection")
    matrix = {}
    for pool in range(3):
        for workers in (1, 2):
            matrix[f"{pool}/{workers}"] = suite(reports / f"matrix/pool-{pool}-workers-{workers}.xml")[0]
    audit = json.loads((reports / "audit.json").read_text(encoding="utf-8-sig"))
    mutations = json.loads((reports / "guard-selftest.json").read_text(encoding="utf-8-sig"))
    if audit["failed"] or mutations["failed"]:
        raise ValueError("audit or mutation checks failed")
    if not all(item["ok"] and item["guards"] and all(g["caught"] for g in item["guards"])
               for item in mutations["mutations"]):
        raise ValueError("mutation evidence is incomplete")
    performance = (reports / "performance.txt").read_text(encoding="utf-8-sig")
    if "no performance regression detected" not in performance or "[FAIL]" in performance:
        raise ValueError("performance guard has not passed")
    acceptance = (reports / "acceptance.txt").read_text(encoding="utf-8-sig")
    counts = re.findall(r"(\d+)/(\d+)\s*项通过", acceptance)
    if not counts or int(counts[-1][0]) != int(counts[-1][1]) or "[FAIL]" in acceptance:
        raise ValueError("delivery acceptance has not passed")
    spec = importlib.util.spec_from_file_location("review_requirement_audit", ROOT / "scripts/audit_requirements.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requirement = (ROOT / "docs/REQUIREMENTS.zh-CN.md").read_text(encoding="utf-8")
    sections = re.findall(r"^#{2,3}\s+(\d+\.\d+)\s+(.+)$", requirement, re.M)
    passed = {case.get("name").split("[")[0] for case in [*cases, *linux_cases]
              if case.find("skipped") is None}
    mapping = []
    for section, title in sections:
        tests = module.REQUIRED_TRACEABILITY_SECTIONS.get(section, ())
        if not tests or not all(test in passed for test in tests):
            raise ValueError(f"unverified requirement section {section}: {tests}")
        mapping.append(dict(section=section, title=title, tests=list(tests)))
    toolchain = json.loads((reports / "toolchain.json").read_text(encoding="utf-8"))
    summary = dict(date=date.today().isoformat(), windows=windows, wsl=wsl, docker=docker, matrix=matrix,
                   python312=py312, python313=py313, cancellation_ack=cancellation_ack,
                   close_capture=close_capture,
                   cancellation_review=cancellation_review,
                   guard_rule_refresh=guard_refresh,
                   resource_recheck=resources, external_framework_recheck=external,
                   review_regressions=review_regressions,
                   wsl_final_recheck=wsl_recheck,
                   audit_recheck=audit_recheck, wsl_audit_recheck=wsl_audit_recheck, mutation_isolation=isolation,
                   installed_wheel=wheel_smoke,
                   soak={key:soak[key] for key in ("minutes", "iterations", "errors", "worker_growth", "rss_growth_mb")},
                   audit={key:audit[key] for key in ("total", "failed", "behavioural", "structural")},
                   mutations=mutations["total"], acceptance="/".join(counts[-1]),
                   performance_passed=True, requirement_subsections=len(mapping),
                   requirements_sha256=hashlib.sha256((ROOT / "docs/REQUIREMENTS.zh-CN.md").read_bytes()).hexdigest(),
                   performance_baseline_sha256=hashlib.sha256((ROOT / "benchmarks/baseline.json").read_bytes()).hexdigest(),
                   source_sha256=source_digest(), full_p2_completed=False, toolchain=toolchain)
    summary["linux_toolchain"] = json.loads((reports / "linux-toolchain.json").read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "requirements.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
