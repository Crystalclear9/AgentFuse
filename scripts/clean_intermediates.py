"""Delete an explicit reviewed cleanup plan, keeping locked leftovers together.

Usage: python scripts/clean_intermediates.py --plan path.json [--apply]
Only relative paths inside this checkout are accepted. Source, active tools,
runtime user data and verification evidence are protected independently of plan.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import stat

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = (".git", "agentfuse", "tests", "scripts", "docs", "examples", "config",
             "benchmarks", ".github", ".tools/python", ".tools/python-linux", ".tools/git",
             ".tools/uv", ".tools/uv-linux", ".tools/browsers", ".tools/docker", ".tools/images",
             ".tools/ripgrep",
             ".venv-local", ".venv-wsl", ".agentfuse/db", ".agentfuse/workspaces",
             ".agentfuse/branches", ".agentfuse/results", ".agentfuse/backups", ".agentfuse/logs",
             ".agentfuse/current-review", ".agentfuse/dist")


def checked(relative):
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError(f"cleanup path must be relative: {relative}")
    target = (ROOT / relative).resolve()
    target.relative_to(ROOT)
    if target == ROOT:
        raise ValueError("cannot remove the workspace")
    parts = target.relative_to(ROOT).parts
    cache = target.name == "__pycache__" and parts[0] in ("agentfuse", "tests", "scripts", "examples")
    allowed = (cache or parts[0] in (".agentfuse", ".venv", "build", ".pytest_cache", "agentfuse.egg-info")
               or parts[:2] in ((".tools", "downloads"), (".tools", "cache"))
               or (len(parts) == 1 and parts[0].startswith(".tmp_")))
    if not allowed:
        raise ValueError(f"not an intermediate artifact: {relative}")
    for name in PROTECTED:
        protected = (ROOT / name).resolve()
        if not cache and (target == protected or target.is_relative_to(protected) or protected.is_relative_to(target)):
            raise ValueError(f"protected cleanup target: {relative}")
    return target


def readonly_retry(function, path, exc):
    target = Path(path)
    target.resolve().relative_to(ROOT)
    target.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
    function(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    names = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    targets = [(name, checked(name)) for name in names]
    if not args.apply:
        print(json.dumps(dict(validated=len(targets), targets=names), ensure_ascii=False, indent=2))
        return
    records = []
    for name, target in targets:
        record = dict(path=name, status="absent")
        if target.exists():
            try:
                if target.is_dir():
                    shutil.rmtree(target, onerror=readonly_retry)
                else:
                    target.unlink()
                record["status"] = "deleted"
            except (OSError, ValueError) as exc:
                if name in (".agentfuse/tmp", ".agentfuse/tmp-linux") and target.is_dir() and not any(target.iterdir()):
                    record.update(status="empty_directory_retained", remaining_files=0, error=str(exc))
                    records.append(record)
                    continue
                quarantine = ROOT / "_manual_delete" / name
                quarantine.parent.mkdir(parents=True, exist_ok=True)
                original_destination = quarantine
                serial = 2
                while os.path.lexists(quarantine):
                    quarantine = original_destination.with_name(f"{original_destination.name}-{serial}")
                    serial += 1
                quarantine.resolve().relative_to((ROOT / "_manual_delete").resolve())
                try:
                    target.rename(quarantine)
                    record.update(status="moved", destination=quarantine.relative_to(ROOT).as_posix())
                except OSError as move_error:
                    # A Windows handle may deny both deletion and renaming.
                    # Preserve an accurate original-path list for manual work.
                    record.update(status="locked_at_original", error=str(exc), move_error=str(move_error))
        records.append(record)
    output = ROOT / "docs/verification/cleanup.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    pending = [record for record in records if record["status"] in ("moved", "locked_at_original")]
    if pending:
        folder = ROOT / "_manual_delete"
        folder.mkdir(exist_ok=True)
        manifest = folder / "MANIFEST.json"
        previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
        combined = {row.get("destination", row["path"]):row for row in [*previous, *pending]}
        manifest.write_text(json.dumps(list(combined.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({status: sum(r["status"] == status for r in records)
                      for status in ("deleted", "absent", "moved", "locked_at_original", "empty_directory_retained")}))


if __name__ == "__main__":
    main()
