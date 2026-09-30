"""The Glue runner's entry script (module/aws/.../common/glue_framework_entry.py)."""
import importlib.util
import pathlib

import pytest

from framework.modules import resolve_module

ENTRY = pathlib.Path(__file__).parents[1] / "module/aws/compliance_frameworks/compliance_batch_framework/common/glue_framework_entry.py"
pytestmark = pytest.mark.skipif(not ENTRY.exists(), reason="deployment module not in this checkout")


@pytest.fixture(scope="module")
def entry():
    spec = importlib.util.spec_from_file_location("glue_framework_entry", ENTRY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def params(name):
    return set(resolve_module(name).params)


def test_glue_arguments(entry):
    got = entry.glue_arguments(["x", "--JOB_NAME", "j", "--FW_MODULE", "FILE_LOAD", "--FW_EXTRA_ARGS",
                                "--bucket b --prefix p/", "--flag", "--FRAMEWORK_DB_SECRET_NAME", "s", "--k=v"])
    assert got == {"JOB_NAME": "j", "FW_MODULE": "FILE_LOAD", "FW_EXTRA_ARGS": "--bucket b --prefix p/",
                   "flag": "", "FRAMEWORK_DB_SECRET_NAME": "s", "k": "v"}


@pytest.mark.parametrize("module, expected, dropped", [
    ("BATCH_CREATION", ["run", "--module", "BATCH_CREATION", "--project", "ODR", "--run-type", "DAILY",
                        "--period", "PREV_DAY", "--table", "ROPENS", "--as-of", "2026-09-01"], []),
    ("FILE_LOAD", ["run", "--module", "FILE_LOAD", "--project", "ODR", "--as-of", "2026-09-01"],
     ["--run-type DAILY", "--period PREV_DAY", "--table ROPENS"]),
    ("BATCH_CLOSE", ["run", "--module", "BATCH_CLOSE", "--project", "ODR", "--run-type", "DAILY",
                     "--table", "ROPENS", "--as-of", "2026-09-01"], ["--period PREV_DAY"]),
])
def test_one_context_serves_every_step(entry, module, expected, dropped):
    args = {"FW_MODULE": module, "FW_PROJECT": "ODR", "FW_RUN_TYPE": "DAILY", "FW_PERIOD": "PREV_DAY",
            "FW_TABLE": "ROPENS", "FW_AS_OF": "2026-09-01"}
    assert entry.build_command(args, params) == (expected, dropped)


def test_command_mode_and_errors(entry):
    assert entry.build_command({"FW_ARGS": "close-batch --btch-id 'a b' --closed-by me"}, params)[0] == \
        ["close-batch", "--btch-id", "a b", "--closed-by", "me"]
    assert entry.build_command({"FW_MODULE": "NOTIFY", "FW_PROJECT": "", "FW_ARGS": ""}, params)[0] == \
        ["run", "--module", "NOTIFY"]
    with pytest.raises(SystemExit, match="not both"):
        entry.build_command({"FW_MODULE": "NOTIFY", "FW_ARGS": "health"}, params)
    with pytest.raises(SystemExit, match="required"):
        entry.build_command({"FW_MODULE": "", "FW_ARGS": ""}, params)
    with pytest.raises(Exception, match="unknown module"):
        entry.build_command({"FW_MODULE": "NOPE"}, params)


def test_exit_codes_decide_whether_the_glue_run_fails(entry, monkeypatch):
    import framework.cli
    monkeypatch.setattr(framework.cli, "main", lambda argv: 1)
    base = ["x", "--FW_MODULE", "FILE_LOAD", "--FW_PROJECT", "ODR"]
    assert entry.main(base + ["--FW_FAIL_ON_EXIT_CODES", "2"]) == 0
    assert entry.main(base + ["--FW_FAIL_ON_EXIT_CODES", "1,2"]) == 1
    assert entry.main(base) == 1
