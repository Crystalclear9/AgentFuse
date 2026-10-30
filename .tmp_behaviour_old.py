def audit_behaviour() -> None:
    """Verify the runtime clauses with a real run.

    The text based checks can only prove that a name exists.  The clauses of
    the sharing protocol, the event protocol and the failure semantics are
    therefore verified against a real workflow: the events have to be in the
    database, the executions have to be physical processes and the results
    have to be the results of those executions.
    """

    import asyncio
    import os
    import shutil

    from agentfuse.app import AgentFuseApp
    from agentfuse.core.state import RuntimeMode
    from agentfuse.scenarios.definitions import (
        scenario_divergent,
        scenario_shared,
    )
    from agentfuse.settings import load_settings

    home = ROOT / ".agentfuse" / "audit-behaviour"
    shutil.rmtree(home, ignore_errors=True)

    async def run() -> None:
        settings = load_settings(home=str(home))
        app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
        try:
            shared = await scenario_shared(app, RuntimeMode.ONLINE, {})
            calls = app.repository.list_logical_calls(shared.workflow_id)
            executions = app.repository.list_executions(shared.workflow_id)
            record(
                "17.2",
                "等价调用只产生一次物理执行",
                len(calls) == 2 and len(executions) == 1,
                f"calls={len(calls)} executions={len(executions)}",
            )
            record(
                "17.2",
                "共享者与 Leader 得到同一个结果",
                len(shared.results) == 2
                and shared.results[0].result_hash == shared.results[1].result_hash,
            )
            record(
                "9.4",
                "工具在独立进程执行",
                all(execution["worker_pid"] for execution in executions)
                and all(
                    execution["worker_pid"] != os.getpid() for execution in executions
                ),
                f"pids={[e['worker_pid'] for e in executions]}",
            )
            record(
                "21.4",
                "执行次数来自真实物理执行记录",
                shared.metrics["physical_executions"] == 1 == len(executions),
                f"metric={shared.metrics['physical_executions']} rows={len(executions)}",
            )
            record(
                "16",
                "调用与执行记录外键完整",
                app.database.write_errors == 0
                and not app.database.foreign_key_violations(),
                f"errors={app.database.write_errors}",
            )
            # the relations have to be there - the count alone proves nothing
            relations = {
                (str(row[0]), str(row[4]), str(row[3]))
                for row in app.database.foreign_keys()
            }
            expected_relations = {
                ("agents", "workflow_id", "workflows"),
                ("agent_invocations", "agent_id", "agents"),
                ("logical_tool_calls", "physical_execution_id", "physical_executions"),
                ("physical_executions", "workflow_id", "workflows"),
                ("share_groups", "leader_execution_id", "physical_executions"),
                ("share_participants", "share_group_id", "share_groups"),
                ("share_participants", "logical_call_id", "logical_tool_calls"),
                ("dependency_records", "execution_id", "physical_executions"),
                ("validation_records", "share_group_id", "share_groups"),
                ("resource_samples", "execution_id", "physical_executions"),
            }
            missing_relations = sorted(
                relation
                for relation in expected_relations
                if relation not in relations
            )
            record(
                "16",
                "外键关系齐备",
                not missing_relations,
                f"缺: {missing_relations}"
                if missing_relations
                else f"{len(expected_relations)} 条关系",
            )
            # the write errors have to be counted: a statement that violates a
            # constraint must not disappear silently
            errors_before = app.database.write_errors
            app.database.write(
                "INSERT INTO agents (agent_id, workflow_id, created_at) "
                "VALUES ('audit-orphan', 'audit-missing-workflow', 1.0)"
            )
            app.database.flush()
            record(
                "16",
                "写入错误被计数",
                app.database.write_errors > errors_before,
                f"errors={app.database.write_errors} last={app.database.last_error}",
            )

            # the event protocol: the events have to be in the database
            events = app.repository.list_events(shared.workflow_id)
            published = {event["event_type"] for event in events}
            for event in (
                "WORKFLOW_STARTED",
                "AGENT_BOUND",
                "AGENT_INVOKED",
                "AGENT_RETURNED",
                "TOOL_CALL_REQUESTED",
                "TOOL_EXECUTION_STARTED",
                "TOOL_READ_OBSERVED",
                "SHARE_CANDIDATE_FOUND",
                "SHARE_VALIDATION_PASSED",
                "TOOL_CALL_WAITING",
                "TOOL_EXECUTION_FINISHED",
                "TOOL_RESULT_DELIVERED",
                "WORKFLOW_FINISHED",
            ):
                record(
                    "5.1",
                    f"事件 {event} 真实产生",
                    event in published,
                    f"已发布 {event}" if event in published else "未产生",
                )
            record(
                "5.1",
                "事件携带因果链",
                any(event["parent_event_id"] for event in events),
            )

            # the divergent scenario: the follower has to be detached and has
            # to run its own execution
            divergent = await scenario_divergent(
                app, RuntimeMode.ONLINE, {"rounds": 3000}
            )
            participants = app.repository.list_participants(divergent.workflow_id)
            divergent_calls = app.repository.list_logical_calls(divergent.workflow_id)
            divergent_executions = app.repository.list_executions(divergent.workflow_id)
            detached = [
                participant for participant in participants if participant["status"] == "DETACHED"
            ]
            record(
                "8.5",
                "在线增量依赖验证",
                divergent.metrics["validation_failures"] >= 1,
                f"failures={divergent.metrics['validation_failures']}",
            )
            record(
                "8.8",
                "分离后独立执行",
                len(detached) >= 1 and len(divergent_executions) == 2,
                f"detached={len(detached)} executions={len(divergent_executions)}",
            )
            record(
                "17.3",
                "冲突依赖导致分离",
                divergent.metrics["detach_count"] >= 1
                and divergent.metrics["validation_failures"] >= 1,
            )
            record(
                "8.7",
                "最终提交验证门禁",
                _final_gate_holds(app, divergent),
                "未完成验证的参与者没有收到共享结果"
                if _final_gate_holds(app, divergent)
                else "分离的参与者收到了共享结果",
            )
            record(
                "21.4",
                "分离后每个调用都有真实执行",
                len({call["physical_execution_id"] for call in divergent_calls})
                == len(divergent_calls),
            )
            record(
                "12.4",
                "分离时 Leader 尚未结束后续计算",
                (divergent.metrics["detach_lead_ms_max"] or 0) > 0,
                f"lead_ms={divergent.metrics['detach_lead_ms_max']}",
            )

            # the candidate key must not contain the snapshot
            from agentfuse.sharing.candidate import compute_candidate_key

            spec = app.registry.get("ast_analyzer")
            key_a = compute_candidate_key(spec, {"files": ["a.py"]})
            key_b = compute_candidate_key(spec, {"files": ["a.py"]})
            key_other = compute_candidate_key(spec, {"files": ["b.py"]})
            record("8.1", "候选键只由工具与参数决定", key_a == key_b)
            record(
                "8.1",
                "候选键不含 workspace snapshot",
                "snapshot" not in key_a,
                key_a,
            )
            record(
                "8.1",
                "不同参数产生不同候选键",
                key_a != key_other,
            )

            # a snapshot that was modified behind the runtime's back must
            # not take part in a shared execution
            from agentfuse.scenarios.common import (
                DEMO_FILES,
                create_branch,
                prepare_base_snapshot,
            )

            base = prepare_base_snapshot(app)
            branch = create_branch(app, base, None, "audit-branch")
            tampered = branch.tree_path / "src" / "alpha.py"
            tampered.write_text("tampered = True\n", encoding="utf-8")
            integrity_ok = app.snapshots.check_integrity(branch.snapshot_id)
            record("21.2", "快照完整性检查发现篡改", integrity_ok is False)

            workflow = app.create_workflow("audit-integrity")
            workflow.default_snapshot_id = base.snapshot_id
            workflow.start()
            await asyncio.gather(
                app.coordinator.submit_tool_call(
                    workflow_id=workflow.workflow_id,
                    tool_name="ast_analyzer",
                    arguments={"files": list(DEMO_FILES)},
                    snapshot_id=base.snapshot_id,
                ),
                app.coordinator.submit_tool_call(
                    workflow_id=workflow.workflow_id,
                    tool_name="ast_analyzer",
                    arguments={"files": list(DEMO_FILES)},
                    snapshot_id=branch.snapshot_id,
                ),
            )
            integrity_executions = app.repository.list_executions(workflow.workflow_id)
            record(
                "21.2",
                "快照被篡改时拒绝共享",
                len(integrity_executions) == 2,
                f"executions={len(integrity_executions)}",
            )

            # §13.2 / §13.3 - the baseline mode computes the same result
            # without any sharing
            baseline = await scenario_shared(app, RuntimeMode.BASELINE, {})
            baseline_executions = app.repository.list_executions(baseline.workflow_id)
            record(
                "13.3",
                "共享结果与 Baseline 完全一致",
                len(baseline_executions) == 2
                and baseline.results[0].result_hash == shared.results[0].result_hash,
                f"baseline_executions={len(baseline_executions)}",
            )
            record(
                "13.2",
                "Baseline 不共享",
                baseline.metrics["physical_executions"] == 2
                and baseline.metrics["saved_executions"] == 0,
                f"executions={baseline.metrics['physical_executions']}",
            )

            # §9.2 - a shared call occupies exactly one execution slot
            slot_workflow = app.create_workflow("audit-shared-slot")
            slot_workflow.default_snapshot_id = base.snapshot_id
            slot_workflow.start()
            observed_slots: list[dict[str, Any]] = []

            def slot_observer(event: Any) -> None:
                if event.event_type == "TOOL_READ_OBSERVED":
                    observed_slots.append(app.scheduler.snapshot())

            app.event_bus.subscribe(slot_observer)
            await asyncio.gather(
                app.coordinator.submit_tool_call(
                    workflow_id=slot_workflow.workflow_id,
                    tool_name="content_digest",
                    arguments={"files": list(DEMO_FILES), "rounds": 600},
                    snapshot_id=base.snapshot_id,
                ),
                app.coordinator.submit_tool_call(
                    workflow_id=slot_workflow.workflow_id,
                    tool_name="content_digest",
                    arguments={"files": list(DEMO_FILES), "rounds": 600},
                    snapshot_id=base.snapshot_id,
                ),
            )
            slot_executions = app.repository.list_executions(slot_workflow.workflow_id)
            record(
                "9.2",
                "共享只占用一次执行槽位",
                len(slot_executions) == 1
                and all(item["running"] <= 1 for item in observed_slots)
                and app.scheduler.snapshot()["admitted_total"] >= 1,
                f"executions={len(slot_executions)} running={sorted({i['running'] for i in observed_slots})}",
            )

            # the failure kinds have to be distinguishable
            failing = app.create_workflow("audit-failure")
            failing.default_snapshot_id = base.snapshot_id
            failing.start()
            raised: str | None = None
            kind: str | None = None
            try:
                await app.coordinator.submit_tool_call(
                    workflow_id=failing.workflow_id,
                    tool_name="fault_injector",
                    arguments={"mode": "raise", "message": "audit"},
                    snapshot_id=base.snapshot_id,
                )
            except Exception as exc:
                raised = type(exc).__name__
                kind = getattr(exc, "kind", None)
            failed_calls = app.repository.list_logical_calls(failing.workflow_id)
            record(
                "10.1",
                "区分工具错误",
                kind == "tool_error"
                and any(
                    call["status"] in ("FAILED", "CANCELLED")
                    for call in failed_calls
                ),
                f"kind={kind} status={[call['status'] for call in failed_calls]}",
            )
            record(
                "10.1",
                "工具错误不会进入共享协调",
                all(
                    call["share_group_id"] is None for call in failed_calls
                ),
            )
        finally:
            await app.close()

    try:
        asyncio.run(run())
    except Exception as exc:  # pragma: no cover - defensive
        record("17.2", "行为审计执行", False, str(exc))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ----------------------------------------------------------------------
