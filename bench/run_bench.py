"""Benchmark suite for measuring matching engine throughput and latency percentiles."""

from __future__ import annotations
import argparse
import cProfile
import io
import pstats
import platform
import sys
import time
from pathlib import Path

# Ensure 'src' and project root are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from typing import List, Tuple
from lob.engine import MatchingEngine
from lob.models import OrderType
from bench.workload import generate_workload


def run_benchmark(
    workload: List[Tuple],
    record_latencies: bool = True,
) -> Tuple[float, float, dict]:
    """Execute pre-generated workload on MatchingEngine and measure latency."""
    engine = MatchingEngine()
    latencies_ns: List[int] = [] if record_latencies else []

    total_ops = len(workload)
    perf_ns = time.perf_counter_ns

    start_total_ns = perf_ns()

    if record_latencies:
        for item in workload:
            op_type = item[0]
            t0 = perf_ns()

            if op_type == "LIMIT":
                engine.process_order(item[1], item[2], OrderType.LIMIT, item[3], item[4])
            elif op_type == "CANCEL":
                engine.cancel_order(item[1])
            elif op_type == "MARKET":
                engine.process_order(item[1], item[2], OrderType.MARKET, item[3])

            t1 = perf_ns()
            latencies_ns.append(t1 - t0)
    else:
        for item in workload:
            op_type = item[0]
            if op_type == "LIMIT":
                engine.process_order(item[1], item[2], OrderType.LIMIT, item[3], item[4])
            elif op_type == "CANCEL":
                engine.cancel_order(item[1])
            elif op_type == "MARKET":
                engine.process_order(item[1], item[2], OrderType.MARKET, item[3])

    end_total_ns = perf_ns()
    total_time_sec = (end_total_ns - start_total_ns) / 1e9
    throughput = total_ops / total_time_sec

    percentiles = {}
    if record_latencies and latencies_ns:
        latencies_ns.sort()
        n = len(latencies_ns)
        percentiles = {
            "p50_us": latencies_ns[int(n * 0.50)] / 1000.0,
            "p95_us": latencies_ns[int(n * 0.95)] / 1000.0,
            "p99_us": latencies_ns[int(n * 0.99)] / 1000.0,
            "p999_us": latencies_ns[int(n * 0.999)] / 1000.0,
            "min_us": latencies_ns[0] / 1000.0,
            "max_us": latencies_ns[-1] / 1000.0,
            "mean_us": (sum(latencies_ns) / n) / 1000.0,
        }

    return total_time_sec, throughput, percentiles


def profile_engine(workload: List[Tuple], top_n: int = 15) -> str:
    """Run cProfile on the workload to identify top CPU bottlenecks."""
    pr = cProfile.Profile()
    pr.enable()

    engine = MatchingEngine()
    for item in workload:
        op_type = item[0]
        if op_type == "LIMIT":
            engine.process_order(item[1], item[2], OrderType.LIMIT, item[3], item[4])
        elif op_type == "CANCEL":
            engine.cancel_order(item[1])
        elif op_type == "MARKET":
            engine.process_order(item[1], item[2], OrderType.MARKET, item[3])

    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(top_n)
    return s.getvalue()


def main():
    parser = argparse.ArgumentParser(description="LOB Engine Benchmark")
    parser.add_argument("--ops", type=int, default=1_000_000, help="Number of operations")
    parser.add_argument("--seed", type=int, default=42, help="Workload PRNG seed")
    parser.add_argument("--profile", action="store_true", help="Run cProfile profiling")
    args = parser.parse_args()

    print(f"System: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Python: {platform.python_version()} ({platform.python_implementation()})")
    print(f"Generating deterministic workload ({args.ops:,} ops, seed={args.seed})...")
    workload = generate_workload(n_ops=args.ops, seed=args.seed)
    print(f"Workload ready. Running benchmark...")

    total_time, throughput, percentiles = run_benchmark(workload, record_latencies=True)

    print("\n" + "=" * 50)
    print("BENCHMARK RESULTS")
    print("=" * 50)
    print(f"Total Operations : {args.ops:,}")
    print(f"Elapsed Time     : {total_time:.4f} s")
    print(f"Throughput       : {throughput:,.0f} ops/sec")
    print("-" * 50)
    print(f"p50 Latency      : {percentiles['p50_us']:.2f} \u00b5s")
    print(f"p95 Latency      : {percentiles['p95_us']:.2f} \u00b5s")
    print(f"p99 Latency      : {percentiles['p99_us']:.2f} \u00b5s")
    print(f"p99.9 Latency    : {percentiles['p999_us']:.2f} \u00b5s")
    print(f"Mean Latency     : {percentiles['mean_us']:.2f} \u00b5s")
    print(f"Max Latency      : {percentiles['max_us']:.2f} \u00b5s")
    print("=" * 50)

    if args.profile:
        print("\nTOP BOTTLENECK PROFILE (cProfile cumulative time):")
        print(profile_engine(workload[:200_000], top_n=20))


if __name__ == "__main__":
    main()
