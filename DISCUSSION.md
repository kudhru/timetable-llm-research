# Research Discussion Summary

## Context
This document captures the research scoping discussion that led to the two ideas in this repo. It is intended as background for any agent or researcher picking up this work.

---

## Step 1: Understanding UniTime's Research (unitime.org/publications.php)

UniTime is the leading open-source university timetabling system, developed by Tomáš Müller, Hana Rudová, and collaborators over ~25 years. Their publications cluster into:

| Area | Core Problem | Key Techniques |
|---|---|---|
| Solver algorithms | CSP + local search | Iterative Forward Search, arc consistency, conflict-based statistics |
| Problem modeling | Real-world institutional constraints | Soft/hard constraints, curriculum complexity |
| Minimal perturbation | Schedule changes with least disruption | Labeling algorithms, formal CSP perturbation |
| Interactive systems | Human-in-the-loop | UI + automated optimization co-design |
| Student sectioning | Assign students to course sections | Batch + real-time algorithms |

**Key observation**: Everything in UniTime is hand-formalized. A human expert converts institutional rules into constraint programs. LLMs have not been applied.

---

## Step 2: Available Datasets

All datasets have formal ground truth (known solutions, exact penalty functions, binary constraint verification):

| Dataset | Instances | Scale |
|---|---|---|
| Purdue Exam (Fall 2008–Fall 2012) | 9 | 1,800–2,200 exams, 30k–35k students, 235–267 rooms |
| Student Sectioning Fall 2007 | 2 | Real enrollment + timetable |
| ITC 2007 (3 tracks) | Multiple | Competition instances |
| ITC 2019 | Multiple | Real multi-institution course timetabling |

---

## Step 3: EMNLP-Level Research Questions

The generic NLP task framing for timetabling research:

| Generic NLP Task | Timetabling Instantiation |
|---|---|
| Constraint-grounded reasoning | Verify/repair violations given NL constraints + schedule |
| Semantic parsing / NL2Formal | Policy document → formal constraint specification |
| Constrained generation | Generate feasible schedule respecting hard constraints |
| Agentic planning under constraints | LLM agent iteratively builds/repairs a schedule |

### Existing Landscape (as of May 2026)
- **ConstraintBench (2025)**: 10 OR domains, best model 65% feasibility — flat constraints only
- **ConstraintLLM (EMNLP 2025)**: NL→CP model generation, single source, no hierarchy
- **CO-Bench / HeuriGym (2025)**: LLM agents as algorithm designers for CO
- **COMPASS (2025)**: Multi-turn constrained optimization benchmark
- **Gödel Agent / STELLA / MAE / AlphaEvolve (2024–2025)**: Self-evolving agents with soft/human feedback
- **University timetabling + LLMs**: Zero published work — clean gap

---

## Step 4: Shortlisted Ideas (All 6)

1. **Warm-start quality**: LLM-generated initial solutions for solver
2. **Constraint violation detection**: Precision/recall on injected violations
3. **Constraint relaxation ranking**: Which to relax when infeasible (→ Idea 2)
4. **Self-evolving agent with formal feedback** (→ Idea 1)
5. **LLM-guided local search**: LLM as move selector
6. **Multi-source NL→constraint integration**: Long-term direction

---

## Step 5: Final Two Selected

### Idea 1: Self-Evolving Agent with Formal Constraint Feedback
**Why top pick**: Highest ceiling. The finding — formal vs. NL feedback for agent self-improvement — generalizes beyond timetabling. No existing self-evolving agent paper uses a formal verification oracle. Timetabling provides the first clean domain for testing this.

### Idea 2: Constraint Hierarchy Reasoning Benchmark
**Why top pick**: Most tractable for a first paper. Fills a documented gap (ConstraintBench only tests flat feasibility). Benchmark papers with clean ground truth and formal metrics are reliable EMNLP main track material.

---

## Tooling Decision

Both ideas use LLM calls in loops. This system has:
- `claude -p` (Claude Code CLI) — Claude Haiku, no API credits needed
- `codex exec` (Codex CLI v0.130.0) — OpenAI models

Both models will be benchmarked in every experiment for model-agnostic findings.

---

## Key References

- [ConstraintBench](https://arxiv.org/abs/2602.22465)
- [ConstraintLLM @ EMNLP 2025](https://aclanthology.org/2025.emnlp-main.809/)
- [CO-Bench](https://arxiv.org/abs/2504.04310)
- [HeuriGym](https://arxiv.org/abs/2506.07972)
- [COMPASS](https://arxiv.org/abs/2510.07043)
- [Gödel Agent](https://arxiv.org/abs/2410.04444)
- [Multi-Agent Evolve](https://arxiv.org/abs/2510.23595)
- [Temporal Constraint Processing in LLMs](https://arxiv.org/abs/2511.10654)
- [UniTime Publications](https://www.unitime.org/publications.php)
- [Purdue Exam Datasets](https://www.unitime.org/exam_datasets.php)
