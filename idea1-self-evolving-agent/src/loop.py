"""
Self-evolving agent loop for exam timetabling.

Implements the core loop:
  1. Propose: LLM generates a solution using current strategy
  2. Verify: Formal verifier checks hard constraints, computes penalty
  3. Feedback: Generate feedback (formal or NL variant)
  4. Reflect: LLM reflects and updates strategy
  5. Archive: Log to JSONL file
"""
import json
import os
import sys
import time
import argparse
from pathlib import Path
from typing import Optional

# Add src dir to path
sys.path.insert(0, os.path.dirname(__file__))

from parse_exam import parse_instance, print_summary
from verifier import check_hard_constraints, compute_penalty, format_violations
from agent import (
    call_llm, logged_call, parse_solution, solution_from_parsed,
    build_proposal_prompt, build_formal_feedback_prompt, build_nl_feedback_prompt,
)

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"
PROMPTS_DIR = BASE_DIR / "prompts"

LOGS_DIR.mkdir(parents=True, exist_ok=True)


def run_evolving_loop(
    instance,
    model: str = "claude-haiku",
    feedback_mode: str = "formal",   # "formal" or "nl"
    T: int = 20,
    log_dir: Optional[str] = None,
    initial_strategy: Optional[list] = None,
) -> tuple:
    """
    Run the self-evolving agent loop for T iterations.

    Returns:
        (history: list of dicts, best_solution: dict)

    history contains one entry per iteration with:
      - iteration, model, feedback_mode
      - num_violations, penalty, best_penalty
      - violations (list)
      - strategy_update (str)
      - parse_success (bool)
      - assigned_exams (int)
    """
    if log_dir is None:
        log_dir = str(LOGS_DIR)

    strategy_memory = list(initial_strategy) if initial_strategy else []
    best_solution = None
    best_penalty = float('inf')
    history = []

    log_path = (
        Path(log_dir) / f"{model}_{feedback_mode}_{instance.name}.jsonl"
    )
    print(f"\nSelf-Evolving Loop: model={model}, feedback={feedback_mode}, T={T}")
    print(f"Instance: {instance.name} ({len(instance.exams)} exams)")
    print(f"Log: {log_path}")
    print("-" * 60)

    for t in range(T):
        # ── 1. Build proposal prompt with accumulated strategy ──────────────
        strategy_context = ""
        if strategy_memory:
            recent = strategy_memory[-5:]  # last 5 learnings
            strategy_context = "\n".join(
                f"  Iteration {i+1} learning: {s}"
                for i, s in enumerate(recent)
                if s and s.strip()
            )

        propose_log = str(log_path).replace(".jsonl", "_propose.jsonl")
        proposal_prompt = build_proposal_prompt(
            instance,
            template_path=str(PROMPTS_DIR / "propose_solution.txt"),
            strategy_context=strategy_context,
        )

        # ── 2. Propose ────────────────────────────────────────────────────────
        parse_success = False
        solution = {}
        violations = []
        penalty = float('inf')
        proposal_error = None

        try:
            response = logged_call(
                prompt=proposal_prompt,
                model=model,
                log_file=str(log_path).replace(".jsonl", "_propose.jsonl"),
                extra_meta={
                    "iteration": t,
                    "phase": "propose",
                    "feedback_mode": feedback_mode,
                },
                timeout=240,
            )
            parsed = parse_solution(response)
            if parsed and "assignments" in parsed:
                solution = solution_from_parsed(parsed)
                parse_success = True
            else:
                proposal_error = "parse_failed: no 'assignments' key"
        except Exception as e:
            proposal_error = str(e)[:200]
            print(f"[t={t}] PROPOSAL ERROR: {proposal_error}")

        # ── 3. Verify ─────────────────────────────────────────────────────────
        if parse_success and solution:
            violations = check_hard_constraints(instance, solution)
            penalty = compute_penalty(instance, solution)

            if penalty < best_penalty:
                best_penalty = penalty
                best_solution = solution

        # ── 4. Generate feedback ──────────────────────────────────────────────
        if feedback_mode == "formal":
            feedback_prompt = build_formal_feedback_prompt(
                instance=instance,
                solution=solution,
                violations=violations,
                penalty=penalty,
                best_penalty=best_penalty,
                template_path=str(PROMPTS_DIR / "reflect_formal.txt"),
            )
        else:  # "nl"
            feedback_prompt = build_nl_feedback_prompt(
                violations=violations,
                penalty=penalty,
                template_path=str(PROMPTS_DIR / "reflect_nl.txt"),
            )

        # ── 5. Reflect ───────────────────────────────────────────────────────
        reflection_data = {}
        strategy_update = ""
        reflect_error = None

        try:
            reflection_response = logged_call(
                prompt=feedback_prompt,
                model=model,
                log_file=str(log_path).replace(".jsonl", "_reflect.jsonl"),
                extra_meta={
                    "iteration": t,
                    "phase": "reflect",
                    "feedback_mode": feedback_mode,
                },
                timeout=240,
            )
            reflection_data = parse_solution(reflection_response)
            strategy_update = reflection_data.get("strategy_update", "")

            # If the reflection also includes a solution, use it
            if "assignments" in reflection_data:
                refl_solution = solution_from_parsed(reflection_data)
                if refl_solution:
                    refl_violations = check_hard_constraints(instance, refl_solution)
                    refl_penalty = compute_penalty(instance, refl_solution)
                    if refl_penalty < best_penalty:
                        best_penalty = refl_penalty
                        best_solution = refl_solution
                    # Use the reflected solution for logging
                    solution = refl_solution
                    violations = refl_violations
                    penalty = refl_penalty
                    parse_success = True
        except Exception as e:
            reflect_error = str(e)[:200]
            print(f"[t={t}] REFLECT ERROR: {reflect_error}")

        # Accumulate strategy
        if strategy_update and strategy_update.strip():
            strategy_memory.append(strategy_update.strip())

        # ── 6. Log iteration ─────────────────────────────────────────────────
        entry = {
            "iteration": t,
            "model": model,
            "feedback_mode": feedback_mode,
            "instance": instance.name,
            "parse_success": parse_success,
            "assigned_exams": len(solution),
            "total_exams": len(instance.exams),
            "num_violations": len(violations),
            "penalty": penalty if penalty != float('inf') else None,
            "best_penalty": best_penalty if best_penalty != float('inf') else None,
            "feasible": len(violations) == 0,
            "violations": violations[:20],  # cap for log size
            "strategy_update": strategy_update,
            "proposal_error": proposal_error,
            "reflect_error": reflect_error,
        }
        history.append(entry)

        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # ── Print progress ────────────────────────────────────────────────────
        status = "FEASIBLE" if entry["feasible"] else f"infeasible"
        pen_str = f"penalty={penalty:.1f}" if penalty != float('inf') else "penalty=N/A"
        best_str = f"best={best_penalty:.1f}" if best_penalty != float('inf') else "best=N/A"
        print(
            f"[t={t:2d}] violations={len(violations):3d} {pen_str} {best_str} "
            f"assigned={len(solution)}/{len(instance.exams)} [{status}]"
        )

        # Brief rate-limiting pause
        if t < T - 1:
            time.sleep(1)

    return history, best_solution


