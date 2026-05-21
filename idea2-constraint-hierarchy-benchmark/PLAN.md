# Idea 2: Constraint Hierarchy Reasoning Benchmark

## Research Question
Can LLMs reason about constraint hierarchies — specifically, which constraints to relax and in what order when a scheduling instance is infeasible?

## Core Hypothesis
ConstraintBench (2025) established that LLMs fail at binary feasibility. But real-world scheduling has layered hierarchies: hard constraints (accreditation, room capacity) → weighted soft constraints (faculty preferences) → weakly preferred heuristics (student convenience). No benchmark tests whether LLMs can reason about this structure. We hypothesize that LLMs have systematic biases in relaxation ordering that correlate with surface linguistic features of constraints rather than their structural impact on feasibility.

## Target Venue
EMNLP main track — Benchmark + Analysis paper

## Novel Contribution vs. Prior Work
- **ConstraintBench (2025)**: Tests binary feasibility across 10 OR domains — flat constraints only, best model 65% feasible
- **ConstraintLLM (EMNLP 2025)**: NL→CP model generation — single source, no hierarchy reasoning
- **This work**: First benchmark for *hierarchical* constraint reasoning on real-world instances with formal ground truth from an actual solver

---

## Step-by-Step Execution Plan

### Phase 0: Setup (Week 1)

#### 0.1 Download Dataset
```bash
# Purdue Examination Timetabling (9 instances, XML format)
# URL: https://www.unitime.org/exam_datasets.php
# Download the zip containing all 9 instances

mkdir -p data/purdue_exam data/over_constrained data/benchmark
```

#### 0.2 Install UniTime Solver (for ground truth)
```bash
# UniTime is a Java application available at unitime.org
# Download: https://github.com/UniTime/unitime (or unitime.org downloads)
# We use it to compute optimal relaxation orderings

# Alternatively: implement a lightweight CP solver in Python using OR-Tools
pip install ortools

# OR-Tools can solve/verify these instances and enumerate relaxation orderings
```

#### 0.3 Understand the Constraint Space

The Purdue exam XML instances contain these constraint types:
```
Hard constraints:
  - student_conflict: two exams with shared students cannot overlap in time
  - room_capacity: exam enrollment <= assigned room capacity
  - period_availability: exam can only go in certain periods
  - room_availability: exam can only use certain rooms
  - distribution_hard: same-period, diff-period, precedence, same-room, diff-room

Soft constraints (with penalty weights):
  - period_penalty: penalty for using a specific period (Saturday, Friday PM)
  - room_penalty: penalty for using a specific room
  - distribution_soft: weighted soft versions of distribution constraints
```

---

### Phase 1: Build Over-Constrained Instances (Week 1–2)

The benchmark requires instances where the constraint set is infeasible. We create these programmatically from the 9 real instances.

#### 1.1 Over-Constraining Strategy

Three strategies to create infeasibility:

**Strategy A — Room Removal**: Remove N rooms until no feasible assignment exists
```python
# src/over_constrain.py

def remove_rooms_until_infeasible(instance, step=1):
    """Remove rooms one by one until instance becomes infeasible."""
    import copy
    from ortools.sat.python import cp_model

    current = copy.deepcopy(instance)
    removals = []

    while is_feasible(current):
        # Remove room with smallest capacity (least useful)
        room_to_remove = min(current.rooms, key=lambda r: r.size)
        current.rooms.remove(room_to_remove)
        removals.append(room_to_remove.id)

    # Return: over-constrained instance + list of removed rooms (these are the "added constraints")
    return current, removals
```

**Strategy B — Exam Addition**: Add N synthetic exams with large enrollments
```python
def add_large_exams(instance, n_exams=5, enrollment=500):
    """Add exams that are hard to place due to size."""
    import copy
    new_instance = copy.deepcopy(instance)
    for i in range(n_exams):
        new_exam = Exam(
            id=f"SYNTHETIC_{i}",
            length=120,
            alt_seating=False,
            max_rooms=1,
            available_periods=[p.id for p in new_instance.periods[:10]],  # restricted
            available_rooms=[r.id for r in new_instance.rooms if r.size >= enrollment],
            student_count=enrollment
        )
        new_instance.exams.append(new_exam)
    return new_instance
```

