# 原计划逐项复核

原文见 REQUIREMENTS.zh-CN.md。以下映射要求对应测试确实通过，不能仅凭函数名存在。

| 小节 | 内容 | 已执行的测试 |
|---|---|---|
| 2.1 | 运行环境 | `test_the_worker_reports_its_real_pid` |
| 2.2 | 完全本地运行 | `test_the_front_end_does_not_load_anything_from_the_network` |
| 2.3 | 开发周期 | `test_the_catalogue_matches_the_runtime` |
| 3.1 | 不重新实现已有系统 | `test_a_foreign_framework_can_run_a_tool_call` |
| 3.2 | 职责划分 | `test_the_two_agents_keep_their_own_identity` |
| 3.3 | 核心架构 | `test_two_scripted_agents_produce_a_real_execution_graph` |
| 4.1 | Workflow | `test_the_execution_is_written_to_the_database` |
| 4.2 | Agent | `test_an_undetermined_agent_identity_is_recorded` |
| 4.3 | AgentInvocation | `test_two_invocations_of_the_same_agent_stay_distinct` |
| 4.4 | LogicalToolCall | `test_the_execution_is_written_to_the_database` |
| 4.5 | PhysicalExecution | `test_the_physical_execution_is_recorded` |
| 4.6 | WorkspaceSnapshot | `test_snapshots_are_immutable` |
| 4.7 | DependencyRecord | `test_the_dependency_ledger_records_every_read` |
| 4.8 | ShareGroup | `test_the_share_group_records_its_participants` |
| 4.9 | ValidationRecord | `test_a_late_joiner_validates_the_existing_ledger` |
| 5.1 | 统一事件协议 | `test_event_ids_are_unique_and_sequences_are_stable` |
| 5.2 | 身份绑定 | `test_an_undetermined_agent_identity_is_recorded` |
| 5.3 | 因果关系 | `test_the_graph_only_contains_observed_relations` |
| 6.1 | 基本目标 | `test_fork_creates_an_independent_branch` |
| 6.2 | 实现方式 | `test_snapshots_are_immutable` |
| 6.3 | 文件依赖表示 | `test_read_text_reports_a_file_read_dependency` |
| 7.1 | 工具注册 | `test_the_four_controlled_tools_are_registered` |
| 7.2 | 工具执行接口 | `test_read_text_reports_a_file_read_dependency` |
| 7.3 | 必须实现的本地工具 | `test_the_ast_analyzer_produces_real_results` |
| 7.4 | 确定性要求 | `test_the_same_seed_produces_the_same_results` |
| 8.1 | 候选共享识别 | `test_same_arguments_produce_the_same_key` |
| 8.2 | 共享执行状态机 | `test_illegal_group_transitions_are_rejected` |
| 8.3 | 第一个调用到达 | `test_two_equivalent_calls_share_a_single_physical_execution` |
| 8.4 | 后续调用到达 | `test_the_candidate_event_is_published` |
| 8.5 | 在线动态依赖验证 | `test_a_conflicting_dependency_detaches_the_follower` |
| 8.6 | 增量验证语义 | `test_the_follower_validates_the_dependencies_that_come_later` |
| 8.7 | 最终提交验证 | `test_a_follower_that_fails_the_final_validation_gets_no_result` |
| 8.8 | 分离机制 | `test_the_detached_follower_uses_its_own_snapshot` |
| 8.9 | 结果缓存边界 | `test_an_identical_request_is_served_from_the_cache` |
| 8.10 | 正确性原则 | `test_the_shared_results_are_identical_to_the_baseline` |
| 9.1 | 资源控制器 | `test_the_resource_snapshot_exposes_the_limits` |
| 9.2 | CPU 并发控制 | `test_the_worker_limit_is_enforced` |
| 9.3 | 内存控制 | `test_the_memory_budget_blocks_new_executions` |
| 9.4 | 工具进程执行 | `test_a_pooled_worker_is_reused` |
| 9.5 | 执行调度 | `test_the_queue_is_fifo` |
| 10.1 | Leader 执行失败 | `test_the_participants_of_a_failed_group_are_failed` |
| 10.2 | Follower 取消 | `test_a_cancelled_follower_does_not_stop_the_leader` |
| 10.3 | Leader 逻辑调用取消 | `test_a_cancelled_leader_keeps_the_follower_alive` |
| 10.4 | 所有参与者取消 | `test_cancelling_every_participant_terminates_the_execution` |
| 10.5 | Runtime 进程中断 | `test_an_interrupted_run_is_marked_with_its_kind` |
| 11.1 | 图节点 | `test_the_graph_records_shared_executions` |
| 11.2 | 图边 | `test_the_delivery_event_is_published_exactly_once` |
| 11.3 | 不完整图处理 | `test_out_of_order_events_do_not_create_wrong_edges` |
| 11.4 | 可观察状态 | `test_the_dashboard_renders_the_real_data` |
| 12.1 | Scripted Agent | `test_the_shared_scenario_shares_one_execution` |
| 12.2 | 场景一：完全共享 | `test_the_shared_scenario_shares_one_execution` |
| 12.3 | 场景二：不同工作区但仍能共享 | `test_the_different_workspace_scenario_shares` |
| 12.4 | 场景三：动态依赖冲突 | `test_the_detection_happens_while_the_leader_is_still_running` |
| 12.5 | 场景四：多个 Agent 并行与汇合 | `test_the_aggregator_only_starts_after_the_workers_finished` |
| 12.6 | 场景五：取消和失败 | `test_the_cancel_failure_scenario_covers_every_case` |
| 13.1 | 比较模式 | `test_the_benchmark_uses_a_fixed_seed` |
| 13.2 | 实验公平性 | `test_the_fairness_comparison_of_a_real_run` |
| 13.3 | 必须记录的指标 | `test_the_comparison_reports_a_real_reduction` |
| 13.4 | Speedup | `test_the_comparison_reports_a_real_reduction` |
| 13.5 | 对比结果 | `test_the_benchmark_endpoint_runs_a_comparison` |
| 14.1 | 产品目标 | `test_the_dashboard_is_served` |
| 14.2 | Dashboard | `test_the_overview_endpoint` |
| 14.3 | Workflow Detail | `test_the_workflow_endpoints` |
| 14.4 | Execution Timeline | `test_the_workflow_endpoints` |
| 14.5 | Dependency Inspector | `test_the_execution_endpoints` |
| 14.6 | Benchmark 页面 | `test_the_benchmark_endpoint_runs_a_comparison` |
| 17.1 | 图重建测试 | `test_the_graph_only_contains_observed_relations` |
| 17.2 | 共享正确性测试 | `test_two_equivalent_calls_share_a_single_physical_execution` |
| 17.3 | 动态验证测试 | `test_a_conflicting_dependency_detaches_the_follower` |
| 17.4 | 并发测试 | `test_many_concurrent_identical_calls_create_one_execution` |
| 17.5 | 资源测试 | `test_the_worker_limit_is_enforced` |
| 17.6 | 快照测试 | `test_snapshots_are_immutable` |
| 17.7 | 集成测试 | `test_a_single_agent_call_produces_one_execution` |
| 21.1 | 共享正确性与运行并发的竞态 | `test_no_logical_call_is_delivered_twice` |
| 21.2 | 快照不变性 | `test_a_modified_snapshot_is_not_shared` |
| 21.3 | 依赖追踪完整性 | `test_a_bypassing_tool_is_never_shared` |
| 21.4 | 实验真实性 | `test_the_comparison_reports_a_real_reduction` |
| 21.5 | Agent Framework 无关性 | `test_a_foreign_framework_can_run_a_tool_call` |

P0/P1 原计划验收与 P2 扩展分别核验。P2 按用户进一步确认的 LangGraph、Linux 容器命令范围交付，见 P2_COMPLETION.md；原始需求未改写。
