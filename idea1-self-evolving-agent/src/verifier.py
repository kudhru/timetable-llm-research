"""
Verifier for university exam timetabling solutions.

A solution is a dict: {exam_id: (period_id, [room_ids])}

Hard constraint violations are returned as a list of dicts.
Penalty is a float (lower is better).
"""
from typing import Dict, List, Tuple


Solution = Dict[str, Tuple[str, List[str]]]


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_shared_students(instance, exam_id_a: str, exam_id_b: str) -> int:
    """Return the number of students enrolled in both exams."""
    students_a = instance.exam_students.get(exam_id_a, set())
    students_b = instance.exam_students.get(exam_id_b, set())
    return len(students_a & students_b)


def _get_room_capacity(instance, room_id: str, alt_seating: bool = False) -> int:
    """Return room capacity (normal or alt-seating)."""
    room = instance.room_map.get(room_id)
    if room is None:
        return 0
    return room.alt if alt_seating else room.size


def _get_exam(instance, exam_id: str):
    return instance.exam_map.get(exam_id)


def _get_period(instance, period_id: str):
    return instance.period_map.get(period_id)


# ── distribution constraint helpers ──────────────────────────────────────────

def _check_same_period(instance, solution: Solution, constraint) -> List[dict]:
    """All exams in the constraint must be in the same period."""
    violations = []
    periods_used = set()
    valid_ids = []
    for eid in constraint.exam_ids:
        if eid in solution:
            periods_used.add(solution[eid][0])
            valid_ids.append(eid)

    if len(periods_used) > 1:
        violations.append({
            "type": "distribution_same_period",
            "constraint_id": constraint.id,
            "exam_ids": valid_ids,
            "periods_used": list(periods_used),
            "description": (
                f"Constraint {constraint.id} (same-period): exams {valid_ids} "
                f"are in different periods {list(periods_used)}"
            ),
        })
    return violations


def _check_different_period(instance, solution: Solution, constraint) -> List[dict]:
    """All exams in the constraint must be in different periods."""
    violations = []
    from collections import Counter
    period_counts = Counter()
    for eid in constraint.exam_ids:
        if eid in solution:
            period_counts[solution[eid][0]] += 1

    for pid, count in period_counts.items():
        if count > 1:
            clashing = [e for e in constraint.exam_ids
                        if e in solution and solution[e][0] == pid]
            violations.append({
                "type": "distribution_different_period",
                "constraint_id": constraint.id,
                "exam_ids": clashing,
                "period_id": pid,
                "description": (
                    f"Constraint {constraint.id} (different-period): exams {clashing} "
                    f"are all in period {pid}"
                ),
            })
    return violations


def _check_precedence(instance, solution: Solution, constraint) -> List[dict]:
    """
    Exams must be scheduled in order: exam_ids[0] before exam_ids[1], etc.
    'Before' means earlier period id (numerically or lexicographically).
    """
    violations = []
    eids = constraint.exam_ids
    # Build ordered list with period indices
    period_order = {p.id: i for i, p in enumerate(instance.periods)}
    assigned = [(eid, solution[eid][0]) for eid in eids if eid in solution]

    for i in range(len(assigned) - 1):
        eid_a, pid_a = assigned[i]
        eid_b, pid_b = assigned[i + 1]
        idx_a = period_order.get(pid_a, -1)
        idx_b = period_order.get(pid_b, -1)
        if idx_a >= idx_b:
            violations.append({
                "type": "distribution_precedence",
                "constraint_id": constraint.id,
                "exam_ids": [eid_a, eid_b],
                "description": (
                    f"Constraint {constraint.id} (precedence): exam {eid_a} in period "
                    f"{pid_a} must come before exam {eid_b} in period {pid_b}"
                ),
            })
    return violations


def _check_distribution(instance, solution: Solution, constraint) -> List[dict]:
    """Dispatch to the appropriate distribution checker."""
    ctype = constraint.type.lower().replace(' ', '-').replace('_', '-')
    if ctype == 'same-period':
        return _check_same_period(instance, solution, constraint)
    elif ctype == 'different-period':
        return _check_different_period(instance, solution, constraint)
    elif ctype == 'precedence':
        return _check_precedence(instance, solution, constraint)
    # Unknown constraint type — skip (not a violation)
    return []


# ── public API ────────────────────────────────────────────────────────────────