**Strategy C — Hard Distribution Constraints**: Add conflicting same-period constraints
```python
def add_conflicting_distributions(instance, n_constraints=10):
    """Add hard same-period constraints between exams that share students."""
    import copy, random
    new_instance = copy.deepcopy(instance)

    # Find high-enrollment exam pairs (most likely to create infeasibility)
    candidates = find_conflicting_pairs(instance)
    selected = random.sample(candidates, min(n_constraints, len(candidates)))

    for pair in selected:
        constraint = DistributionConstraint(
            id=f"ADDED_{pair[0]}_{pair[1]}",
            type="same-period",
            exam_ids=list(pair),
            hard=True
        )
        new_instance.constraints.append(constraint)

    return new_instance
```

#### 1.2 Generate Benchmark Instances

For each of 9 real instances × 3 strategies × 3 difficulty levels (mild/moderate/severe) = **81 over-constrained instances**

```python
def generate_benchmark(instances, output_dir="data/over_constrained"):
    benchmark = []
    for instance in instances:
        for strategy in ["room_removal", "exam_addition", "distribution"]:
            for difficulty in ["mild", "moderate", "severe"]:
                oc_instance, changes = apply_strategy(instance, strategy, difficulty)
                # Verify it's actually infeasible
                if not is_feasible(oc_instance):
                    benchmark.append({
                        "base_instance": instance.name,
                        "strategy": strategy,
                        "difficulty": difficulty,
                        "instance": oc_instance,
                        "changes": changes,
                    })
    return benchmark
```

---

### Phase 2: Compute Ground Truth Relaxation Orderings (Week 2)

For each over-constrained instance, compute the solver-optimal relaxation ordering: the order in which removing constraints most improves feasibility.

#### 2.1 Define "Relaxation" Options

For each over-constrained instance, enumerate candidate relaxations:
```python
def enumerate_relaxations(instance):
    """
    Returns list of (relaxation_id, description, relaxation_action)
    Each relaxation makes one change that might restore feasibility.
    """
    relaxations = []

    # Option 1: Relax a hard distribution constraint to soft
    for c in instance.constraints:
        if c.hard:
            relaxations.append({
                "id": f"soften_{c.id}",
                "description": f"Convert constraint {c.id} ({c.type} between {c.exam_ids}) from hard to soft",
                "action": ("soften_constraint", c.id),
                "constraint_type": c.type,
                "affected_exams": c.exam_ids,
            })

    # Option 2: Extend period availability for a restricted exam
    for exam in instance.exams:
        if len(exam.available_periods) < len(instance.periods) * 0.5:
            relaxations.append({
                "id": f"extend_periods_{exam.id}",
                "description": f"Allow exam {exam.id} to be scheduled in additional periods",
                "action": ("extend_period_availability", exam.id),
                "constraint_type": "period_availability",
                "affected_exams": [exam.id],
            })

    # Option 3: Re-admit a removed room (Strategy A instances only)
    # ... etc.

    return relaxations
```

#### 2.2 Score Each Relaxation
```python
def score_relaxation(instance, relaxation):
    """
    Apply relaxation and measure improvement:
    - 1.0 if applying this relaxation alone restores full feasibility
    - float in [0,1] proportional to fraction of violations removed
    - 0.0 if relaxation has no effect
    """
    modified = apply_relaxation(instance, relaxation)
    violations_before = len(check_hard_constraints(instance, solve_greedily(instance)))
    violations_after = len(check_hard_constraints(modified, solve_greedily(modified)))

    if is_feasible(modified):
        return 1.0
    elif violations_before == 0:
        return 0.0
    else:
        return (violations_before - violations_after) / violations_before
```

