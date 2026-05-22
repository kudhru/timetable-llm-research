"""
Parametric difficulty threshold search for exam timetabling.

Uses exponential probe + binary search (following the kudhru/parametric-llm-benchmarks
methodology) to find exactly where a model fails at zero-shot scheduling as n_exams grows.

Usage:
    python src/search_threshold.py --model gpt-5.5
    python src/search_threshold.py --model claude-haiku
    python src/search_threshold.py --model claude-haiku --probe-down  # probe n=10,5 too
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from generate_instance import generate_instance
from parse_exam import ExamInstance
from verifier import check_hard_constraints
from agent import (
    call_llm, logged_call, parse_solution, solution_from_parsed,
    build_proposal_prompt,
)

BASE_DIR = Path(__file__).parent.parent
RESULTS_DIR = BASE_DIR / "results"
LOGS_DIR = BASE_DIR / "logs"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants ─────────────────────────────────────────────────────────────────

N_PROBES = 3
PASS_THRESHOLD = 2 / 3  # pass if >=2 of 3 probes feasible
PROMPT_CHAR_LIMIT = 80_000  # ~20k tokens; use summarized format above this


# ── Prompt building (with size guard) ─────────────────────────────────────────

def build_prompt_for_instance(instance: ExamInstance) -> str:
    """
    Build a proposal prompt for the instance.

    For large instances (>PROMPT_CHAR_LIMIT chars), uses a compact format that
    lists only exam IDs, student counts, and period/room counts — not full lists.
    """
    prompt = build_proposal_prompt(instance)

    if len(prompt) <= PROMPT_CHAR_LIMIT:
        return prompt

    # Compact format for large instances
    return _build_compact_prompt(instance)


def _build_compact_prompt(instance: ExamInstance) -> str:
    """Compact prompt for large instances — omits full availability lists."""
    exam_lines = []
    for exam in instance.exams:
        exam_lines.append(
            f'  {{"exam_id": "{exam.id}", "students": {exam.student_count}, '
            f'"n_available_periods": {len(exam.available_periods)}, '
            f'"available_periods_sample": {exam.available_periods[:5]}, '
            f'"n_available_rooms": {len(exam.available_rooms)}, '
            f'"available_rooms_sample": {exam.available_rooms[:5]}}}'
        )
    exam_list_str = "[\n" + ",\n".join(exam_lines) + "\n]"

    # Compact period list (first 10 only)
    period_lines = []
    for p in instance.periods[:10]:
        period_lines.append(
            f'  {{"id": "{p.id}", "day": "{p.day}", "time": "{p.time}", "penalty": {p.penalty}}}'
        )
    if len(instance.periods) > 10:
        period_lines.append(f'  ... ({len(instance.periods)} total periods, IDs 1–{len(instance.periods)})')
    period_list_str = "[\n" + ",\n".join(period_lines) + "\n]"

    # Compact room list (first 10 only)
    room_lines = []
    for r in instance.rooms[:10]:
        room_lines.append(
            f'  {{"id": "{r.id}", "capacity": {r.size}}}'
        )
    if len(instance.rooms) > 10:
        room_lines.append(f'  ... ({len(instance.rooms)} total rooms, IDs 1–{len(instance.rooms)})')
    room_list_str = "[\n" + ",\n".join(room_lines) + "\n]"

    # Hard constraints
    hard_constraints = [c for c in instance.constraints if c.hard]
    if hard_constraints:
        hc_str = "\n".join(
            f"  - {c.type.upper()} (id={c.id}): exams {c.exam_ids}"
            for c in hard_constraints[:20]
        )
        if len(hard_constraints) > 20:
            hc_str += f"\n  ... ({len(hard_constraints)} total)"
    else:
        hc_str = "  (none)"

    room_caps = sorted(r.size for r in instance.rooms)

    return f"""You are a university exam scheduling assistant. Schedule {len(instance.exams)} exams.

