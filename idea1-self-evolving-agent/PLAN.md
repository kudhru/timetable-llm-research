# Idea 1: Self-Evolving Agent with Formal Constraint Feedback

## Research Question
Does formal, verifiable constraint feedback produce faster and more reliable agent self-improvement than natural language feedback in constraint satisfaction domains?

## Core Hypothesis
Existing self-evolving agent frameworks (Gödel Agent, STELLA, Multi-Agent Evolve) use soft or human-in-the-loop feedback. University timetabling provides a formal, automatic oracle: hard constraint violation is binary and exact; soft constraint penalty is a real-valued score. We hypothesize that this formal feedback signal enables more targeted self-improvement than equivalent natural language descriptions of failure.

## Target Venue
EMNLP / ACL — Agents + Reasoning track

## Novel Contribution vs. Prior Work
- **Gödel Agent / STELLA / MAE**: Self-evolution on open-ended tasks with NL or human feedback
- **AlphaEvolve (DeepMind 2025)**: Evolves algorithm code via LLM, not reasoning strategy
- **This work**: Self-evolution in a formal CSP domain where (a) feedback is verifiable and exact, (b) improvement is unambiguously measurable, (c) generalization across instances is testable

---

## Step-by-Step Execution Plan

### Phase 0: Setup (Week 1)

#### 0.1 Download Datasets
```bash
# Purdue Examination Timetabling (9 instances, XML format)
# Go to: https://www.unitime.org/exam_datasets.php
# Download the zip file containing all 9 instances

mkdir -p data/purdue_exam
# Place downloaded XML files in data/purdue_exam/
# Files will be named like: exam-fall08.xml, exam-spring09.xml, etc.
```

#### 0.2 Parse and Validate Dataset
```bash
mkdir -p src/
```

Create `src/parse_exam.py`:
```python
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class Period:
    id: str
    length: int       # minutes
    date: str
    time: str
    penalty: int      # penalty for assigning an exam here

@dataclass
class Room:
    id: str
    size: int
    alt: int          # alternate capacity
    x: int
    y: int

@dataclass
class Exam:
    id: str
    length: int
    alt_seating: bool
    max_rooms: int
    available_periods: List[str]
    available_rooms: List[str]
    student_count: int = 0

@dataclass
class Student:
    id: str
    exam_ids: List[str]
    unavailable_periods: List[str] = field(default_factory=list)

@dataclass
class DistributionConstraint:
    id: str
    type: str         # same-room, diff-period, same-period, etc.
    exam_ids: List[str]
    hard: bool
    weight: int = 0

@dataclass
class ExamInstance:
    name: str
    periods: List[Period]
    rooms: List[Room]
    exams: List[Exam]
    students: List[Student]
    constraints: List[DistributionConstraint]

def parse_instance(xml_path: str) -> ExamInstance:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    # implement parsing following unitime.org/exam_dataformat.php
    # return ExamInstance(...)
    pass
```

#### 0.3 Build Constraint Verifier
Create `src/verifier.py`:
```python
from typing import Dict, List, Tuple

# A solution maps exam_id -> (period_id, [room_ids])
Solution = Dict[str, Tuple[str, List[str]]]

def check_hard_constraints(instance, solution: Solution) -> List[dict]:
    """
    Returns list of violations. Each violation is a dict with:
      - type: str (e.g., 'student_conflict', 'room_capacity', 'unavailable_period')
      - exam_ids: list of involved exam ids
      - description: human-readable string
    """
    violations = []

    # 1. Student conflict: two exams with shared students in same period
    period_to_exams = {}
    for exam_id, (period_id, rooms) in solution.items():
        period_to_exams.setdefault(period_id, []).append(exam_id)

    for period_id, exam_ids in period_to_exams.items():
        for i in range(len(exam_ids)):
            for j in range(i + 1, len(exam_ids)):
                shared = get_shared_students(instance, exam_ids[i], exam_ids[j])
                if shared > 0:
                    violations.append({
                        "type": "student_conflict",
                        "exam_ids": [exam_ids[i], exam_ids[j]],
                        "period_id": period_id,
                        "shared_students": shared,
                        "description": f"Exams {exam_ids[i]} and {exam_ids[j]} share {shared} students but are in same period {period_id}"
                    })

    # 2. Room capacity exceeded
    for exam_id, (period_id, room_ids) in solution.items():
        total_capacity = sum(get_room_capacity(instance, r) for r in room_ids)
        exam_size = get_exam_size(instance, exam_id)
        if exam_size > total_capacity:
            violations.append({
                "type": "room_capacity",
                "exam_ids": [exam_id],
                "description": f"Exam {exam_id} has {exam_size} students but assigned rooms hold {total_capacity}"
            })

    # 3. Period/room availability
    for exam_id, (period_id, room_ids) in solution.items():
        exam = get_exam(instance, exam_id)
        if period_id not in exam.available_periods:
            violations.append({
                "type": "unavailable_period",
                "exam_ids": [exam_id],
                "description": f"Exam {exam_id} assigned to unavailable period {period_id}"
            })

    # 4. Distribution constraints
    for constraint in instance.constraints:
        if constraint.hard:
            violations.extend(check_distribution(instance, solution, constraint))

    return violations


def compute_penalty(instance, solution: Solution) -> float:
    """Compute total soft constraint penalty score. Lower is better."""
    penalty = 0.0

    # Period penalties
    for exam_id, (period_id, _) in solution.items():
        period = get_period(instance, period_id)
        penalty += period.penalty

    # Soft distribution constraints
    for constraint in instance.constraints:
        if not constraint.hard:
            penalty += compute_distribution_penalty(instance, solution, constraint)

    return penalty
```

