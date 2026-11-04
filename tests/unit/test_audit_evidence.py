"""A reference to a test is traceability, never proof it was executed."""
import importlib.util
from pathlib import Path


def test_acceptance_references_are_structural_even_when_tests_exist():
    path = Path(__file__).resolve().parents[2] / "scripts/audit_requirements.py"
    spec = importlib.util.spec_from_file_location("evidence_audit", path)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    audit.audit_acceptance_checklist()
    references = [r for r in audit.RESULTS if r["detail"].startswith("测试 ")]
    assert references
    assert all(r["kind"] == "structural" for r in references)


def test_interface_existence_does_not_count_as_execution():
    path = Path(__file__).resolve().parents[2] / "scripts/audit_requirements.py"
    spec = importlib.util.spec_from_file_location("interface_audit", path)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    audit.audit_interfaces()
    structural = [r for r in audit.RESULTS if "方法" in r["clause"] or "API " in r["clause"] or "注册字段" in r["clause"]]
    assert structural and all(r["kind"] == "structural" for r in structural)
    actual = [r for r in audit.RESULTS if r["clause"] == "读取立即上报依赖"]
    assert actual[0]["ok"] and actual[0]["kind"] == "behavioural"