# 19 the priority tiers
# ----------------------------------------------------------------------
#: The P2 clauses of requirement 19 and the state that has to be documented
#: for them.  The check is self maintaining: the documented table is the
#: contract, the code has to match it.
P2_EXPECTATIONS = {
    "可插拔调度策略": True,
    "会话内已完成结果复用": True,
    "Worker 预热池": True,
    "性能基线与回归守卫": True,
    "CI 工作流": True,
    "跨会话持久化结果缓存": False,
    "GPU 调度与 GPU KV Cache 管理": False,
    "分布式执行 / 多节点调度": False,
    "多用户权限与认证": False,
    "崩溃后的自动恢复执行": False,
}


def audit_priority_tiers() -> None:
    """Check §19 - the capability tiers and the documented P2 status.

    Every P2 clause has to be listed in ``KNOWN_LIMITATIONS.md`` with its
    state, and the state has to match the code: an implemented clause has to
    point at a module that exists, a clause that is not implemented must not
    have one.
    """

    text = read("docs/KNOWN_LIMITATIONS.md")
    section = text.split("## 0.")[1].split("\n## ")[0] if "## 0." in text else ""
    rows = {}
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("P2 条款", "---"):
            continue
        if set(cells[0]) <= set("-: "):
            continue
        rows[cells[0]] = (cells[1], cells[2])

    for clause, implemented in P2_EXPECTATIONS.items():
        row = rows.get(clause)
        if row is None:
            record("19", f"P2 条款 {clause}", False, "未在 KNOWN_LIMITATIONS 中列出")
            continue
        status, entry = row
        documented = "已实现" in status and "未实现" not in status
        if documented != implemented:
            record(
                "19",
                f"P2 条款 {clause}",
                False,
                f"文档声明与期望不符: {entry[:60]}",
            )
            continue
        if not implemented:
            record("19", f"P2 条款 {clause}", True, "已明确声明未实现")
            continue
        # an implemented clause has to point at code that exists
        import re

        paths = re.findall(r"`([\w./]+\.py)`", entry)
        missing = [path for path in paths if not (ROOT / path).exists()]
        record(
            "19",
            f"P2 条款 {clause}",
            not missing,
            f"缺少: {missing}" if missing else f"{len(paths)} 个入口存在",
        )

    # the P0 clauses are the acceptance contract - they must all be covered
    # by a behaviour check of this script
    p0_clauses = {
        "真实多 Agent 工作流": "17.2",
        "受控调用接入": "7.2",
        "真实工具进程执行": "9.4",
        "本地工作区快照": "6.2",
        "工具依赖追踪": "7.2",
        "In-flight Sharing": "17.2",
        "在线动态依赖验证": "8.5",
        "冲突分离": "8.8",
        "正确结果分发": "8.7",
        "Worker 并发控制": "9.2",
        "基线对比": "13.3",
        "SQLite": "16",
        "可运行 CLI": "15",
        "基础 Web UI": "14",
        "完整自动化测试": "17.2",
    }
    covered = {item["section"] for item in RESULTS}
    for clause, section in p0_clauses.items():
        record(
            "19",
            f"P0 条款 {clause}",
            section in covered,
            f"由 §{section} 覆盖" if section in covered else f"§{section} 没有检查",
        )


