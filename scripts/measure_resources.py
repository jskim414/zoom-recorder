"""Read-only sampling of an existing process tree; does not launch or record audio."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import time

import psutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seconds < 1 or args.warmup < 0:
        parser.error("seconds must be positive and warmup nonnegative")
    process = psutil.Process(args.pid)
    cpu_count = psutil.cpu_count() or 1
    total_ram = psutil.virtual_memory().total
    samples = []
    tracked = {p.pid: p for p in [process, *process.children(recursive=True)]}
    for member in tracked.values():
        member.cpu_percent()
    started = time.monotonic()
    for index in range(args.warmup + args.seconds):
        time.sleep(1)
        members = [process, *process.children(recursive=True)]
        cpu = 0.0
        rss = private = threads = 0
        for member in members:
            if member.pid not in tracked:
                tracked[member.pid] = member
                member.cpu_percent()
            member = tracked[member.pid]
            cpu += member.cpu_percent()
            memory = member.memory_info()
            rss += memory.rss
            private += getattr(memory, "private", memory.vms)
            threads += member.num_threads()
        samples.append({
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "phase": "warmup" if index < args.warmup else "idle",
            "working_set_mib": rss / 1024**2,
            "private_commit_mib": private / 1024**2,
            "cpu_machine_percent": cpu / cpu_count,
            "cpu_one_core_percent": cpu,
            "threads": threads,
            "pids": [member.pid for member in members],
        })
    idle = [sample for sample in samples if sample["phase"] == "idle"]
    summary = {}
    for key in ("working_set_mib", "private_commit_mib", "cpu_machine_percent", "cpu_one_core_percent"):
        values = [sample[key] for sample in idle]
        summary[key] = {"mean": round(statistics.mean(values), 3),
                        "min": round(min(values), 3), "max": round(max(values), 3)}
    summary["working_set_percent_of_ram"] = round(summary["working_set_mib"]["mean"] * 1024**2 / total_ram * 100, 3)
    summary["warmup_working_set_peak_mib"] = round(max(sample["working_set_mib"] for sample in samples[:args.warmup]), 3) if args.warmup else None
    result = {"timestamp": datetime.now().astimezone().isoformat(), "pid": args.pid,
              "logical_cpus": cpu_count, "ram_gib": round(total_ram / 1024**3, 2),
              "warmup_samples": args.warmup, "idle_samples": args.seconds,
              "scope": "Target and descendants; working set sum may count shared pages twice; 1-second samples; no recording or transcription initiated by sampler",
              "summary": summary, "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