## Instance: {instance.name}
- Exams: {len(instance.exams)}
- Periods: {len(instance.periods)} (IDs: 1 to {len(instance.periods)})
- Rooms: {len(instance.rooms)} (IDs: 1 to {len(instance.rooms)}, capacities: {room_caps[0]}–{room_caps[-1]})
- Students: {len(instance.students)}

## Sample Periods (full range: 1–{len(instance.periods)})
{period_list_str}

## Sample Rooms (full range: 1–{len(instance.rooms)})
{room_list_str}

## Hard Constraints (MUST satisfy ALL)
1. STUDENT CONFLICT: No two exams sharing >= 1 student in the same period.
2. ROOM CAPACITY: Total assigned room capacity >= exam student count.
3. PERIOD AVAILABILITY: Each exam must use one of its listed available periods.
4. ROOM AVAILABILITY: Each exam must use rooms from its listed available rooms.
5. Distribution constraints:
{hc_str}

## Exams to Schedule
For each exam: exam_id, student count, sample available periods (use any period in 1–{len(instance.periods)} that is listed in available_periods_sample or try adjacent IDs), sample available rooms (similarly).
IMPORTANT: All exams have access to ALL {len(instance.periods)} periods and ALL {len(instance.rooms)} rooms — the sample shows only the first 5.
{exam_list_str}

## Output Format
Return ONLY a JSON object (no other text):
{{
  "assignments": [
    {{"exam_id": "E1", "period_id": "1", "room_ids": ["1"]}},
    ...
  ]
}}

