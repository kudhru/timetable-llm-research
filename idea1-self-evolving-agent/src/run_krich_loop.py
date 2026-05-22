"""
Self-evolving loop experiments at k_rich (confound-controlled).

Runs the self-evolving loop at the true parametric failure threshold k_rich
for each model, using the rich prompt (identical to loop's t=0 format).

Usage:
    python3 src/run_krich_loop.py --model gpt-5.5 --feedback formal --n-exams 200
    python3 src/run_krich_loop.py --model claude-haiku --feedback nl --n-exams 16
    python3 src/run_krich_loop.py --all  # run all 4 combinations

Results saved to:
    logs/gpt55_formal_krich.jsonl
    logs/gpt55_nl_krich.jsonl
    logs/haiku_formal_krich.jsonl
    logs/haiku_nl_krich.jsonl
"""
import argparse
import json
import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from generate_instance import generate_instance
from loop import run_evolving_loop, compute_metrics

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"
RESULTS_DIR = BASE_DIR / "results"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Default k_rich values (from threshold search)
K_RICH = {
    "gpt-5.5": 200,     # borderline pass (2/3) with rich prompt at 41k chars
    "claude-haiku": 16,  # first failing point (0/3) with rich prompt
}

# Human-readable model name for log files
LOG_NAME = {
    "gpt-5.5": "gpt55",
    "claude-haiku": "haiku",
}

N_SEEDS = 3
T_ITERATIONS = 15


def run_one(model: str, feedback_mode: str, n_exams: int, seeds: list = None, t_iters: int = None):
    """
    Run the self-evolving loop for all seeds at given n_exams and collect results.

    Saves aggregated results to logs/<logname>_<feedback>_krich.jsonl and
    results/loop_<logname>_<feedback>_krich.json.
    """
    if seeds is None:
        seeds = list(range(N_SEEDS))
    if t_iters is None:
        t_iters = T_ITERATIONS

    log_name = LOG_NAME.get(model, model.replace("-", "_").replace(".", "_"))
    out_jsonl = LOGS_DIR / f"{log_name}_{feedback_mode}_krich.jsonl"
    out_results = RESULTS_DIR / f"loop_{log_name}_{feedback_mode}_krich.json"

    print(f"\n{'='*60}")
    print(f"KRICH LOOP: model={model}, feedback={feedback_mode}, n_exams={n_exams}")
    print(f"Seeds: {seeds}, T={t_iters}")
    print(f"Output: {out_jsonl}")
    print(f"{'='*60}")

    all_histories = []
    all_metrics = []

    for seed in seeds:
        print(f"\n--- Seed {seed} ---")
        instance = generate_instance(n_exams=n_exams, seed=seed)

        # Use a seed-specific log subdir to avoid collisions
        seed_log_dir = str(LOGS_DIR / f"krich_{log_name}_{feedback_mode}_seed{seed}")
        Path(seed_log_dir).mkdir(parents=True, exist_ok=True)

        history, best_solution = run_evolving_loop(
            instance=instance,
            model=model,
            feedback_mode=feedback_mode,
            T=t_iters,
            log_dir=seed_log_dir,
        )

        metrics = compute_metrics(history)
        metrics["seed"] = seed
        metrics["n_exams"] = n_exams
        metrics["model"] = model
        metrics["feedback_mode"] = feedback_mode
        metrics["k_rich"] = n_exams
        metrics["T"] = t_iters
        metrics["timestamp"] = datetime.now().isoformat()

        all_histories.append({"seed": seed, "history": history, "metrics": metrics})
        all_metrics.append(metrics)

        # Write one record per seed to JSONL
        with open(out_jsonl, "a") as f:
            f.write(json.dumps(metrics) + "\n")

        print(f"\nSeed {seed} done: first_feasible={metrics['first_feasible_iter']}, "
              f"feasibility_rate={metrics['feasibility_rate']:.1%}, "
              f"final_best_penalty={metrics['final_best_penalty']}")

    # Aggregate summary
    feasibility_rates = [m["feasibility_rate"] for m in all_metrics]
    first_feasibles = [m["first_feasible_iter"] for m in all_metrics if m["first_feasible_iter"] is not None]

    summary = {
        "model": model,
        "feedback_mode": feedback_mode,
        "n_exams": n_exams,
        "seeds": seeds,
        "T": t_iters,
        "mean_feasibility_rate": sum(feasibility_rates) / len(feasibility_rates) if feasibility_rates else 0,
        "mean_first_feasible_iter": sum(first_feasibles) / len(first_feasibles) if first_feasibles else None,
        "n_seeds_with_feasible": len(first_feasibles),
        "per_seed": all_metrics,
        "timestamp": datetime.now().isoformat(),
    }

    with open(out_results, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'='*60}")
    print(f"SUMMARY: model={model}, feedback={feedback_mode}, n_exams={n_exams}")
    print(f"  Mean feasibility rate: {summary['mean_feasibility_rate']:.1%}")
    print(f"  Mean first feasible iter: {summary['mean_first_feasible_iter']}")
    print(f"  Seeds with feasible: {summary['n_seeds_with_feasible']}/{len(seeds)}")
    print(f"  Results: {out_results}")
    print(f"{'='*60}")

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Run self-evolving loop at k_rich (confound-controlled)"
    )
    parser.add_argument(
        "--model",
        choices=["gpt-5.5", "claude-haiku"],
        help="Model to test (required unless --all)",
    )
    parser.add_argument(
        "--feedback",
        choices=["formal", "nl"],
        default="formal",
        help="Feedback mode: 'formal' (exact violation trace) or 'nl' (vague natural language)",
    )
    parser.add_argument(
        "--n-exams",
        type=int,
        default=None,
        help="Number of exams (k_rich). Default: model-specific k_rich",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=list(range(N_SEEDS)),
        help=f"Seeds to run (default: 0 1 2)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=T_ITERATIONS,
        help=f"Number of loop iterations T (default: {T_ITERATIONS})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all 4 combinations: (gpt-5.5, claude-haiku) x (formal, nl)",
    )

    args = parser.parse_args()

    t_iters = args.iterations

    if args.all:
        combos = [
            ("gpt-5.5", "formal", K_RICH["gpt-5.5"]),
            ("gpt-5.5", "nl", K_RICH["gpt-5.5"]),
            ("claude-haiku", "formal", K_RICH["claude-haiku"]),
            ("claude-haiku", "nl", K_RICH["claude-haiku"]),
        ]
        summaries = []
        for model, feedback, n_exams in combos:
            s = run_one(model, feedback, n_exams, seeds=args.seeds, t_iters=t_iters)
            summaries.append(s)

        # Print 2x2 comparison table
        print("\n\n" + "="*60)
        print("2x2 COMPARISON TABLE (model x feedback)")
        print("="*60)
        print(f"{'Model':<15} {'Feedback':<10} {'n_exams':<10} {'Feasib%':<10} {'1stFeas':<10}")
        print("-"*55)
        for s in summaries:
            first = s["mean_first_feasible_iter"]
            first_str = f"{first:.1f}" if first is not None else "never"
            print(f"{s['model']:<15} {s['feedback_mode']:<10} {s['n_exams']:<10} "
                  f"{s['mean_feasibility_rate']:.1%}    {first_str}")
    else:
        if not args.model:
            parser.error("--model is required unless --all is specified")
        n_exams = args.n_exams or K_RICH[args.model]
        run_one(args.model, args.feedback, n_exams, seeds=args.seeds, t_iters=t_iters)


if __name__ == "__main__":
    main()