#### 2.3 Compute Optimal Ordering
```python
def compute_optimal_relaxation_order(instance):
    """
    Greedy optimal: at each step, pick the relaxation that reduces
    violations the most.
    Returns ordered list of relaxation_ids.
    """
    relaxations = enumerate_relaxations(instance)
    remaining = list(relaxations)
    order = []
    current_instance = instance

    while remaining and not is_feasible(current_instance):
        scores = [(r, score_relaxation(current_instance, r)) for r in remaining]
        best = max(scores, key=lambda x: x[1])
        order.append(best[0])
        current_instance = apply_relaxation(current_instance, best[0])
        remaining.remove(best[0])

    return order  # Ground truth ordering
```

This gives you ground truth for every benchmark instance.

---

### Phase 3: Design LLM Evaluation Tasks (Week 2–3)

Three task types of increasing difficulty:

#### Task Type 1: Binary Relaxation Selection (Easiest)
*"Given these two relaxation options, which one should be applied first to move toward feasibility?"*

```python
def build_binary_selection_prompt(instance, relaxation_a, relaxation_b):
    return f"""
A university exam schedule is infeasible. You must choose which constraint to relax first.

## Current Violations
{format_violations(check_hard_constraints_greedy(instance))}

## Option A
{relaxation_a['description']}
Affects: {relaxation_a['affected_exams']}
Constraint type: {relaxation_a['constraint_type']}

## Option B
{relaxation_b['description']}
Affects: {relaxation_b['affected_exams']}
Constraint type: {relaxation_b['constraint_type']}

Which option should be relaxed first to make the schedule feasible?
Answer with just "A" or "B", followed by one sentence of reasoning.
"""
```

Metric: Accuracy (binary classification vs. ground truth)

#### Task Type 2: Ranking (Medium)
*"Rank these 5 relaxation options from most to least important."*

```python
def build_ranking_prompt(instance, relaxations):
    options = "\n".join([
        f"{chr(65+i)}. {r['description']} [type: {r['constraint_type']}]"
        for i, r in enumerate(relaxations)
    ])
    return f"""
A university exam schedule is infeasible due to multiple constraint violations.

## Violations Summary
- Total hard violations: {count_violations(instance)}
- Violation types: {summarize_violation_types(instance)}

## Relaxation Options (rank from most to least important)
{options}

Rank these options A through {chr(65+len(relaxations)-1)} from highest to lowest priority.
Format: "Ranking: X > Y > Z > ..." followed by reasoning.
"""
```

Metric: Kendall's tau vs. ground truth ordering

#### Task Type 3: Full Relaxation Chain (Hardest)
*"Propose a sequence of relaxations to restore feasibility."*

```python
def build_chain_prompt(instance):
    return f"""
This exam schedule is infeasible. Propose a sequence of constraint relaxations to restore feasibility.

## Instance
- Exams: {len(instance.exams)}, Students: {total_students(instance)}
- Periods: {len(instance.periods)}, Rooms: {len(instance.rooms)}

## Current Violations
{format_all_violations(instance)}

## Available Relaxations
{format_all_relaxations(instance)}

Propose an ordered sequence of relaxations. Output as JSON:
{{
  "sequence": [
    {{"relaxation_id": "...", "reason": "..."}},
    ...
  ]
}}
"""
```

Metric: (1) Does sequence restore feasibility? (2) Length vs. optimal length (shorter = better). (3) Overlap with optimal sequence.

---

### Phase 4: Run Benchmark (Week 3–4)

#### 4.1 Models to Evaluate

| Model | CLI Command |
|---|---|
| Claude Haiku | `claude --model claude-haiku-4-5-20251001 -p "..."` |
| Claude Sonnet | `claude --model claude-sonnet-4-6 -p "..."` |
| GPT-4o-mini | `codex exec --model gpt-4o-mini "..."` |
| GPT-4o | `codex exec --model gpt-4o "..."` |

