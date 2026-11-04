"""Build a release report from complete test evidence; never turn skips into passes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
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
    for path in sorted(files):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, default=ROOT / ".agentfuse/current-review")
    args = parser.parse_args()
    reports = args.reports.resolve()
    windows, cases = suite(reports / "windows-suite.xml")
    wsl, linux_cases = suite(reports / "wsl-suite.xml")
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
    summary = dict(date="2026-11-04", windows=windows, wsl=wsl, docker=docker, matrix=matrix,
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
    output = ROOT / "docs/verification/current"
    output.mkdir(parents=True, exist_ok=True)
    for source in reports.glob("*"):
        if (source.is_file() and source.name != "report-generation.json"
                and not source.name.startswith("remote-ci")
                and source.suffix in (".xml", ".json", ".txt", ".log")):
            shutil.copyfile(source, output / source.name)
    shutil.copytree(reports / "matrix", output / "matrix", dirs_exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "requirements.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    review = "# 原计划逐项复核\n\n原文见 REQUIREMENTS.zh-CN.md。以下映射要求对应测试确实通过，不能仅凭函数名存在。\n\n"
    review += "| 小节 | 内容 | 已执行的测试 |\n|---|---|---|\n"
    for row in mapping:
        review += f"| {row['section']} | {row['title']} | " + ", ".join(f"`{t}`" for t in row["tests"]) + " |\n"
    review += "\n首版必需验收与已约定扩展完成；全文中所有 P2 尚不能宣称完成。生产 LLM/GPU KV 按用户选择保留，框架高级自动适配、任意程序完备追踪及绝对通用沙箱仍有明确边界。详见 KNOWN_LIMITATIONS.md。\n"
    (ROOT / "docs/PLAN_REVIEW.md").write_text(review, encoding="utf-8")
    section = f'''<!-- current-verification:start -->
## 原计划完整复核（2026-11-04）

| 检查 | 本轮实际结果 |
|---|---|
| Windows 完整测试，项目内 Python/Node/Chromium | {windows['passed']} passed / {windows['skipped']} skipped |
| WSL2 完整测试，项目内独立 Python/Node | {wsl['passed']} passed / {wsl['skipped']} skipped |
| Docker、TLS 与扩展独立验证 | {docker['passed']} passed / {docker['skipped']} skipped |
| 资源释放与迟到 RSS 定向复检 | {resources['passed']} passed |
| 外部框架与 CLI 定向复检 | {external['passed']} passed |
| Windows / WSL 最终快照配置与本轮回归复检 | {review_regressions['passed']} / {wsl_recheck['passed']} passed |
| Windows / WSL 审计、证据分类与追溯表最终复检 | {audit_recheck['passed']} / {wsl_audit_recheck['passed']} passed |
| 十分钟持续运行 | {soak['iterations']} 轮，{soak['errors']} 错误，残留 Worker 增长 {soak['worker_growth']} |
| 独立安装的 wheel 真执行 | Baseline 2 次 / Online 1 次物理执行，离线 API 文档已打包 |
| 预热池 0/1/2 × 并发 1/2 关键用例 | 六组全部通过（明细见 current/summary.json） |
| 需求审计 | {audit['total']}/{audit['total']}，{audit['behavioural']} 行为 / {audit['structural']} 结构 |
| 故意破坏实现的变异自检 | {mutations['total']}/{mutations['total']} 检出 |
| 交付验收 | {'/'.join(counts[-1])} |
| 性能守卫，原基线/原容差，重复 5 次 | 通过 |
| 原始计划编号小节 | {len(mapping)} 项均有实际通过的测试映射 |

Windows 跳过：{'；'.join(windows['skip_reasons'])}。
WSL2 跳过：{'；'.join(wsl['skip_reasons'])}。
Windows 的符号链接边界由 WSL2 实际执行，Docker 与 Windows 浏览器分别独立实测；跳过不计作通过。
Python 与全部 Python 依赖、Git、uv、Node、Chromium 已安装在项目的 `.tools` / `.venv-local` / `.venv-wsl`。
Docker Engine、WSL2 和操作系统属于系统前提，未伪称可通过复制普通文件实现项目内安装。
源码与测试快照（文本换行归一为 LF）：`{summary['source_sha256']}`。环境与原始证据见 [本轮汇总](verification/current/summary.json)。
完整映射见 [PLAN_REVIEW.md](PLAN_REVIEW.md)。测试通过不代表所有 P2 都完成，保留的范围见 KNOWN_LIMITATIONS.md。

本轮解决取消/最终回收重复终止同一个进程、超时任务自取消、Windows 解释器启动器开销、API 文档 CDN 依赖和远程 RSS 未接入问题。
另外修复外部框架强杀导致 SQLite 未刷盘，以及已释放执行收到迟到 RSS 后重新占用预算的竞态；定向复检单独列出。
空闲预热 Worker 不再扫描进程树；本地进程树 RSS 改为父子内存求和，真实父子进程验证通过。
首轮变异自检 57/58，发现报告中的无效测试引用未被审计；已补齐引用核验并重新完成全部 58 项。自检使用独立源码副本，逐文件验证还原和主源码未改动。
前一轮失败记录留存于 verification 根目录，当前原始结果位于 verification/current；不覆盖失败历史、不修改性能基线或放宽容差。
<!-- current-verification:end -->
'''
    report = ROOT / "docs/TEST_REPORT.md"
    text = report.read_text(encoding="utf-8")
    old = re.search(r"<!-- current-verification:start -->.*?<!-- current-verification:end -->", text, re.S)
    if old:
        history = ROOT / "docs/verification/previous-review.md"
        if not history.exists(): history.write_text(old.group(), encoding="utf-8")
    text = re.sub(r"<!-- current-verification:start -->.*?<!-- current-verification:end -->", "", text, flags=re.S)
    report.write_text(text.replace("# Test Report\n", "# Test Report\n\n" + section, 1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