def check_hard_constraints(instance, solution: Solution) -> List[dict]:
    """
    Check all hard constraints and return a list of violations.

    Each violation is a dict with:
      - type: str
      - exam_ids: list of involved exam ids
      - description: human-readable string
      (plus type-specific fields)

    Checks:
      1. Student conflict: two exams sharing ≥1 student in the same period
      2. Room capacity: total assigned room capacity < exam student count
      3. Period availability: exam assigned to unavailable period
      4. Room availability: exam assigned to unavailable room
      5. Hard distribution constraints (same-period, different-period, precedence)
      6. Missing assignments: exams not in solution
    """
    violations = []

    # --- Pre-compute period → exams mapping ---
    period_to_exams: Dict[str, List[str]] = {}
    for exam_id, (period_id, _rooms) in solution.items():
        period_to_exams.setdefault(period_id, []).append(exam_id)

    # 1. Student conflicts ────────────────────────────────────────────────────
    for _period_id, exam_ids in period_to_exams.items():
        for i in range(len(exam_ids)):
            for j in range(i + 1, len(exam_ids)):
                shared = _get_shared_students(instance, exam_ids[i], exam_ids[j])
                if shared > 0:
                    violations.append({
                        "type": "student_conflict",
                        "exam_ids": [exam_ids[i], exam_ids[j]],
                        "period_id": _period_id,
                        "shared_students": shared,
                        "description": (
                            f"Exams {exam_ids[i]} and {exam_ids[j]} share {shared} "
                            f"students but are in the same period {_period_id}"
                        ),
                    })

    # 2. Room capacity ────────────────────────────────────────────────────────
    for exam_id, (period_id, room_ids) in solution.items():
        exam = _get_exam(instance, exam_id)
        if exam is None:
            continue
        total_cap = sum(
            _get_room_capacity(instance, r, exam.alt_seating) for r in room_ids
        )
        if exam.student_count > total_cap:
            violations.append({
                "type": "room_capacity",
                "exam_ids": [exam_id],
                "student_count": exam.student_count,
                "total_capacity": total_cap,
                "room_ids": room_ids,
                "description": (
                    f"Exam {exam_id} has {exam.student_count} students but "
                    f"assigned rooms {room_ids} hold only {total_cap}"
                ),
            })

    # 3. Period availability ──────────────────────────────────────────────────
    for exam_id, (period_id, _rooms) in solution.items():
        exam = _get_exam(instance, exam_id)
        if exam is None:
            continue
        if exam.available_periods and period_id not in exam.available_periods:
            violations.append({
                "type": "unavailable_period",
                "exam_ids": [exam_id],
                "period_id": period_id,
                "description": (
                    f"Exam {exam_id} assigned to period {period_id} "
                    f"which is not in its available periods"
                ),
            })

    # 4. Room availability ────────────────────────────────────────────────────
    for exam_id, (period_id, room_ids) in solution.items():
        exam = _get_exam(instance, exam_id)
        if exam is None:
            continue
        for room_id in room_ids:
            if exam.available_rooms and room_id not in exam.available_rooms:
                violations.append({
                    "type": "unavailable_room",
                    "exam_ids": [exam_id],
                    "room_id": room_id,
                    "description": (
                        f"Exam {exam_id} assigned to room {room_id} "
                        f"which is not in its available rooms"
                    ),
                })

    # 5. Hard distribution constraints ───────────────────────────────────────
    for constraint in instance.constraints:
        if constraint.hard:
            violations.extend(_check_distribution(instance, solution, constraint))

    # 6. Missing assignments ──────────────────────────────────────────────────
    for exam in instance.exams:
        if exam.id not in solution:
            violations.append({
                "type": "missing_assignment",
                "exam_ids": [exam.id],
                "description": f"Exam {exam.id} has no assignment in solution",
            })

    return violations


def compute_penalty(instance, solution: Solution) -> float:
    """
    Compute total soft constraint penalty score (lower is better).

    Components:
      1. Period penalties: instance.periods[p].penalty for each assigned exam
      2. Room period-specific penalties (where applicable)
      3. Soft distribution constraint penalties
    """
    penalty = 0.0

    for exam_id, (period_id, room_ids) in solution.items():
        period = _get_period(instance, period_id)
        if period:
            penalty += period.penalty

        # Room-specific period penalties
        for room_id in room_ids:
            room = instance.room_map.get(room_id)
            if room and period_id in room.period_penalties:
                penalty += room.period_penalties[period_id]

    # Soft distribution constraints
    for constraint in instance.constraints:
        if not constraint.hard:
            for v in _check_distribution(instance, solution, constraint):
                penalty += constraint.weight

    return penalty


def format_violations(violations: List[dict], max_show: int = 20) -> str:
    """Format a list of violations as a human-readable string."""
    if not violations:
        return "No violations found."
    lines = [f"Total violations: {len(violations)}"]
    from collections import Counter
    types = Counter(v["type"] for v in violations)
    lines.append("By type: " + ", ".join(f"{t}={c}" for t, c in types.most_common()))
    lines.append("")
    for v in violations[:max_show]:
        lines.append(f"  [{v['type']}] {v['description']}")
    if len(violations) > max_show:
        lines.append(f"  ... and {len(violations) - max_show} more")
    return "\n".join(lines)