#### 0.4 Directory Structure
```
idea1-self-evolving-agent/
├── PLAN.md                    (this file)
├── data/
│   └── purdue_exam/           (download XML files here)
├── src/
│   ├── parse_exam.py
│   ├── verifier.py
│   ├── agent.py               (Phase 2)
│   ├── loop.py                (Phase 3)
│   └── evaluate.py            (Phase 4)
├── prompts/
│   ├── propose_solution.txt
│   ├── reflect_formal.txt
│   └── reflect_nl.txt
├── logs/                      (JSONL logs of all LLM calls)
└── results/                   (metrics, plots)
```

---

### Phase 1: Baseline Agent — Zero-Shot Scheduling (Week 1–2)

#### 1.1 Design the Proposal Prompt

Create `prompts/propose_solution.txt`:
```
You are a university exam scheduling assistant.

## Instance Summary
- Exams: {num_exams} exams
- Periods: {num_periods} available periods ({period_dates})
- Rooms: {num_rooms} rooms (capacities: {room_capacities})
- Student enrollments: {num_students} students

## Constraints (Hard — must satisfy)
1. No two exams sharing students can be in the same period
2. Room capacity must not be exceeded
3. Each exam must be assigned to one of its available periods
4. Distribution constraints: {hard_constraints_nl}

## Soft Preferences (minimize penalty)
- Avoid Saturday periods (penalty: {saturday_penalty})
- Avoid Friday afternoon (penalty: {friday_pm_penalty})
- Soft distribution constraints: {soft_constraints_nl}

## Your Task
Assign each exam to a period and one or more rooms.

Output your solution as JSON:
{{
  "assignments": [
    {{"exam_id": "E1", "period_id": "P3", "room_ids": ["R2"]}},
    ...
  ]
}}

Exams to assign: {exam_list}
```

#### 1.2 Implement Baseline Agent Call

Create `src/agent.py`:
```python
import json
import subprocess
import re

def call_llm(prompt: str, model: str = "claude-haiku") -> str:
    """See shared/TOOLING.md for full implementation."""
    if model == "claude-haiku":
        result = subprocess.run(
            ["claude", "--model", "claude-haiku-4-5-20251001", "-p", prompt],
            capture_output=True, text=True, timeout=180
        )
    elif model.startswith("gpt"):
        result = subprocess.run(
            ["codex", "exec", "--model", model, prompt],
            capture_output=True, text=True, timeout=180
        )
    else:
        raise ValueError(f"Unknown model: {model}")

    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def parse_solution(response: str) -> dict:
    """Extract JSON solution from LLM response."""
    match = re.search(r'\{.*\}', response, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return {}


def build_proposal_prompt(instance, template_path: str = "prompts/propose_solution.txt") -> str:
    with open(template_path) as f:
        template = f.read()
    # Fill in instance details
    return template.format(
        num_exams=len(instance.exams),
        num_periods=len(instance.periods),
        # ... fill all placeholders
    )
```

#### 1.3 Measure Baseline
For each of the 9 Purdue instances:
- Run 10 zero-shot proposals (different random seeds / temperature variation via prompt)
- For each: count hard violations, compute penalty
- Record: mean violations, mean penalty, % feasible proposals

```python
# src/evaluate.py
def baseline_run(instance, model: str, n_runs: int = 10):
    results = []
    for run in range(n_runs):
        prompt = build_proposal_prompt(instance)
        response = call_llm(prompt, model)
        solution = parse_solution(response)
        violations = check_hard_constraints(instance, solution)
        penalty = compute_penalty(instance, solution) if not violations else float('inf')
        results.append({
            "run": run, "violations": len(violations),
            "penalty": penalty, "feasible": len(violations) == 0
        })
    return results
```

---

