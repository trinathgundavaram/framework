"""The Step Functions workflow template (module/aws/.../stepfunctions/workflow.asl.json.tftpl)."""
import json
import pathlib
import re

import pytest

jsonata = pytest.importorskip("jsonata", reason="pip install jsonata-python")
TEMPLATE = (pathlib.Path(__file__).parents[1] / "module/aws/compliance_frameworks/compliance_batch_framework"
            / "stepfunctions/workflow.asl.json.tftpl")
pytestmark = pytest.mark.skipif(not TEMPLATE.exists(), reason="deployment module not in this checkout")

STEPS = {"BATCH_CREATION": (0.0625, 60, "1,2"), "FILE_LOAD": (1, 120, "2"), "OVERRIDE_DECISIONS": (0.0625, 30, "2"),
         "BATCH_CLOSE": (0.0625, 30, "1,2"), "NOTIFY": (0.0625, 30, "1,2")}


def esc(value):
    """What stepfunctions.tf passes."""
    return json.dumps(json.dumps(value))[1:-1]


def render(project, topic="arn:aws:sns:us-east-1:1:t", settings=None):
    values = {"workflow_label": f"project {project}" if project else "all projects", "timeout_seconds": "28800",
              "environment": "dev", "region": "us-east-1", "failure_topic_arn": topic,
              "runner_job_name": "compliance_batch_framework_runner_dev", "known_steps": esc(sorted(STEPS)),
              "step_capacity": esc({k: v[0] for k, v in STEPS.items()}),
              "step_timeout": esc({k: v[1] for k, v in STEPS.items()}),
              "step_fail_on": esc({k: v[2] for k, v in STEPS.items()}),
              "project_settings": esc({f"--FRAMEWORK_{k}": v for k, v in (settings or {}).items()}),
              "project_arg": esc({"--FW_PROJECT": project} if project else {})}
    text = TEMPLATE.read_text()
    assert set(re.findall(r"\$\{(\w+)\}", text)) == set(values)
    return json.loads(re.sub(r"\$\{(\w+)\}", lambda m: values[m.group(1)], text))


def ev(expr, states):
    assert expr.startswith("{%") and expr.endswith("%}"), expr
    return jsonata.Jsonata(expr[2:-2]).evaluate(None, {"states": states})


def alert(asl, err, execution_input):
    st, sns = asl["States"], None
    ctx = {"Execution": {"Input": execution_input, "Name": "exec-1", "Id": "arn:aws:states:us-east-1:1:execution:m:exec-1"}}
    if ev(st["Alert"]["Choices"][0]["Condition"], {"input": err}):
        args = st["NotifyFailure"]["Arguments"]
        sns = {"Subject": ev(args["Subject"], {"input": err, "context": ctx}),
               "Message": ev(args["Message"], {"input": err, "context": ctx})}
    return {"error": ev(st["Failed"]["Error"], {"input": err}), "cause": ev(st["Failed"]["Cause"], {"input": err}),
            "sns": sns}


def execute(asl, inp, fail_step=None):
    """Walk the workflow the way Step Functions runs it; returns the Glue calls and the outcome."""
    st = asl["States"]
    if not ev(st["Validate"]["Choices"][0]["Condition"], {"input": inp}):
        return [], alert(asl, ev(st["InvalidInput"]["Output"], {"input": inp}), inp)
    m, calls = st["RunSteps"], []
    for i, step in enumerate(ev(m["Items"], {"input": inp})):
        item = ev(m["ItemSelector"], {"input": inp, "context": {"Map": {"Item": {"Value": step, "Index": i}}}})
        task = m["ItemProcessor"]["States"]["RunStep"]
        a = task["Arguments"]
        calls.append({"JobName": a["JobName"], "MaxCapacity": ev(a["MaxCapacity"], {"input": item}),
                      "Timeout": ev(a["Timeout"], {"input": item}), "Arguments": ev(a["Arguments"], {"input": item}),
                      "TimeoutSeconds": ev(task["TimeoutSeconds"], {"input": item})})
        if step == fail_step:
            err = {"Error": "States.TaskFailed", "Cause": json.dumps({"JobRunState": "FAILED", "ErrorMessage": "exit 2"})}
            caught = ev(task["Catch"][0]["Output"], {"input": item, "errorOutput": err})
            failed = m["ItemProcessor"]["States"]["StepFailed"]
            outer = {"Error": failed["Error"], "Cause": ev(failed["Cause"], {"input": caught})}
            return calls, alert(asl, ev(m["Catch"][0]["Output"], {"input": inp, "errorOutput": outer}), inp)
    return calls, None


