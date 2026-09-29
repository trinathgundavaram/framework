"""Glue entry point: runs one framework module or command per Glue run."""
import os
import shlex
import sys

CONTEXT = (("project", "FW_PROJECT", "--project"), ("run_type", "FW_RUN_TYPE", "--run-type"),
           ("period", "FW_PERIOD", "--period"), ("table", "FW_TABLE", "--table"))


def glue_arguments(argv: list) -> dict:
    """Glue passes `--KEY value` pairs."""
    out, i = {}, 1
    while i < len(argv):
        item = argv[i]
        if item.startswith("--"):
            key = item[2:]
            if "=" in key:
                key, value = key.split("=", 1)
                out[key] = value
                i += 1
                continue
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and (not nxt.startswith("--") or " " in nxt):
                out[key] = nxt
                i += 2
                continue
            out[key] = ""
        i += 1
    return out


def build_command(args: dict, module_params) -> tuple:
    """(CLI argv, context values the module does not take)."""
    module, raw = args.get("FW_MODULE", "").strip(), args.get("FW_ARGS", "").strip()
    if module and raw:
        raise SystemExit("give --FW_MODULE (a module) or --FW_ARGS (a command), not both")
    if not module and not raw:
        raise SystemExit("--FW_MODULE (e.g. FILE_LOAD) or --FW_ARGS (e.g. 'init-db') is required")
    dropped = []
    if module:
        accepted = module_params(module)
        command = ["run", "--module", module]
        for name, key, flag in CONTEXT:
            value = args.get(key, "").strip()
            if not value:
                continue
            if name in accepted:
                command += [flag, value]
            else:
                dropped.append(f"{flag} {value}")
    else:
        command = shlex.split(raw)
    if args.get("FW_AS_OF", "").strip():
        command += ["--as-of", args["FW_AS_OF"].strip()]
    return command + shlex.split(args.get("FW_EXTRA_ARGS", "")), dropped


def main(argv: list) -> int:
    args = glue_arguments(argv)
    for key, value in args.items():
        if key.startswith("FRAMEWORK_"):
            os.environ[key] = value
    fail_on = {int(c) for c in args.get("FW_FAIL_ON_EXIT_CODES", "1,2").split(",") if c.strip()}

    from framework.cli import main as framework_main
    from framework.modules import resolve_module

    command, dropped = build_command(args, lambda name: set(resolve_module(name).params))
    print(f"module={args.get('FW_MODULE') or '-'} project={args.get('FW_PROJECT') or '*'} "
          f"command: framework {' '.join(shlex.quote(c) for c in command)}", flush=True)
    if dropped:
        print(f"not used by {args['FW_MODULE']}: {', '.join(dropped)}", flush=True)
    rc = framework_main(command)
    print(f"framework exit code {rc}" + (" (fails the run)" if rc in fail_on else ""), flush=True)
    return rc if rc in fail_on else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
