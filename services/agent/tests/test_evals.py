"""The deterministic `parallel_tool_use` eval metric, on synthetic invocations."""

from google.adk.evaluation.eval_case import IntermediateData, Invocation
from google.adk.evaluation.eval_metrics import EvalMetric
from google.genai import types

from scenepilot.evals.metrics import parallel_tool_use, score_invocation


def _inv(queries: list[str] | None, cite: bool, answer_url: str = "https://digitalsky.dgca.gov.in/airspace-map") -> Invocation:
    calls = [types.FunctionCall(name="parallel_search", args={"objective": "drone rules Mumbai", "search_queries": queries})] if queries is not None else []
    responses = [types.FunctionResponse(name="parallel_search", response={"search_run_id": "search_0123456789", "results": [{"url": "https://digitalsky.dgca.gov.in/airspace-map", "title": "Digital Sky"}]})] if queries is not None else []
    text = f"FACTS: red zones need permission ({answer_url}). RECOMMENDATION: apply early." if cite else "FACTS: red zones need permission. RECOMMENDATION: apply early."
    return Invocation(user_content=types.Content(role="user", parts=[types.Part(text="drone?")]), final_response=types.Content(role="model", parts=[types.Part(text=text)]), intermediate_data=IntermediateData(tool_uses=calls, tool_responses=responses))


def test_metric_rewards_by_the_book_parallel_use():
    good = _inv(["Mumbai drone permission filming", "DGCA Digital Sky red zone", "Mumbai Police aerial NOC"], cite=True)
    assert score_invocation(good)[0] == 1.0
    bad_queries = _inv(['site:dgca.gov.in "drone rules"', "x", "a very long sentence that is not a keyword query"], cite=True)
    assert score_invocation(bad_queries)[0] == 0.7
    no_cite = _inv(["Mumbai drone permission filming", "DGCA Digital Sky red zone", "Mumbai Police aerial NOC"], cite=False)
    assert score_invocation(no_cite)[0] == 0.7
    no_tool = _inv(None, cite=False)
    assert score_invocation(no_tool)[0] == 0.0
    # our tools' own citation style — [search_<id>#n] — counts as a grounded citation
    label_cite = _inv(["a b c", "d e f", "g h i"], cite=False)
    label_cite.final_response = types.Content(role="model", parts=[types.Part(text="FACTS: red zones need permission [search_0123456789#1].")])
    assert score_invocation(label_cite)[0] == 1.0


def test_metric_function_signature_and_threshold():
    result = parallel_tool_use(EvalMetric(metric_name="parallel_tool_use", threshold=0.8), [_inv(["a b c", "d e f", "g h i"], cite=True)], None, None)
    assert result.overall_score == 1.0 and result.overall_eval_status.name == "PASSED"
    result = parallel_tool_use(EvalMetric(metric_name="parallel_tool_use", threshold=0.8), [_inv(None, cite=False)], None, None)
    assert result.overall_eval_status.name == "FAILED"