def test_steps_run_in_order_with_their_own_settings():
    calls, failure = execute(render("ODR", settings={"FILE_RULES_MODE": "GATE"}),
                             {"schedule": "daily", "steps": ["BATCH_CREATION", "FILE_LOAD"],
                              "run_type": "DAILY", "period": "PREV_DAY", "table": None})
    assert failure is None and [c["Arguments"]["--FW_MODULE"] for c in calls] == ["BATCH_CREATION", "FILE_LOAD"]
    assert calls[0]["Arguments"] == {"--FRAMEWORK_FILE_RULES_MODE": "GATE", "--FW_PROJECT": "ODR",
                                     "--FW_MODULE": "BATCH_CREATION", "--FW_FAIL_ON_EXIT_CODES": "1,2",
                                     "--FW_RUN_TYPE": "DAILY", "--FW_PERIOD": "PREV_DAY"}
    assert (calls[1]["MaxCapacity"], calls[1]["Timeout"], calls[1]["TimeoutSeconds"]) == (1, 120, 8100)
    assert calls[1]["Arguments"]["--FW_FAIL_ON_EXIT_CODES"] == "2"
    assert all(isinstance(v, str) for c in calls for v in c["Arguments"].values())


@pytest.mark.parametrize("steps", [["NOTIFY"], "NOTIFY"])
def test_a_single_step_is_still_a_list(steps):
    calls, failure = execute(render("ODR"), {"steps": steps})
    assert failure is None and len(calls) == 1


def test_all_projects_runs_without_a_project_and_passes_as_of():
    calls, _ = execute(render(None), {"steps": ["BATCH_CLOSE"], "as_of": "2026-09-01"})
    assert calls[0]["Arguments"] == {"--FW_MODULE": "BATCH_CLOSE", "--FW_FAIL_ON_EXIT_CODES": "1,2",
                                     "--FW_AS_OF": "2026-09-01"}


@pytest.mark.parametrize("bad", [{}, {"steps": []}, {"steps": ["FILE_LOAD", "NOPE"]}, {"steps": "nope"}])
def test_bad_input_runs_nothing_and_alerts(bad):
    calls, failure = execute(render("ODR"), bad)
    assert calls == [] and failure["error"] == "InvalidInput" and "non-empty list of" in failure["cause"]
    assert failure["sns"]["Subject"] == "[FAILED] project ODR dev: InvalidInput"


def test_a_failed_step_stops_the_rest_and_the_alert_names_project_step_and_schedule():
    calls, failure = execute(render("ODR"), {"schedule": "odr-15min", "steps": ["FILE_LOAD", "BATCH_CLOSE", "NOTIFY"]},
                             fail_step="BATCH_CLOSE")
    assert [c["Arguments"]["--FW_MODULE"] for c in calls] == ["FILE_LOAD", "BATCH_CLOSE"]
    assert failure["error"] == "StepFailed" and failure["cause"].startswith("step BATCH_CLOSE (2 of 3) failed")
    sns = failure["sns"]
    assert sns["Subject"] == "[FAILED] project ODR dev: StepFailed" and len(sns["Subject"]) <= 100
    assert "Schedule: odr-15min" in sns["Message"] and "step BATCH_CLOSE (2 of 3)" in sns["Message"]
    assert "executions/details/arn:aws:states" in sns["Message"]


def test_without_an_alert_topic_the_failure_is_still_recorded():
    _, failure = execute(render("ODR", topic=""), {"steps": ["NOPE"]})
    assert failure["sns"] is None and failure["error"] == "InvalidInput"
