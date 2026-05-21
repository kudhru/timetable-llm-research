# LLM Agents for University Timetabling Research

Two independent research directions exploring what LLM agents can do in the university timetabling domain — a field dominated by constraint satisfaction solvers since the early 2000s.

## Why Timetabling?

University timetabling is one of the richest real-world benchmarks for constraint reasoning. It combines temporal, resource, preference, and precedence constraints simultaneously at a scale that synthetic benchmarks don't reach. Crucially, **all datasets have formal ground truth** (known near-optimal solutions, exact penalty functions, binary hard constraint verification) — making LLM behavior measurable without human annotation.

## Research Directions

### [Idea 1: Self-Evolving Agent with Formal Constraint Feedback](./idea1-self-evolving-agent/PLAN.md)
*Does formal, verifiable constraint feedback produce faster and more reliable agent self-improvement than natural language feedback?*

A self-improving agentic loop where the agent proposes schedules, receives exact constraint violation traces as feedback, reflects, and refines its strategy — benchmarked against natural language feedback variants. Novel contribution: existing self-evolving agent work (Gödel Agent, STELLA, MAE) uses soft/human feedback; this is the first application to a domain with a formal, automatic verification oracle.

**Target venue**: EMNLP / ACL (Agents + Reasoning track)

### [Idea 2: Constraint Hierarchy Reasoning Benchmark](./idea2-constraint-hierarchy-benchmark/PLAN.md)
*Can LLMs reason about which constraints to relax, in what order, when a scheduling instance is infeasible?*

A benchmark for hierarchical constraint reasoning — the gap that ConstraintBench (2025) did not address. Builds on Purdue University exam datasets (9 real instances), programmatically over-constrains them, and compares LLM relaxation orderings against solver-optimal orderings.

**Target venue**: EMNLP main track (Benchmark + Analysis paper)

## Tooling

Both directions use local LLM inference via:
- **Claude Haiku** via `claude -p` (Claude Code CLI, no API credits needed)
- **OpenAI model** via `codex exec` (Codex CLI, available on this system)

See [TOOLING.md](./shared/TOOLING.md) for exact command patterns.

## Datasets

| Dataset | Source | Instances | Key Contents |
|---|---|---|---|
| Purdue Exam (Fall 2008–Fall 2012) | unitime.org/exam_datasets.php | 9 | 1800–2200 exams, 30k–35k students, 235–267 rooms, XML format |
| Student Sectioning (Fall 2007) | unitime.org/sct_datasets.php | 2 | Course requests, timetable, batch solutions |
| ITC 2007 (3 tracks) | itc2007.cs.qub.ac.uk | Multiple | Exam + course timetabling competition instances |
| ITC 2019 | itc2019.org | Multiple | Real-world multi-institution course timetabling |

## Background

This research was scoped based on a survey of:
- UniTime publications (unitime.org/publications.php) — the leading open-source timetabling system
- Existing LLM + constraint satisfaction benchmarks: ConstraintBench, ConstraintLLM (EMNLP 2025), CO-Bench, HeuriGym, COMPASS
- Self-evolving agent frameworks: Gödel Agent, STELLA, Multi-Agent Evolve, AlphaEvolve (DeepMind 2025)

Key finding: **University timetabling + LLMs has zero published work** — a clean gap with high-quality public datasets.