#### 4.2 Run Script
```python
# src/run_benchmark.py
import json
from pathlib import Path

def run_full_benchmark(benchmark_instances, models, output_dir="results/"):
    results = []

    for instance_data in benchmark_instances:
        instance = instance_data["instance"]
        ground_truth = compute_optimal_relaxation_order(instance)

        for model_name, call_fn in models.items():
            # Task 1: Binary selection
            binary_results = []
            pairs = generate_pairs(enumerate_relaxations(instance), n=20)
            for r_a, r_b in pairs:
                prompt = build_binary_selection_prompt(instance, r_a, r_b)
                response = call_fn(prompt)
                predicted = parse_binary_choice(response)
                correct = (predicted == "A") == (ground_truth.index(r_a) < ground_truth.index(r_b))
                binary_results.append(correct)

            # Task 2: Ranking
            relaxations = enumerate_relaxations(instance)[:5]
            prompt = build_ranking_prompt(instance, relaxations)
            response = call_fn(prompt)
            predicted_order = parse_ranking(response)
            tau = compute_kendall_tau(predicted_order, ground_truth[:5])

            # Task 3: Chain
            prompt = build_chain_prompt(instance)
            response = call_fn(prompt)
            chain = parse_chain(response)
            chain_metrics = evaluate_chain(instance, chain, ground_truth)

            results.append({
                "instance": instance_data["base_instance"],
                "strategy": instance_data["strategy"],
                "difficulty": instance_data["difficulty"],
                "model": model_name,
                "binary_accuracy": sum(binary_results) / len(binary_results),
                "ranking_tau": tau,
                "chain_feasibility_restored": chain_metrics["feasible"],
                "chain_length": chain_metrics["length"],
                "chain_overlap": chain_metrics["overlap"],
            })

    Path(output_dir).mkdir(exist_ok=True)
    with open(f"{output_dir}/benchmark_results.json", "w") as f:
        json.dump(results, f, indent=2)

    return results
```

#### 4.3 Execute
```bash
# Run benchmark for all models
python src/run_benchmark.py \
    --instances data/over_constrained/ \
    --models claude-haiku claude-sonnet gpt-4o-mini gpt-4o \
    --output results/

# Estimated: ~81 instances × 4 models × ~30 LLM calls each = ~10,000 calls
# Claude Haiku: fast enough for this volume
```

---

### Phase 5: Analysis (Week 4)

#### 5.1 Primary Results

**Binary Accuracy** (Task 1): How often does each model pick the better relaxation?
- Expected baseline: 50% (random)
- Human upper bound: compute by asking 3 domain experts

**Ranking Quality** (Task 2): Kendall's tau averaged across instances
- τ = 1.0: perfect ordering
- τ = 0.0: random
- τ < 0: systematically wrong (interesting finding!)

**Chain Quality** (Task 3): % of instances where model's chain restores feasibility

#### 5.2 Failure Mode Analysis (EMNLP's Favorite Section)

```python
def analyze_failure_modes(results):
    """
    Categorize why LLMs get relaxation ordering wrong.
    """
    failures = [r for r in results if r["binary_accuracy"] < 0.6]

    # Hypothesis 1: LLMs prefer relaxing constraints with more familiar language
    # Test: do failures correlate with constraint name complexity?

    # Hypothesis 2: LLMs prefer relaxing constraints affecting fewer exams
    # (small-scope bias) even when large-scope relaxations are more impactful
    # Test: compare affected_exams count for chosen vs. optimal relaxation

    # Hypothesis 3: LLMs fail more on distribution constraints vs. capacity constraints
    # (distribution constraints are more abstract)
    # Test: break down accuracy by constraint_type

    # Hypothesis 4: Difficulty scales with number of interacting constraints
    # Test: correlate accuracy with constraint interaction graph density
```

#### 5.3 Constraint Type Breakdown
```python
def accuracy_by_constraint_type(results):
    by_type = {}
    for r in results:
        ct = r["constraint_type"]
        by_type.setdefault(ct, []).append(r["correct"])
    return {ct: sum(v)/len(v) for ct, v in by_type.items()}
```

Expected: LLMs perform worst on distribution constraints (inter-exam relationships) vs. capacity constraints (single-exam, more concrete).