### Phase 2: Self-Evolving Loop — Core Implementation (Week 2–3)

The loop runs for `T` iterations. At each iteration:
1. **Propose**: LLM generates a solution (using current strategy prompt)
2. **Verify**: Formal verifier checks hard constraints, computes penalty
3. **Feedback**: Generate feedback (formal or NL variant)
4. **Reflect**: LLM reflects on failures and updates strategy
5. **Archive**: Store (solution, violations, penalty, reflection) in memory

#### 2.1 Formal Feedback (Treatment)

When violations occur, feed the exact violation trace back:

Create `prompts/reflect_formal.txt`:
```
You proposed the following schedule. The formal constraint verifier found these violations:

## Hard Constraint Violations ({num_violations} total)
{violation_list}

Example violation:
- Type: student_conflict
- Exams: E042, E107
- Shared students: 847
- Period: P05 (Monday 9am)
- Rule: Two exams sharing students cannot be in the same period

## Current Penalty Score: {penalty}
## Previous Best Penalty: {best_penalty}

## Violation Pattern Analysis
Most violated constraint type: {most_common_violation_type} ({count} occurrences)
Exams involved in most violations: {hot_exams}
Most congested period: {busiest_period} ({exams_in_period} exams, {conflict_pairs} conflict pairs)

## Your Task
1. Identify WHY these violations occurred in your previous assignment
2. State a revised strategy to avoid them
3. Propose a corrected assignment

Output format:
{{
  "diagnosis": "...",
  "strategy_update": "...",
  "assignments": [...]
}}
```

#### 2.2 Natural Language Feedback (Control)

Create `prompts/reflect_nl.txt`:
```
You proposed a schedule but it didn't work well.

Some problems observed:
- Some students have overlapping exams
- Some rooms may be too small for their assigned exams
- The schedule could be better organized

Please reflect on what might have gone wrong and propose a better schedule.

Output format:
{{
  "diagnosis": "...",
  "strategy_update": "...",
  "assignments": [...]
}}
```

#### 2.3 Strategy Memory

The agent accumulates a "strategy document" across iterations — an evolving set of rules it has learned.

```python
# src/loop.py
import json
from pathlib import Path

def run_evolving_loop(
    instance,
    model: str,
    feedback_mode: str,   # "formal" or "nl"
    T: int = 20,
    log_dir: str = "logs/"
):
    strategy_memory = []   # list of strategy_update strings from each reflection
    best_solution = None
    best_penalty = float('inf')
    history = []

    for t in range(T):
        # Build proposal prompt, injecting accumulated strategy
        strategy_context = "\n".join([
            f"Iteration {i+1} learning: {s}"
            for i, s in enumerate(strategy_memory[-5:])  # last 5 learnings
        ])
        prompt = build_proposal_prompt(instance, strategy_context=strategy_context)

        # Call LLM
        response = call_llm(prompt, model)
        solution = parse_solution(response)

        # Verify
        violations = check_hard_constraints(instance, solution)
        penalty = compute_penalty(instance, solution) if not violations else float('inf')

        if penalty < best_penalty:
            best_penalty = penalty
            best_solution = solution

        # Generate feedback
        if feedback_mode == "formal":
            feedback_prompt = build_formal_feedback_prompt(
                instance, solution, violations, penalty, best_penalty
            )
        else:
            feedback_prompt = build_nl_feedback_prompt(violations, penalty)

        # Reflect
        reflection = call_llm(feedback_prompt, model)
        reflection_data = parse_solution(reflection)
        strategy_memory.append(reflection_data.get("strategy_update", ""))

        # Log
        entry = {
            "iteration": t,
            "model": model,
            "feedback_mode": feedback_mode,
            "num_violations": len(violations),
            "penalty": penalty,
            "best_penalty": best_penalty,
            "violations": violations,
            "strategy_update": reflection_data.get("strategy_update", ""),
        }
        history.append(entry)

        log_path = Path(log_dir) / f"{model}_{feedback_mode}_{instance.name}.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        print(f"[t={t}] violations={len(violations)} penalty={penalty:.1f} best={best_penalty:.1f}")

    return history, best_solution
```

---

### Phase 3: Experimental Conditions (Week 3–4)

Run the following 2×2 design across all 9 Purdue instances:

| Condition | Model | Feedback |
|---|---|---|
| A | Claude Haiku | Formal (exact violation trace) |
| B | Claude Haiku | NL (vague description) |
| C | GPT-4o-mini (via codex) | Formal |
| D | GPT-4o-mini (via codex) | NL |

For each condition × instance: run T=20 iterations, 3 random seeds (60 total runs per condition).