Rules:
- Every exam must have exactly one assignment.
- Use period IDs between 1 and {len(instance.periods)}.
- Use room IDs between 1 and {len(instance.rooms)}.
- Never assign two conflicting exams (shared students) to the same period.
- Assign rooms with total capacity >= student count.
- Output ONLY the JSON, nothing else.
"""


# ── Single probe ──────────────────────────────────────────────────────────────

def probe_single(n_exams: int, model: str, seed: int, results_file: str) -> bool:
    """
    Run a single probe: generate instance, call LLM, verify solution.

    Returns True if the solution is feasible (0 hard constraint violations).
    Appends a JSONL result line to results_file.
    """
    log_file = str(LOGS_DIR / f"threshold_{model.replace('-', '_')}_n{n_exams}_s{seed}.jsonl")

    record = {
        "model": model,
        "n_exams": n_exams,
        "seed": seed,
        "violations": None,
        "feasible": False,
        "pass_rate": None,
        "timestamp": datetime.now().isoformat(),
    }

    try:
        # Generate instance
        instance = generate_instance(n_exams=n_exams, seed=seed)

        # Build prompt
        prompt = build_prompt_for_instance(instance)
        prompt_chars = len(prompt)

        print(f"    [n={n_exams}, seed={seed}] prompt={prompt_chars} chars", end=" ", flush=True)

        # Call LLM
        timeout = 300 if model == "gpt-5.5" else 180
        response = logged_call(
            prompt=prompt,
            model=model,
            log_file=log_file,
            extra_meta={
                "n_exams": n_exams,
                "seed": seed,
                "phase": "threshold_probe",
            },
            timeout=timeout,
        )

        # Parse solution
        parsed = parse_solution(response)
        if not parsed or "assignments" not in parsed:
            record["violations"] = -1  # parse failure
            record["feasible"] = False
            print(f"-> PARSE_FAIL")
        else:
            solution = solution_from_parsed(parsed)
            violations = check_hard_constraints(instance, solution)
            record["violations"] = len(violations)
            record["feasible"] = len(violations) == 0
            status = "PASS" if record["feasible"] else f"FAIL ({len(violations)} violations)"
            print(f"-> {status}")

    except Exception as e:
        record["error"] = str(e)[:300]
        print(f"-> ERROR: {str(e)[:100]}")

    # Append to results file
    with open(results_file, "a") as f:
        f.write(json.dumps(record) + "\n")

    return record["feasible"]


# ── Multi-seed probe ──────────────────────────────────────────────────────────

def probe(n_exams: int, model: str, results_file: str, n_probes: int = N_PROBES) -> float:
    """
    Run N_PROBES instances with different seeds, return pass rate.

    Each probe: generate instance, run zero-shot LLM scheduling, check feasibility.
    """
    print(f"\n  Probing n_exams={n_exams} with {n_probes} seeds ...")
    passes = 0
    for seed in range(n_probes):
        ok = probe_single(n_exams=n_exams, model=model, seed=seed, results_file=results_file)
        if ok:
            passes += 1
        # Brief pause between calls
        if seed < n_probes - 1:
            time.sleep(2)

    rate = passes / n_probes
    print(f"  => n_exams={n_exams}: {passes}/{n_probes} pass, rate={rate:.2f}")

    # Update pass_rate in the JSONL records
    _update_pass_rate(results_file, n_exams, rate)

    return rate


def _update_pass_rate(results_file: str, n_exams: int, rate: float):
    """Update pass_rate field for all records with this n_exams."""
    try:
        with open(results_file) as f:
            lines = f.readlines()
        updated = []
        for line in lines:
            try:
                rec = json.loads(line)
                if rec.get("n_exams") == n_exams:
                    rec["pass_rate"] = rate
                updated.append(json.dumps(rec) + "\n")
            except json.JSONDecodeError:
                updated.append(line)
        with open(results_file, "w") as f:
            f.writelines(updated)
    except Exception:
        pass  # Non-critical


# ── Exponential probe ─────────────────────────────────────────────────────────

def exponential_probe(model: str, results_file: str, start_k: int = 20) -> int:
    """
    Double n_exams until the model fails (pass_rate < PASS_THRESHOLD).

    Returns the first n_exams where the model fails.
    """
    print(f"\n{'='*60}")
    print(f"EXPONENTIAL PROBE: model={model}")
    print(f"{'='*60}")

    k = start_k
    last_pass_k = None

    while k <= 1280:
        rate = probe(k, model, results_file)
        if rate >= PASS_THRESHOLD:
            last_pass_k = k
            print(f"  PASSES at n_exams={k} (rate={rate:.2f})")
            k *= 2
        else:
            print(f"  FAILS at n_exams={k} (rate={rate:.2f})")
            return k

    # Safety cap reached — model passes everything up to 1280
    print(f"  Model passes all tested sizes up to n_exams=1280")
    return k


# ── Binary search ─────────────────────────────────────────────────────────────

def binary_search(lo: int, hi: int, model: str, results_file: str) -> int:
    """
    Find exact threshold between lo (passes) and hi (fails).

    Returns the smallest n_exams where the model fails (hi).
    """
    print(f"\n{'='*60}")
    print(f"BINARY SEARCH: model={model}, lo={lo}, hi={hi}")
    print(f"{'='*60}")

    while hi - lo > 5:
        mid = (lo + hi) // 2
        rate = probe(mid, model, results_file)
        if rate >= PASS_THRESHOLD:
            print(f"  PASSES at n_exams={mid} (rate={rate:.2f}) -> lo={mid}")
            lo = mid
        else:
            print(f"  FAILS at n_exams={mid} (rate={rate:.2f}) -> hi={mid}")
            hi = mid

    print(f"\nThreshold found: n_exams={hi} (smallest failing k)")
    return hi


# ── Downward probe (for models that fail at the start) ────────────────────────

def probe_downward(model: str, results_file: str, start_k: int = 20) -> int:
    """
    Probe smaller n_exams values to find where a model first passes.

    Returns the largest n_exams where the model passes (or 0 if never).
    """
    print(f"\n{'='*60}")
    print(f"DOWNWARD PROBE: model={model}, starting from n_exams={start_k}")
    print(f"{'='*60}")

    candidates = []
    k = start_k
    while k >= 5:
        candidates.append(k)
        k = k // 2

    last_pass = 0
    for k in sorted(candidates):
        rate = probe(k, model, results_file)
        if rate >= PASS_THRESHOLD:
            last_pass = k
            print(f"  PASSES at n_exams={k}")
        else:
            print(f"  FAILS at n_exams={k}")

    return last_pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parametric difficulty threshold search for exam timetabling"
    )
    parser.add_argument(
        "--model",
        required=True,
        choices=["gpt-5.5", "claude-haiku"],
        help="Model to test",
    )
    parser.add_argument(
        "--start-k",
        type=int,
        default=20,
        help="Starting n_exams for exponential probe (default: 20)",
    )
    parser.add_argument(
        "--probe-down",
        action="store_true",
        help="Also probe downward (for models failing at start_k)",
    )
    parser.add_argument(
        "--n-probes",
        type=int,
        default=N_PROBES,
        help=f"Number of seeds per probe (default: {N_PROBES})",
    )
    parser.add_argument(
        "--skip-binary",
        action="store_true",
        help="Skip binary search, just do exponential probe",
    )
    args = parser.parse_args()

    model = args.model
    results_file = str(RESULTS_DIR / f"threshold_search_{model.replace('-', '_')}.jsonl")

    print(f"\nParametric Threshold Search")
    print(f"  Model:       {model}")
    print(f"  Start k:     {args.start_k}")
    print(f"  N probes:    {args.n_probes}")
    print(f"  Results:     {results_file}")
    print(f"  Pass thresh: {PASS_THRESHOLD:.2f} ({int(PASS_THRESHOLD * args.n_probes)}/{args.n_probes} probes)")

    # ── Step 1: Check pass at start_k ─────────────────────────────────────────
    rate_start = probe(args.start_k, model, results_file, n_probes=args.n_probes)

    if rate_start < PASS_THRESHOLD:
        # Model already fails at start_k — probe downward
        print(f"\nModel already fails at n_exams={args.start_k}!")
        if args.probe_down:
            last_pass = probe_downward(model, results_file, start_k=args.start_k // 2)
            if last_pass > 0 and not args.skip_binary:
                # Binary search between last_pass and start_k
                threshold = binary_search(last_pass, args.start_k, model, results_file)
            else:
                threshold = args.start_k
        else:
            # Just probe n=10 and n=5 to see if it ever passes
            for k in [10, 5]:
                probe(k, model, results_file, n_probes=args.n_probes)
            threshold = args.start_k

        print(f"\n{'='*60}")
        print(f"RESULT: {model} threshold <= {threshold} exams")
        print(f"{'='*60}")
        return threshold

    # ── Step 2: Exponential probe upward ─────────────────────────────────────
    failing_k = exponential_probe(model, results_file, start_k=args.start_k * 2)

    # The previous k (passing) is start_k or start_k * 2^(step-1)
    # We know start_k passes; the exponential probe doubles from there
    # Reconstruct the last passing k
    # Re-read results to find it
    last_pass_k = args.start_k
    try:
        with open(results_file) as f:
            seen = {}
            for line in f:
                try:
                    rec = json.loads(line)
                    n = rec.get("n_exams")
                    rate = rec.get("pass_rate")
                    if n is not None and rate is not None:
                        seen[n] = rate
                except json.JSONDecodeError:
                    pass
        for n in sorted(seen.keys()):
            if seen[n] >= PASS_THRESHOLD and n < failing_k:
                last_pass_k = n
    except Exception:
        pass

    if args.skip_binary:
        threshold = failing_k
    else:
        # ── Step 3: Binary search ─────────────────────────────────────────────
        threshold = binary_search(last_pass_k, failing_k, model, results_file)

    print(f"\n{'='*60}")
    print(f"RESULT: {model} threshold = {threshold} exams")
    print(f"  (passes at n <= {last_pass_k}, fails at n >= {threshold})")
    print(f"{'='*60}")

    # Write summary
    summary = {
        "model": model,
        "threshold_n_exams": threshold,
        "last_passing_n_exams": last_pass_k,
        "timestamp": datetime.now().isoformat(),
    }
    summary_path = RESULTS_DIR / f"threshold_summary_{model.replace('-', '_')}.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Summary saved to: {summary_path}")

    return threshold


if __name__ == "__main__":
    main()