def extract_strategy_learnings(history: list) -> list:
    """Extract strategy_update strings from a history list."""
    return [
        h["strategy_update"] for h in history
        if h.get("strategy_update", "").strip()
    ]


def compute_metrics(history: list) -> dict:
    """Compute summary metrics from a loop history."""
    if not history:
        return {}

    # First feasible iteration
    first_feasible = next(
        (h["iteration"] for h in history if h.get("feasible", False)), None
    )

    # Violation trajectory
    violation_traj = [h.get("num_violations", 0) for h in history]

    # Penalty trajectory (use large sentinel for infeasible)
    penalty_traj = [
        h["penalty"] if h.get("penalty") is not None else float('inf')
        for h in history
    ]

    feasible_runs = [h for h in history if h.get("feasible", False)]
    feasibility_rate = len(feasible_runs) / len(history)

    final_best = min(
        (h["best_penalty"] for h in history if h.get("best_penalty") is not None),
        default=None,
    )

    return {
        "first_feasible_iter": first_feasible,
        "final_best_penalty": final_best,
        "violation_trajectory": violation_traj,
        "penalty_trajectory": [p if p != float('inf') else None for p in penalty_traj],
        "feasibility_rate": feasibility_rate,
        "total_iterations": len(history),
        "feasible_count": len(feasible_runs),
    }


def main():
    parser = argparse.ArgumentParser(description="Self-evolving exam scheduling loop")
    parser.add_argument("--instance", required=True, help="Path to XML instance")
    parser.add_argument("--model", default="claude-haiku",
                        choices=["claude-haiku", "claude-sonnet", "codex-default", "gpt-5.5"])
    parser.add_argument("--feedback", default="formal", choices=["formal", "nl"])
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--log-dir", default=None)
    args = parser.parse_args()

    instance = parse_instance(args.instance)
    print_summary(instance)

    history, best_solution = run_evolving_loop(
        instance=instance,
        model=args.model,
        feedback_mode=args.feedback,
        T=args.iterations,
        log_dir=args.log_dir,
    )

    metrics = compute_metrics(history)

    print(f"\n{'='*60}")
    print("LOOP RESULTS")
    print(f"{'='*60}")
    print(f"First feasible iteration: {metrics['first_feasible_iter']}")
    print(f"Final best penalty: {metrics['final_best_penalty']}")
    print(f"Feasibility rate: {metrics['feasibility_rate']:.1%}")
    print(f"Violation trajectory: {metrics['violation_trajectory']}")

    results_path = BASE_DIR / "results" / f"loop_{args.model}_{args.feedback}_{instance.name}.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w") as f:
        json.dump({"metrics": metrics, "history": history}, f, indent=2)
    print(f"\nResults saved to: {results_path}")

    return history, best_solution, metrics


if __name__ == "__main__":
    main()