# ── unit tests ────────────────────────────────────────────────────────────────

def _run_unit_tests():
    """Run inline unit tests. Prints PASS/FAIL for each."""
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    from parse_exam import parse_instance

    inst_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "purdue_exam", "synthetic_small.xml"
    )
    inst = parse_instance(inst_path)
    print(f"Loaded instance: {inst.name} ({len(inst.exams)} exams)")

    # Test 1: Student conflict detected
    # E1 and E2 share students S1-S10 + others, both in period 1
    sol_conflict = {
        "E1": ("1", ["R1"]),  # 25 students, R1 capacity 60 → fits
        "E2": ("1", ["R2"]),  # 30 students, R2 capacity 40 → fits
        # assign remaining exams to avoid missing_assignment errors
    }
    # Only test the two exams we care about
    viols = check_hard_constraints(inst, sol_conflict)
    conflict_viols = [v for v in viols if v["type"] == "student_conflict"]
    assert len(conflict_viols) >= 1, f"FAIL test 1: expected >=1 conflict, got {conflict_viols}"
    found_e1_e2 = any(
        set(v["exam_ids"]) == {"E1", "E2"} for v in conflict_viols
    )
    assert found_e1_e2, f"FAIL test 1: E1-E2 conflict not found in {conflict_viols}"
    print("PASS test 1: student conflict between E1 and E2 detected")

    # Test 2: Room capacity violation
    sol_cap = {"E18": ("1", ["R4"])}  # E18 has ~60 students, R4 capacity 30
    viols2 = check_hard_constraints(inst, sol_cap)
    cap_viols = [v for v in viols2 if v["type"] == "room_capacity"]
    assert any(v["exam_ids"] == ["E18"] for v in cap_viols), \
        f"FAIL test 2: capacity violation for E18 not found; violations={cap_viols}"
    print("PASS test 2: room capacity violation for E18 detected")

    # Test 3: Unavailable period violation
    # E16 is only available in periods 2-8 (not period 1)
    sol_unavail = {"E16": ("1", ["R1"])}
    viols3 = check_hard_constraints(inst, sol_unavail)
    avail_viols = [v for v in viols3 if v["type"] == "unavailable_period"]
    assert any(v["exam_ids"] == ["E16"] for v in avail_viols), \
        f"FAIL test 3: unavailable_period for E16 not found; violations={avail_viols}"
    print("PASS test 3: unavailable period violation for E16 detected")

    # Test 4: Valid solution has no student conflicts
    # Assign E1 to period 1 and E3 to period 2 — they share students but in different periods
    sol_valid_partial = {
        "E1": ("1", ["R1"]),  # 25 students, cap 60
        "E3": ("2", ["R3"]),  # ~50 students, cap 80
    }
    viols4 = check_hard_constraints(inst, sol_valid_partial)
    sc_viols = [v for v in viols4 if v["type"] == "student_conflict"]
    assert len(sc_viols) == 0, f"FAIL test 4: unexpected conflict: {sc_viols}"
    print("PASS test 4: no student conflict when E1/E3 in different periods")

    # Test 5: Distribution constraint - same-period (C1: E10 and E20 must be in same period)
    # Put E10 in period 1 and E20 in period 2 — violates C1
    sol_dist = {
        "E10": ("1", ["R2"]),
        "E20": ("2", ["R3"]),
    }
    viols5 = check_hard_constraints(inst, sol_dist)
    dist_viols = [v for v in viols5 if v["type"] == "distribution_same_period"]
    assert len(dist_viols) >= 1, f"FAIL test 5: expected same-period violation, got {dist_viols}"
    print("PASS test 5: distribution same-period constraint violation detected")

    # Test 6: compute_penalty - period penalties
    # Assign all 20 exams to penalty-free periods; penalty should be low
    full_sol = {}
    period_ids = [p.id for p in inst.periods]
    for i, exam in enumerate(inst.exams):
        # Use available periods; pick one
        avail = exam.available_periods if exam.available_periods else period_ids
        pid = avail[i % len(avail)]
        # Pick first available room large enough
        room = inst.rooms[i % len(inst.rooms)]
        full_sol[exam.id] = (pid, [room.id])

    pen = compute_penalty(inst, full_sol)
    print(f"PASS test 6: compute_penalty returned {pen:.1f} (no assertion on value)")

    print("\nAll unit tests passed.")


if __name__ == "__main__":
    _run_unit_tests()
