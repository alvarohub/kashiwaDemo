#!/usr/bin/env python3
"""Benchmark Ollama models on this machine and append results to BENCHMARKS.md.

Usage (on the Pi):
    python3 benchmark.py                      # benchmark the configured model
    python3 benchmark.py llama3.2:1b phi3.5:latest   # specific model(s)
    python3 benchmark.py --all                # every installed model

Each model gets a warm-up run (loads it into RAM), then N timed runs of a
fixed prompt. Records: TTFT, generation tok/s, total time, temperature.
Results are appended as a row to BENCHMARKS.md.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

import config

PROMPT = "Explain what a differentiable manifold is in two short sentences."
NUM_PREDICT = 100
RUNS = 3  # timed runs per model (after 1 warm-up)
API = config.OLLAMA_API_BASE.rstrip("/")
BENCH_FILE = Path(__file__).parent / "BENCHMARKS.md"


def read_temp() -> float | None:
    try:
        return float(Path(config.TEMPERATURE_PATH).read_text().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def installed_models() -> list[str]:
    data = requests.get(f"{API}/tags", timeout=10).json()
    return [m.get("name") or m.get("model") for m in data.get("models", [])]


def bench_once(model: str) -> dict:
    payload = {
        "model": model,
        "prompt": PROMPT,
        "stream": False,
        "options": {"num_predict": NUM_PREDICT},
    }
    t0 = time.perf_counter()
    r = requests.post(f"{API}/generate", json=payload, timeout=300)
    r.raise_for_status()
    wall = time.perf_counter() - t0
    d = r.json()

    eval_count = d.get("eval_count", 0)
    eval_s = d.get("eval_duration", 0) / 1e9
    prompt_eval_s = d.get("prompt_eval_duration", 0) / 1e9
    load_s = d.get("load_duration", 0) / 1e9
    ttft = load_s + prompt_eval_s  # time until first token ≈ load + prompt eval
    return {
        "tok_per_s": eval_count / eval_s if eval_s > 0 else 0,
        "ttft_s": ttft,
        "load_s": load_s,
        "wall_s": wall,
        "tokens": eval_count,
    }


def fmt_row(model: str, results: list[dict], temp: float | None) -> str:
    best = max(results, key=lambda r: r["tok_per_s"])
    avg_tps = sum(r["tok_per_s"] for r in results) / len(results)
    warm_ttft = min(r["ttft_s"] for r in results)  # best warm ttft
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    temp_s = f"{temp:.0f}°C" if temp is not None else "--"
    return (
        f"| {now} | {model} | {avg_tps:.2f} | {best['tok_per_s']:.2f} "
        f"| {warm_ttft:.2f} s | {best['wall_s']:.1f} s | {NUM_PREDICT} | {temp_s} |"
    )


TABLE_HEADER = (
    "| Date | Model | avg tok/s | best tok/s | warm TTFT | wall (100 tok) "
    "| max tokens | temp |\n"
    "|---|---|---|---|---|---|---|---|"
)


def append_row(row: str) -> None:
    if BENCH_FILE.exists():
        text = BENCH_FILE.read_text()
    else:
        text = (
            "# Model Benchmarks\n\n"
            f"Machine: {platform.node()} (Raspberry Pi, CPU-only).\n"
            f'Prompt: "{PROMPT}" — num_predict={NUM_PREDICT}, {RUNS} timed runs '
            "after 1 warm-up.\n\n"
            + TABLE_HEADER
            + "\n"
        )
    if TABLE_HEADER.split("\n")[0] not in text:
        text += "\n" + TABLE_HEADER + "\n"
    if not text.endswith("\n"):
        text += "\n"
    text += row + "\n"
    BENCH_FILE.write_text(text)


def bench_model(model: str) -> None:
    print(f"\n=== {model} ===")
    print("  warm-up (loads model, slow)...", end=" ", flush=True)
    w = bench_once(model)
    print(f"load {w['load_s']:.1f}s")
    results = []
    for i in range(RUNS):
        r = bench_once(model)
        results.append(r)
        print(f"  run {i+1}: {r['tok_per_s']:.2f} tok/s, ttft {r['ttft_s']:.2f}s, wall {r['wall_s']:.1f}s")
    temp = read_temp()
    row = fmt_row(model, results, temp)
    append_row(row)
    print(f"  → appended to {BENCH_FILE.name}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--all" in sys.argv:
        models = installed_models()
        print(f"Installed models: {', '.join(models)}")
    elif args:
        models = args
    else:
        models = [config.MODEL_NAME]

    for m in models:
        try:
            bench_model(m)
        except requests.RequestException as e:
            print(f"  ERROR for {m}: {e}")
    print("\nDone. See BENCHMARKS.md")


if __name__ == "__main__":
    main()