#### 5.4 Difficulty Scaling
Plot accuracy vs. difficulty (mild/moderate/severe) for each model and task type. Expected: sharp drop from mild to severe for all models, with larger drops for smaller models.

---

### Phase 6: Benchmark Release & Paper (Week 5–6)

#### 6.1 Benchmark Artifact
Release as a public dataset on HuggingFace or GitHub:
```
constraint-hierarchy-benchmark/
├── instances/
│   ├── room_removal_mild_*.json
│   ├── room_removal_moderate_*.json
│   ├── distribution_severe_*.json
│   └── ...
├── ground_truth/
│   ├── optimal_orderings.json
│   └── scores.json
├── evaluation/
│   └── evaluate.py           (standardized evaluation script)
└── README.md
```

Each instance JSON:
```json
{
  "instance_id": "purdue_fall08_room_removal_mild_01",
  "base_dataset": "purdue_exam_fall08",
  "overconstrain_strategy": "room_removal",
  "difficulty": "mild",
  "num_exams": 1802,
  "num_violations": 12,
  "relaxations": [...],
  "ground_truth_ordering": ["relax_id_3", "relax_id_7", ...],
  "constraint_nl_descriptions": {
    "relax_id_3": "Remove the requirement that Exam E042 must be scheduled in the morning",
    ...
  }
}
```

#### 6.2 Paper Structure (8 pages EMNLP format)

1. **Introduction**: Gap — ConstraintBench tests feasibility, not hierarchy reasoning
2. **Problem Formulation**: Constraint hierarchy, relaxation ordering task
3. **Benchmark Construction**: Over-constraining strategies, ground truth computation
4. **Experiments**: 3 task types × 4 models × 81 instances
5. **Results**: Binary accuracy, Kendall's tau, chain feasibility
6. **Analysis**: Failure modes, constraint type breakdown, difficulty scaling
7. **Related Work**: ConstraintBench, ConstraintLLM, PlanBench, COMPASS
8. **Conclusion + Benchmark Release**

#### Key Tables to Produce
- Table 1: Binary accuracy by model × difficulty
- Table 2: Kendall's tau by model × constraint type
- Table 3: Chain feasibility rate by model × difficulty
- Figure 1: Accuracy vs. difficulty curves (all models, all task types)
- Figure 2: Accuracy breakdown by constraint type (heatmap: model × constraint_type)

---

## LLM Call Patterns (Quick Reference)

```bash
# Task 1 — Binary selection with Claude Haiku
claude --model claude-haiku-4-5-20251001 -p "$(python src/build_prompt.py --task binary --instance X --pair A B)"

# Task 2 — Ranking with GPT-4o-mini
codex exec --model gpt-4o-mini "$(python src/build_prompt.py --task ranking --instance X)"

# Task 3 — Full chain with Claude Sonnet
claude --model claude-sonnet-4-6 -p "$(python src/build_prompt.py --task chain --instance X)"

# Batch run all instances for one model
python src/run_benchmark.py --model claude-haiku --task binary

# Check codex flag syntax if needed:
codex --help
```

See [shared/TOOLING.md](../shared/TOOLING.md) for Python subprocess wrappers and logging.

---

## Expected Timeline

| Week | Deliverable |
|---|---|
| 1 | Datasets downloaded, parser working, OR-Tools solver integrated |
| 2 | 81 over-constrained instances generated, ground truth orderings computed |
| 3 | Prompts for all 3 task types designed and pilot-tested on 5 instances |
| 4 | Full benchmark run complete across all models |
| 5 | Failure mode analysis, figures, human baseline (3 domain experts) |
| 6 | Paper draft + benchmark artifact released |

## Dependency Note for Claude Code

To run the solver and generate ground truth, you need:
```bash
pip install ortools numpy pandas scipy matplotlib seaborn
```

The benchmark generation (Phase 1–2) must complete before Phase 3 (LLM evaluation). All subsequent phases are independent per instance and can be parallelized.
