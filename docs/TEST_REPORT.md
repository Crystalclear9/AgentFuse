# Test Report

## 清理后的复验

Windows Python 3.11 全量 379 项，368 通过、11 跳过，启用了项目内 Chromium。跳过项涉及按需安装的框架 SDK、模型、容器镜像及 Windows 符号链接权限。WSL2 共享执行 14 项通过，需求审计 325/325，交付验收 44/44。验收结束后确认临时工作区自动回收。

## 最近一次含可选框架的完整验证

| 检查 | 环境与结果 |
|---|---|
| 全量测试 | Windows Python 3.11，380 项，371 通过、9 跳过。启用项目内 Chromium、LangGraph 和 LangChain |
| 定向回归 | Windows 76 通过、1 跳过；WSL2 13 通过 |
| 需求审计与交付验收 | 325/325 与 44/44 |
| 远端 CI | 18/18，包括 Windows、Ubuntu、macOS、Worker 矩阵、变异守卫、浏览器及十分钟稳定性观察 |

以上数量对应运行时代码验证提交 `d1e10a5`。Windows 跳过项涉及符号链接权限及未加载的本地 GGUF 模型和 Linux 审计镜像。机器可读结果见 [最近一次验证](verification/latest.json) 与 [CI 运行](https://github.com/Crystalclear9/AgentFuse/actions/runs/41992291019)。

## 测试命令

在仓库根目录执行。使用现成的项目内环境，或按 [工具安装说明](LOCAL_TOOLS.md) 恢复。

```powershell
. ./scripts/enter-local.ps1
python -m pytest tests -q
python scripts/audit_requirements.py
python scripts/acceptance_check.py
```

需要真实浏览器时，在运行 pytest 前设置 `$env:AGENTFUSE_BROWSER_TEST = '1'`。框架和 P2 环境的准备方法见 [框架接入指南](ADAPTER_GUIDE.md) 和 [P2 验收](P2_COMPLETION.md)。WSL2 可使用 `source scripts/enter-local.sh`。

## 8. 尚未验证的功能

- **真实外部 Agent 框架接入**已实测 LangGraph、LangChain 以及 Python、Node.js 的通用进程桥接。AutoGen、CrewAI 等 SDK 尚未逐一验证。
- **长时间运行的资源稳定性**有十分钟 CI 和历史 45 分钟本机记录。数小时或生产负载的持续运行尚未验证。
- **磁盘故障 / 断电时的数据一致性**已覆盖强杀进程和截断 WAL，尚未测试实际存储设备断电或故障。
- **符号链接逃逸在 Windows 上未执行**，本机创建符号链接需要额外权限。Linux/WSL2 已执行该检查。
- 分布式 Worker 已验证同机多进程、TLS 和结果传输。多台物理机的部署尚未实测。

## 9. 性能与历史证据

[Benchmark](BENCHMARK.md) 记录工作负载、性能和正确性指标。[验收证据索引](verification/README.md) 提供性能基线、P2 专项证据及历史原始报告的 Git 路径。

## 10. 规范逐条追溯表

下表是机器生成的：每个需求小节都指向一个**被执行**的东西
（测试、验收项或审计检查）。生成器：`python scripts/audit_requirements.py --trace`。

| 需求小节 | 证据 |
|---|---|
| §4 | 测试 test_the_execution_is_written_to_the_database (+1) |
| §12 | 测试 test_the_shared_scenario_shares_one_execution (+1) |
| §13 | 测试 test_the_comparison_reports_a_real_reduction (+1) |
| §14 | 测试 test_the_dashboard_is_served (+1) |
| §15 | 测试 test_the_cli_is_usable (+1) |
| §16 | 测试 test_a_fresh_database_has_the_current_schema (+1) |
| §18 | 测试 test_the_cli_runs_a_scenario |
| §19 | 测试 test_the_code_defaults_match_the_configuration (+1) |
| §20 | 测试 test_the_engineering_layout_is_complete (+1) |
| §22 | 测试 test_the_documents_have_the_required_sections (+1) |
| §23 | 可在单台电脑上启动 -> test_a_fresh_database_has_the_current_schema (+40) |
| §24 | 测试 test_the_development_log_records_every_stage |
| §25 | 测试 test_the_cli_is_usable |
| §2.1 | 测试 test_the_worker_reports_its_real_pid |
| §2.2 | 测试 test_the_front_end_does_not_load_anything_from_the_network (+1) |
| §2.3 | 测试 test_the_catalogue_matches_the_runtime |
| §3.1 | 测试 test_a_foreign_framework_can_run_a_tool_call (+1) |
| §3.2 | 测试 test_the_two_agents_keep_their_own_identity |
| §3.3 | 测试 test_two_scripted_agents_produce_a_real_execution_graph |
| §4.1 | 测试 test_the_execution_is_written_to_the_database |
| §4.2 | 测试 test_an_undetermined_agent_identity_is_recorded |
| §4.3 | 测试 test_two_invocations_of_the_same_agent_stay_distinct |
| §4.4 | 测试 test_the_execution_is_written_to_the_database |
| §4.5 | 测试 test_the_physical_execution_is_recorded |
| §4.6 | 测试 test_snapshots_are_immutable |
| §4.7 | 测试 test_the_dependency_ledger_records_every_read |
| §4.8 | 测试 test_the_share_group_records_its_participants (+1) |
| §4.9 | 测试 test_a_late_joiner_validates_the_existing_ledger |
| §5.1 | 测试 test_event_ids_are_unique_and_sequences_are_stable (+1) |
| §5.2 | 测试 test_an_undetermined_agent_identity_is_recorded (+1) |
| §5.3 | 测试 test_the_graph_only_contains_observed_relations |
| §6.1 | 测试 test_fork_creates_an_independent_branch |
| §6.2 | 测试 test_snapshots_are_immutable (+1) |
| §6.3 | 测试 test_read_text_reports_a_file_read_dependency |
| §7.1 | 测试 test_the_four_controlled_tools_are_registered (+1) |
| §7.2 | 测试 test_read_text_reports_a_file_read_dependency (+1) |
| §7.3 | 测试 test_the_ast_analyzer_produces_real_results (+1) |
| §7.4 | 测试 test_the_same_seed_produces_the_same_results (+1) |
| §8.1 | 测试 test_same_arguments_produce_the_same_key (+1) |
| §8.2 | 测试 test_illegal_group_transitions_are_rejected (+1) |
| §8.3 | 测试 test_two_equivalent_calls_share_a_single_physical_execution |
| §8.4 | 测试 test_the_candidate_event_is_published |
| §8.5 | 测试 test_a_conflicting_dependency_detaches_the_follower (+1) |
| §8.6 | 测试 test_the_follower_validates_the_dependencies_that_come_later |
| §8.7 | 测试 test_a_follower_that_fails_the_final_validation_gets_no_result (+1) |
| §8.8 | 测试 test_the_detached_follower_uses_its_own_snapshot (+1) |
| §8.9 | 测试 test_an_identical_request_is_served_from_the_cache |
| §9.1 | 测试 test_the_resource_snapshot_exposes_the_limits (+1) |
| §9.2 | 测试 test_the_worker_limit_is_enforced (+1) |
| §9.3 | 测试 test_the_memory_budget_blocks_new_executions (+1) |
| §9.4 | 测试 test_a_pooled_worker_is_reused (+1) |
| §9.5 | 测试 test_the_queue_is_fifo |
| §10.1 | 测试 test_the_participants_of_a_failed_group_are_failed (+1) |
| §10.2 | 测试 test_a_cancelled_follower_does_not_stop_the_leader |
| §10.3 | 测试 test_a_cancelled_leader_keeps_the_follower_alive |
| §10.4 | 测试 test_cancelling_every_participant_terminates_the_execution (+1) |
| §10.5 | 测试 test_an_interrupted_run_is_marked_with_its_kind (+1) |
| §11.1 | 测试 test_the_graph_records_shared_executions |
| §11.2 | 测试 test_the_delivery_event_is_published_exactly_once |
| §11.3 | 测试 test_out_of_order_events_do_not_create_wrong_edges (+1) |
| §11.4 | 测试 test_the_dashboard_renders_the_real_data |
| §12.1 | 测试 test_the_shared_scenario_shares_one_execution |
| §12.2 | 测试 test_the_shared_scenario_shares_one_execution |
| §12.3 | 测试 test_the_different_workspace_scenario_shares |
| §12.4 | 测试 test_the_detection_happens_while_the_leader_is_still_running (+1) |
| §12.5 | 测试 test_the_aggregator_only_starts_after_the_workers_finished |
| §12.6 | 测试 test_the_cancel_failure_scenario_covers_every_case (+1) |
| §13.1 | 测试 test_the_benchmark_uses_a_fixed_seed (+1) |
| §13.2 | 测试 test_the_fairness_comparison_of_a_real_run (+1) |
| §13.3 | 测试 test_the_comparison_reports_a_real_reduction (+1) |
| §13.4 | 测试 test_the_comparison_reports_a_real_reduction (+1) |
| §13.5 | 测试 test_the_benchmark_endpoint_runs_a_comparison (+1) |
| §14.1 | 测试 test_the_dashboard_is_served |
| §14.2 | 测试 test_the_overview_endpoint |
| §14.3 | 测试 test_the_workflow_endpoints |
| §14.4 | 测试 test_the_workflow_endpoints |
| §14.5 | 测试 test_the_execution_endpoints |
| §14.6 | 测试 test_the_benchmark_endpoint_runs_a_comparison |
| §16.1 | 测试 test_a_fresh_database_has_the_current_schema |
| §16.2 | 测试 test_a_transaction_is_committed_at_once |
| §16.3 | 测试 test_the_schema_declares_the_foreign_keys |
| §16.4 | 测试 test_no_logical_call_is_delivered_twice |
| §16.5 | 测试 test_a_burst_of_events_does_not_block_the_event_loop (+1) |
| §16.6 | 测试 test_the_workflow_endpoints |
| §16.7 | 测试 test_a_fresh_database_has_the_current_schema |
| §16.8 | 测试 test_an_old_database_is_migrated |
| §17.1 | 主动不会因为并发而混淆 -> test_the_two_agents_keep_their_own_identity (+10) |
| §17.2 | 相同参数和相同依赖可以共享 -> test_two_equivalent_calls_share_a_single_physical_execution (+16) |
| §17.3 | Leader 读取文件后立即产生依赖事件 -> test_the_dependency_event_precedes_the_execution_end (+16) |
| §17.4 | 多个 Agent 同时提交相同调用 -> test_many_concurrent_identical_calls_create_one_execution (+15) |
| §17.5 | 运行 Worker 数不超过配置上限 -> test_the_worker_limit_is_enforced (+14) |
| §17.6 | 不同分支文件状态互不影响 -> test_fork_creates_an_independent_branch (+13) |
| §17.7 | 单 Agent 调用工具 -> test_a_single_agent_call_produces_one_execution (+17) |
| §21.1 | 测试 test_no_logical_call_is_delivered_twice (+1) |
| §21.2 | 测试 test_a_modified_snapshot_is_not_shared (+1) |
| §21.3 | 测试 test_a_bypassing_tool_is_never_shared (+1) |
| §21.4 | 测试 test_the_comparison_reports_a_real_reduction (+1) |
| §21.5 | 测试 test_a_foreign_framework_can_run_a_tool_call (+1) |
| §8.10 | 测试 test_the_shared_results_are_identical_to_the_baseline |
