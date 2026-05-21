"""
Baseline experiment: zero-shot LLM scheduling on exam instances.

Runs N_RUNS zero-shot proposals per model, records violations and penalty,
and saves results to results/baseline_results.json.
"""
import json
import os
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.dirname(__file__))

from parse_exam import parse_instance, print_summary
from verifier import check_hard_constraints, compute_penalty, format_violations
from agent import (
    call_llm, logged_call, parse_solution, solution_from_parsed,
    build_proposal_prompt,
)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data" / "purdue_exam"
RESULTS_DIR = BASE_DIR / "results"
LOGS_DIR = BASE_DIR / "logs"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def run_baseline_for_instance(instance_path: str, model: str, n_runs: int = 5) -> list:
    """
    Run n_runs zero-shot proposals on the given instance with the given model.
    Returns a list of result dicts.
    """
    print(f"\n{'='*60}")
    print(f"Loading instance: {instance_path}")
    instance = parse_instance(instance_path)
    print_summary(instance)

    # Build the proposal prompt
    prompt = build_proposal_prompt(
        instance,
        template_path=str(BASE_DIR / "prompts" / "propose_solution.txt"),
    )
    print(f"\nProposal prompt length: {len(prompt)} chars")

    results = []
    log_file = str(LOGS_DIR / f"baseline_{model}_{instance.name}.jsonl")
    print(f"\nRunning {n_runs} zero-shot proposals with model={model} ...")
    print(f"Logging to: {log_file}")

    for run_idx in range(n_runs):
        print(f"\n  Run {run_idx+1}/{n_runs} ...", end=" ", flush=True)
        result = {
            "run": run_idx,
            "instance": instance.name,
            "model": model,
            "parse_success": False,
            "num_violations": None,
            "violation_types": {},
            "penalty": None,
            "feasible": False,
            "assigned_exams": 0,
            "total_exams": len(instance.exams),
            "raw_response_chars": 0,
            "error": None,
        }

        try:
            response = logged_call(
                prompt=prompt,
                model=model,
                log_file=log_file,
                extra_meta={"run": run_idx, "phase": "baseline"},
                timeout=240,
            )
            result["raw_response_chars"] = len(response)

            # Parse JSON
            parsed = parse_solution(response)
            if not parsed or "assignments" not in parsed:
                result["error"] = "parse_failed: no 'assignments' key in response"
                print(f"PARSE FAIL (no assignments)")
            else:
                solution = solution_from_parsed(parsed)
                result["parse_success"] = True
                result["assigned_exams"] = len(solution)

                # Verify
                violations = check_hard_constraints(instance, solution)
                penalty = compute_penalty(instance, solution)

                result["num_violations"] = len(violations)
                result["feasible"] = len(violations) == 0
                result["penalty"] = penalty if len(violations) == 0 else None

                # Count violation types
                from collections import Counter
                vtype_counts = Counter(v["type"] for v in violations)
                result["violation_types"] = dict(vtype_counts)

                status = "FEASIBLE" if result["feasible"] else f"INFEASIBLE ({len(violations)} violations)"
                print(f"{status}, penalty={penalty:.1f}, assigned={len(solution)}/{len(instance.exams)}")

                # Print detailed violations for first run
                if run_idx == 0:
                    print(format_violations(violations, max_show=10))

        except Exception as e:
            result["error"] = str(e)[:300]
            print(f"ERROR: {e}")

        results.append(result)
        # Brief pause to avoid rate limiting
        if run_idx < n_runs - 1:
            time.sleep(2)

    return results


def print_summary_table(all_results: list):
    """Print a summary table of all results."""
    print(f"\n{'='*60}")
    print("BASELINE RESULTS SUMMARY")
    print(f"{'='*60}")

    # Group by (instance, model)
    from collections import defaultdict
    groups = defaultdict(list)
    for r in all_results:
        groups[(r["instance"], r["model"])].append(r)

    for (inst, model), runs in groups.items():
        n = len(runs)
        parsed = [r for r in runs if r["parse_success"]]
        feasible = [r for r in runs if r["feasible"]]
        violations_list = [r["num_violations"] for r in parsed if r["num_violations"] is not None]
        penalties = [r["penalty"] for r in feasible if r["penalty"] is not None]

        print(f"\nInstance: {inst} | Model: {model}")
        print(f"  Runs: {n}")
        print(f"  Parse success: {len(parsed)}/{n}")
        print(f"  Feasible: {len(feasible)}/{n} ({100*len(feasible)/n:.0f}%)")
        if violations_list:
            print(f"  Avg violations (parsed): {sum(violations_list)/len(violations_list):.1f} "
                  f"(min={min(violations_list)}, max={max(violations_list)})")
        if penalties:
            print(f"  Avg penalty (feasible): {sum(penalties)/len(penalties):.1f}")
        else:
            print(f"  Penalty: N/A (no feasible solutions)")

        # Violation type breakdown
        from collections import Counter
        all_vtypes = Counter()
        for r in parsed:
            for vtype, cnt in r.get("violation_types", {}).items():
                all_vtypes[vtype] += cnt
        if all_vtypes:
            print(f"  Violation types: {dict(all_vtypes.most_common())}")


def main():
    # Use synthetic_small instance (manageable for LLM)
    instance_path = str(DATA_DIR / "synthetic_small.xml")

    all_results = []

    # Run with claude-haiku (5 runs)
    try:
        results_haiku = run_baseline_for_instance(instance_path, "claude-haiku", n_runs=5)
        all_results.extend(results_haiku)
    except Exception as e:
        print(f"ERROR with claude-haiku: {e}")

    # Try codex/GPT-4o-mini (1 run as comparison)
    print("\n" + "="*60)
    print("Attempting GPT-4o-mini via codex exec ...")
    print("Checking codex availability ...")
    import subprocess
    help_result = subprocess.run(
        ["codex", "--help"], capture_output=True, text=True, timeout=15
    )
    print(f"codex --help returncode: {help_result.returncode}")
    if help_result.returncode == 0:
        # Try exec subcommand
        exec_result = subprocess.run(
            ["codex", "exec", "--help"], capture_output=True, text=True, timeout=15
        )
        print(f"codex exec --help returncode: {exec_result.returncode}")
        print(f"codex exec --help stdout[:300]: {exec_result.stdout[:300]}")
        if exec_result.returncode == 0:
            try:
                results_gpt = run_baseline_for_instance(instance_path, "gpt-4o-mini", n_runs=1)
                all_results.extend(results_gpt)
            except Exception as e:
                print(f"ERROR with gpt-4o-mini: {e}")
        else:
            print("codex exec not available; skipping GPT-4o-mini run")
            # Try -q syntax
            print("Trying: codex -q 'hello' ...")
            q_result = subprocess.run(
                ["codex", "-q", "Say exactly: hello"],
                capture_output=True, text=True, timeout=30
            )
            print(f"codex -q returncode: {q_result.returncode}")
            print(f"stdout: {q_result.stdout[:200]}")
            print(f"stderr: {q_result.stderr[:200]}")
    else:
        print("codex not available; skipping GPT-4o-mini run")

    # Save results
    output_path = str(RESULTS_DIR / "baseline_results.json")
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to: {output_path}")

    print_summary_table(all_results)

    return all_results


if __name__ == "__main__":
    main()
