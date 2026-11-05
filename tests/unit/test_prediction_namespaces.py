from agentfuse.runtime.prediction import PathPredictor


def test_tool_names_do_not_create_agent_forecast_edges():
    predictor = PathPredictor()
    predictor.observe('tool-stream', 'Planner')
    predictor.observe('tool-stream', 'read_text')
    assert predictor.forecast('Planner') == [dict(path=['Planner'], probability=1.0)]


def test_agent_labels_and_tool_names_learn_separate_distributions():
    predictor = PathPredictor()
    predictor.observe_invocation('workflow', 'parent', 'Planner')
    predictor.observe_invocation('workflow', 'child', 'Reviewer', 'parent')
    predictor.observe('tool-stream', 'Planner')
    predictor.observe('tool-stream', 'read_text')
    assert predictor.predict('Planner') == [dict(tool='read_text', probability=1.0, observations=1)]
    assert predictor.forecast('Planner') == [dict(path=['Planner', 'Reviewer'], probability=1.0)]