```bash
# Run all conditions
python src/loop.py --instance data/purdue_exam/exam-fall08.xml \
    --model claude-haiku --feedback formal --iterations 20 --seeds 3

python src/loop.py --instance data/purdue_exam/exam-fall08.xml \
    --model claude-haiku --feedback nl --iterations 20 --seeds 3

# Repeat for all 9 instances and both models
```

#### Generalization Test (Week 4)
- Train (run evolution): instances 1–8
- Test: run only 5 iterations on instance 9, starting from the strategy accumulated on 1–8
- Compare convergence speed vs. starting fresh on instance 9

```python
def generalization_test(train_instances, test_instance, model, feedback_mode):
    # Accumulate strategy from training instances
    combined_strategy = []
    for inst in train_instances:
        history, _ = run_evolving_loop(inst, model, feedback_mode, T=10)
        combined_strategy.extend(extract_strategy_learnings(history))

    # Apply to test instance
    history_test, _ = run_evolving_loop(
        test_instance, model, feedback_mode, T=5,
        initial_strategy=combined_strategy[-10:]
    )
    return history_test
```

---

### Phase 4: Evaluation & Analysis (Week 4–5)

#### 4.1 Primary Metrics
```python
def compute_metrics(history: list) -> dict:
    return {
        # Convergence speed: iteration at which first feasible solution found
        "first_feasible_iter": next(
            (h["iteration"] for h in history if h["num_violations"] == 0), None
        ),
        # Final best penalty (lower is better)
        "final_best_penalty": min(h["penalty"] for h in history),
        # Violation reduction rate
        "violation_trajectory": [h["num_violations"] for h in history],
        # Penalty trajectory
        "penalty_trajectory": [h["penalty"] for h in history],
        # % iterations producing feasible solutions
        "feasibility_rate": sum(1 for h in history if h["num_violations"] == 0) / len(history),
    }
```

#### 4.2 Main Comparison (RQ1)
Plot convergence curves: penalty vs. iteration for formal vs. NL feedback, per model. Expected result: formal feedback converges faster and reaches lower final penalty.

#### 4.3 Strategy Quality Analysis (RQ2)
Manually inspect `strategy_update` strings across iterations. Code them for:
- Specificity (mentions specific exam IDs vs. generic advice)
- Constraint type awareness (correctly identifies which constraint was violated)
- Improvement relevance (strategy addresses actual cause of failure)

2 annotators, Cohen's kappa for reliability.

#### 4.4 Generalization Analysis (RQ3)
Compare first-feasible-iter on test instance (9th) for:
- Fresh start (no accumulated strategy)
- Strategy from training instances (1–8)

#### 4.5 Model Comparison (RQ4)
Claude Haiku vs. GPT-4o-mini: same metrics across all conditions.

---

### Phase 5: Paper Writing (Week 5–6)

#### Paper Structure (8 pages EMNLP format)
1. **Introduction**: Formal feedback as a path to reliable agent self-improvement
2. **Background**: Timetabling problem formulation; self-evolving agent frameworks
3. **Method**: Loop design, formal vs. NL feedback, strategy memory
4. **Experimental Setup**: Datasets, models, conditions, metrics
5. **Results**: Convergence curves, feasibility rates, generalization
6. **Analysis**: Strategy quality coding, failure mode analysis
7. **Related Work**: Gödel Agent, AlphaEvolve, Reflexion, ConstraintBench
8. **Conclusion**: Formal feedback as a general principle for agent self-improvement

#### Key Tables to Produce
- Table 1: First-feasible iteration per condition × instance
- Table 2: Final penalty per condition × instance
- Figure 1: Convergence curves (formal vs. NL, 4 subplots per model)
- Figure 2: Strategy specificity scores across iterations

---

## LLM Call Patterns (Quick Reference)

```bash
# Claude Haiku — proposal step
claude --model claude-haiku-4-5-20251001 -p "$(cat prompts/propose_solution.txt)" > output.txt

# Claude Haiku — reflection step
claude --model claude-haiku-4-5-20251001 -p "$(cat prompts/reflect_formal.txt)" > reflection.txt

# GPT-4o-mini via codex — proposal step
codex exec --model gpt-4o-mini "$(cat prompts/propose_solution.txt)"

# Check codex flag syntax if above fails:
codex --help
```

See [shared/TOOLING.md](../shared/TOOLING.md) for full Python subprocess wrappers.

---

## Expected Timeline

| Week | Deliverable |
|---|---|
| 1 | Data downloaded, parser + verifier working, baseline metrics collected |
| 2 | Proposal + reflection prompts tuned, baseline agent complete |
| 3 | Self-evolving loop implemented and running on all 9 instances |
| 4 | All 4 conditions complete, generalization test done |
| 5 | Analysis, figures, strategy coding |
| 6 | Paper draft |