# ----------------------------------------------------------------------
# 13 the benchmark
# ----------------------------------------------------------------------
def audit_benchmark() -> None:
    """Check §13 - the comparison modes, the fairness rules and the metrics."""

    runner = read("agentfuse/benchmarks/runner.py")
    metrics = read("agentfuse/monitoring/metrics.py")
    document = read("docs/BENCHMARK.md")

    cli = read("agentfuse/cli.py")
    for mode in ("baseline", "online", "deferred"):
        record("13.1", f"比较模式 {mode}", f'"{mode}"' in runner or f'"{mode}"' in cli)
    record("13.1", "模式 C 可选且已实现", "deferred" in runner or "deferred" in cli)

    # the fairness rules of the module docstring
    for rule in (
        "same input repository and same snapshots",
        "same tool implementations",
        "same request sequence",
        "same worker limit, memory budget and timeout",
        "same random seed",
    ):
        record("13.2", f"公平性规则 {rule}", rule in runner)

    # the metrics of requirement 13.3
    for metric in (
        "makespan",
        "physical_executions",
        "saved_executions",
        "share_success_rate",
        "total_cpu_time",
        "peak_rss",
        "avg_share_wait",
        "validation_count",
        "validation_failures",
        "validation_ms",
        "detach_count",
        "first_conflict_at",
        "follower_independent_at",
    ):
        record("13.3", f"指标 {metric}", f'"{metric}"' in metrics)

    record("13.4", "Speedup 定义", "speedup" in runner)
    record("13.4", "负收益也必须显示", "speedup" in runner and "baseline" in runner)
    record("13.5", "对比页面展示指标", "bench-result" in read("agentfuse/web/app.js"))
    for section in ("场景", "方法", "工作负载", "指标定义", "公平性原则", "实测结果"):
        record("13", f"BENCHMARK 文档 {section}", section in document)


# ----------------------------------------------------------------------
# 11.3 the incomplete graph
# ----------------------------------------------------------------------