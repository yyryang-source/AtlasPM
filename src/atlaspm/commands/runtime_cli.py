"""Unified workstation setup, diagnostics and bounded research operation."""
from atlaspm.framework import configuration

COMMANDS = {"system"}


def register_parsers(sub):
    p = sub.add_parser("system", help="Unified local setup, health and research runtime")
    p.add_argument("action", choices=("setup", "doctor", "status", "monitor", "offline",
                                     "stop", "takeover", "resume", "recover"))
    p.add_argument("--config", default=str(configuration.DEFAULT_PATH))
    p.add_argument("--env-file", help="Ignored local .env file; OS environment takes precedence")
    p.add_argument("--allow-network", action="store_true",
                   help="Permit only configured read-only source polling in monitor")
    p.add_argument("--allow-local-ai", action="store_true",
                   help="Permit configured loopback model inference in monitor")


def run(args):
    from atlaspm.framework import system_runtime as runtime
    from atlaspm.framework.orchestrator import set_control, offline_release
    if args.action != "monitor" and (args.allow_network or args.allow_local_ai):
        raise ValueError("Runtime opt-ins require monitor")
    if args.action == "setup":
        return runtime.setup(args.config)
    if args.action in {"stop", "takeover", "resume"}:
        return {**set_control(args.action), "status": args.action.upper(), "exit_code": 0}
    if args.action == "status":
        return runtime.status()
    config = configuration.load(args.config)
    with configuration.environment(config, env_file=args.env_file):
        if args.action == "doctor":
            return runtime.doctor(config)
        if args.action == "recover":
            return runtime.recover()
        if args.action == "offline":
            report = offline_release(checkpoint=config["paths"]["checkpoint"])
            return {**report, "exit_code": int(report["status"] != "OFFLINE_FIXTURE_PASS")}
        return runtime.monitor(config, allow_network=args.allow_network,
                               allow_local_ai=args.allow_local_ai)
