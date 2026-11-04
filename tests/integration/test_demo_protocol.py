"""Requirement 12.1 - the demo must not have its own execution path.

The scripted agents are a convenience for the demo, not a second runtime: they
enter AgentFuse through the **same** adapter interface that an external
framework uses.  The test checks both sides of that claim:

* statically: the scenarios do not reach into the coordinator themselves, they
  use the adapter;
* behaviourally: a scenario produces the events of the adapter protocol - an
  agent is bound, an invocation starts and the call goes through the runtime.
"""

from __future__ import annotations

import pathlib
import re

from agentfuse.core.state import EventType

SCENARIOS = pathlib.Path("agentfuse/scenarios/definitions.py")


def test_the_demo_has_no_own_execution_path():
    """The scenarios must not call the coordinator directly."""

    source = SCENARIOS.read_text(encoding="utf-8")
    # the adapter is what the scenarios use
    assert "ScriptedAdapter(" in source, (
        "the scenarios have to use the adapter - otherwise the demo would be "
        "a second runtime"
    )
    # and they must not reach into the runtime behind the adapter's back
    direct = re.findall(r"\.coordinator\.\w+", source)
    assert not direct, (
        "the scenarios reach into the coordinator instead of using the "
        f"adapter: {direct}"
    )
    # the tool is called through the agent, not through the coordinator
    assert "coordinator.submit_tool_call" not in source, (
        "the scenarios submit the tool call themselves - the adapter has to do it"
    )


async def test_the_demo_produces_the_events_of_the_adapter_protocol(app, snapshot):
    """A scenario produces the events that the adapter protocol produces."""

    from agentfuse.scenarios.definitions import scenario_shared

    # the scenario creates the workflow itself - the test reads its events
    result = await scenario_shared(app, app.mode, {})
    workflow_id = result.workflow_id

    events = app.repository.list_events(workflow_id)
    types = {str(row["event_type"]) for row in events}
    for expected in (
        EventType.AGENT_BOUND,
        EventType.AGENT_INVOKED,
        EventType.TOOL_CALL_REQUESTED,
        EventType.TOOL_EXECUTION_STARTED,
        EventType.TOOL_RESULT_DELIVERED,
    ):
        assert str(expected) in types, (
            f"the demo did not produce {expected} - the events are {sorted(types)}"
        )

    # the agents of the demo are bound through the adapter, so they carry an
    # identity and an invocation
    agents = app.repository.list_agents(workflow_id)
    assert agents, "the demo has to bind its agents"
    assert all(row["agent_id"] for row in agents), agents
    invocations = app.repository.list_invocations(workflow_id)
    assert invocations, "the demo has to start invocations"
