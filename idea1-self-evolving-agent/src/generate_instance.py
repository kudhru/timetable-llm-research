"""
Parametric instance generator for exam timetabling.

Produces synthetic ExamInstance objects (compatible with parse_exam.py dataclasses)
with controllable difficulty parameterized by n_exams.

Primary difficulty parameter: n_exams

Fixed secondary parameters (held constant across the binary search):
  - n_periods = max(10, n_exams // 4)
  - n_rooms = max(5, n_exams // 8)
  - students_per_exam = 30 (average enrollment)
  - enrollment_overlap = 0.3 (fraction of exam pairs sharing >=1 student)
  - room_tightness = 0.75 (total room capacity / total enrollment)
  - constraint_density = 0.05 (hard distribution constraints per exam)
"""
import random
import xml.etree.ElementTree as ET
from xml.dom import minidom
import os
import sys
from typing import Optional, Dict, List, Set

sys.path.insert(0, os.path.dirname(__file__))
from parse_exam import (
    ExamInstance, Period, Room, Exam, Student, DistributionConstraint
)


# ── Constants ─────────────────────────────────────────────────────────────────

STUDENTS_PER_EXAM = 30
ENROLLMENT_OVERLAP = 0.3
ROOM_TIGHTNESS = 0.75
CONSTRAINT_DENSITY = 0.05


# ── Greedy solver (for feasibility verification) ──────────────────────────────

def get_shared_students(instance: ExamInstance, exam_id_a: str, exam_id_b: str) -> int:
    """Return the number of students enrolled in both exams."""
    students_a = instance.exam_students.get(exam_id_a, set())
    students_b = instance.exam_students.get(exam_id_b, set())
    return len(students_a & students_b)


def greedy_solve(instance: ExamInstance) -> Optional[dict]:
    """
    Assign exams to periods greedily, avoiding student conflicts.

    Returns a solution dict {exam_id: (period_id, [room_ids])} or None if infeasible.
    """
    solution = {}
    period_exams: Dict[str, List[str]] = {p.id: [] for p in instance.periods}

    # Sort exams by enrollment descending (hardest to place first)
    sorted_exams = sorted(instance.exams, key=lambda e: e.student_count, reverse=True)

    for exam in sorted_exams:
        placed = False
        for period in instance.periods:
            if period.id not in exam.available_periods:
                continue
            # Check no student conflict with already-placed exams
            conflict = False
            for placed_exam_id in period_exams[period.id]:
                if get_shared_students(instance, exam.id, placed_exam_id) > 0:
                    conflict = True
                    break
            if not conflict:
                # Find rooms with enough capacity
                needed = exam.student_count
                assigned_rooms = []
                for room in sorted(instance.rooms, key=lambda r: r.size):
                    if room.id in exam.available_rooms:
                        assigned_rooms.append(room.id)
                        needed -= room.size
                        if needed <= 0:
                            break
                if needed <= 0:
                    solution[exam.id] = (period.id, assigned_rooms)
                    period_exams[period.id].append(exam.id)
                    placed = True
                    break
        if not placed:
            return None  # infeasible
    return solution


# ── Instance generator ────────────────────────────────────────────────────────

def generate_instance(
    n_exams: int,
    seed: int = 42,
    students_per_exam: int = STUDENTS_PER_EXAM,
    enrollment_overlap: float = ENROLLMENT_OVERLAP,
    room_tightness: float = ROOM_TIGHTNESS,
    constraint_density: float = CONSTRAINT_DENSITY,
) -> ExamInstance:
    """
    Generate a synthetic ExamInstance with n_exams exams.

    The instance is guaranteed to be feasibly solvable (verified by the greedy solver).
    If the initial room configuration is infeasible, room count is increased until feasible.

    Args:
        n_exams: Number of exams (primary difficulty parameter)
        seed: Random seed for reproducibility
        students_per_exam: Average number of students per exam
        enrollment_overlap: Fraction of exam pairs that share >=1 student
        room_tightness: Total room capacity / total enrollment (>1 = excess capacity)
        constraint_density: Hard distribution constraints per exam

    Returns:
        ExamInstance with indexes built and verified feasible
    """
    rng = random.Random(seed)

    n_periods = max(10, n_exams // 4)
    n_rooms = max(5, n_exams // 8)

    instance = _build_instance(
        n_exams=n_exams,
        n_periods=n_periods,
        n_rooms=n_rooms,
        seed=seed,
        rng=rng,
        students_per_exam=students_per_exam,
        enrollment_overlap=enrollment_overlap,
        room_tightness=room_tightness,
        constraint_density=constraint_density,
    )

    # Verify feasibility; if not, increase room count
    solution = greedy_solve(instance)
    extra_rooms = 0
    while solution is None and extra_rooms < 20:
        extra_rooms += 1
        instance = _build_instance(
            n_exams=n_exams,
            n_periods=n_periods,
            n_rooms=n_rooms + extra_rooms,
            seed=seed,
            rng=random.Random(seed),  # Reset rng for determinism
            students_per_exam=students_per_exam,
            enrollment_overlap=enrollment_overlap,
            room_tightness=room_tightness,
            constraint_density=constraint_density,
        )
        solution = greedy_solve(instance)

    if solution is None:
        raise RuntimeError(
            f"Could not generate feasible instance with n_exams={n_exams} "
            f"even after adding {extra_rooms} extra rooms"
        )

    return instance


def _build_instance(
    n_exams: int,
    n_periods: int,
    n_rooms: int,
    seed: int,
    rng: random.Random,
    students_per_exam: int,
    enrollment_overlap: float,
    room_tightness: float,
    constraint_density: float,
) -> ExamInstance:
    """Build the ExamInstance without feasibility check."""

    # ── 1. Periods ────────────────────────────────────────────────────────────
    periods = []
    days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    times = ["8:00a", "10:00a", "1:00p", "3:00p", "6:00p"]
    for i in range(n_periods):
        day = days[i % len(days)]
        time = times[(i // len(days)) % len(times)]
        # 90% of periods have penalty=0; 10% have penalty=1
        penalty = 0 if rng.random() < 0.9 else 1
        periods.append(Period(
            id=str(i + 1),
            length=120,
            day=day,
            time=time,
            penalty=penalty,
        ))

    # ── 2. Students and enrollments ───────────────────────────────────────────
    exam_ids = [str(i + 1) for i in range(n_exams)]

    # Build enrollment using a graph-coloring-aware approach.
    #
    # Key constraint: The conflict graph must be n_periods-colorable so the
    # greedy solver can find a valid schedule. With n_periods = max(10, n_exams//4),
    # each "color class" (period) holds ~n_exams/n_periods = 4 exams.
    #
    # Strategy:
    # 1. Pre-assign a "period color" to each exam (round-robin ensures balance)
    # 2. Students take 2 exams: one from their "home" period group, one from a
    #    different group. This creates a conflict graph that is exactly n_periods-colorable
    #    by construction (same-color exams never conflict).
    # 3. enrollment_overlap parameter controls cross-group conflict density.

    exam_student_sets: Dict[str, Set[str]] = {eid: set() for eid in exam_ids}
    student_exam_map: Dict[str, List[str]] = {}

    # Assign each exam a "color" (period group) — round-robin
    exam_color = {}
    color_exams: Dict[int, List[str]] = {}
    for i, eid in enumerate(exam_ids):
        c = i % n_periods
        exam_color[eid] = c
        color_exams.setdefault(c, []).append(eid)

    # Generate students
    s_idx = 0
    for eid in exam_ids:
        c = exam_color[eid]
        # Exams in OTHER colors (can conflict with eid)
        other_color_exams = [e for e in exam_ids if exam_color[e] != c]

        for _ in range(students_per_exam):
            sid = str(s_idx + 1)
            s_idx += 1

            # Primary enrollment: this exam
            chosen = [eid]

            # With probability enrollment_overlap, enroll in one exam from a different color
            # This is the ONLY way cross-color conflicts are created
            if rng.random() < enrollment_overlap and other_color_exams:
                # Pick an exam from a randomly chosen other color
                other_colors = list(set(range(n_periods)) - {c})
                target_color = rng.choice(other_colors)
                candidates = color_exams.get(target_color, [])
                if candidates:
                    chosen.append(rng.choice(candidates))

            # Deduplicate
            chosen = list(dict.fromkeys(chosen))
            student_exam_map[sid] = chosen
            for e in chosen:
                exam_student_sets[e].add(sid)

    students = [
        Student(id=sid, exam_ids=eids)
        for sid, eids in student_exam_map.items()
    ]

    # ── 3. Rooms ──────────────────────────────────────────────────────────────
    # Total enrollment across all exams
    total_enrollment = sum(len(exam_student_sets[eid]) for eid in exam_ids)

    # Target total capacity = total_enrollment / room_tightness
    target_total_cap = int(total_enrollment / room_tightness)
    base_room_size = max(30, target_total_cap // n_rooms)

    rooms = []
    for r_idx in range(n_rooms):
        # Vary room sizes: some large, some small
        size_factor = rng.uniform(0.5, 2.0)
        size = max(20, int(base_room_size * size_factor))
        alt = max(10, size // 2)
        rooms.append(Room(
            id=str(r_idx + 1),
            size=size,
            alt=alt,
            coordinates="0.0,0.0",
            period_penalties={},
        ))

    # ── 4. Exams ──────────────────────────────────────────────────────────────
    # Build conflict graph: pairs of exams that share students
    conflict_pairs: Set[frozenset] = set()
    for sid, eids in student_exam_map.items():
        for i in range(len(eids)):
            for j in range(i + 1, len(eids)):
                conflict_pairs.add(frozenset([eids[i], eids[j]]))

    exams = []
    all_period_ids = [p.id for p in periods]
    all_room_ids = [r.id for r in rooms]

    for eid in exam_ids:
        student_count = len(exam_student_sets[eid])
        # All periods are available for all exams (simplification)
        avail_periods = list(all_period_ids)
        # All rooms are available for all exams
        avail_rooms = list(all_room_ids)
        exams.append(Exam(
            id=eid,
            length=120,
            alt_seating=False,
            max_rooms=4,
            available_periods=avail_periods,
            available_rooms=avail_rooms,
            student_count=student_count,
        ))

    # ── 5. Distribution constraints ───────────────────────────────────────────
    # Add hard same-period or different-period constraints between non-conflicting pairs
    n_constraints = int(n_exams * constraint_density)
    constraints = []

    # Non-conflicting pairs
    non_conflict_pairs = []
    for i in range(len(exam_ids)):
        for j in range(i + 1, len(exam_ids)):
            pair = frozenset([exam_ids[i], exam_ids[j]])
            if pair not in conflict_pairs:
                non_conflict_pairs.append((exam_ids[i], exam_ids[j]))

    rng.shuffle(non_conflict_pairs)
    selected_pairs = non_conflict_pairs[:n_constraints]

    for c_idx, (eid_a, eid_b) in enumerate(selected_pairs):
        ctype = "different-period" if rng.random() < 0.5 else "same-period"
        constraints.append(DistributionConstraint(
            id=str(c_idx + 1),
            type=ctype,
            exam_ids=[eid_a, eid_b],
            hard=True,
            weight=0,
        ))

    # ── 6. Build instance ─────────────────────────────────────────────────────
    instance = ExamInstance(
        name=f"synthetic_n{n_exams}_s{seed}",
        periods=periods,
        rooms=rooms,
        exams=exams,
        students=students,
        constraints=constraints,
    )
    instance.build_indexes()
    return instance


# ── XML serialization ─────────────────────────────────────────────────────────

def save_instance_xml(instance: ExamInstance, path: str):
    """
    Write the instance to an XML file compatible with the existing parser.

    The format mirrors the UniTime examination timetabling XML format used
    by parse_instance() in parse_exam.py.
    """
    root = ET.Element("examtt")

    # Periods
    periods_elem = ET.SubElement(root, "periods")
    for p in instance.periods:
        ET.SubElement(periods_elem, "period", {
            "id": p.id,
            "length": str(p.length),
            "day": p.day,
            "time": p.time,
            "penalty": str(p.penalty),
        })

    # Rooms
    rooms_elem = ET.SubElement(root, "rooms")
    for r in instance.rooms:
        room_elem = ET.SubElement(rooms_elem, "room", {
            "id": r.id,
            "size": str(r.size),
            "alt": str(r.alt),
            "coordinates": r.coordinates,
        })
        for period_id, pen in r.period_penalties.items():
            ET.SubElement(room_elem, "period", {"id": period_id, "penalty": str(pen)})

    # Exams
    exams_elem = ET.SubElement(root, "exams")
    for e in instance.exams:
        exam_elem = ET.SubElement(exams_elem, "exam", {
            "id": e.id,
            "length": str(e.length),
            "alt": "true" if e.alt_seating else "false",
            "maxRooms": str(e.max_rooms),
        })
        for pid in e.available_periods:
            ET.SubElement(exam_elem, "period", {"id": pid})
        for rid in e.available_rooms:
            ET.SubElement(exam_elem, "room", {"id": rid})

    # Students
    students_elem = ET.SubElement(root, "students")
    for s in instance.students:
        student_elem = ET.SubElement(students_elem, "student", {"id": s.id})
        for eid in s.exam_ids:
            ET.SubElement(student_elem, "exam", {"id": eid})

    # Constraints
    constraints_elem = ET.SubElement(root, "constraints")
    for c in instance.constraints:
        c_elem = ET.SubElement(constraints_elem, c.type, {
            "id": c.id,
            "hard": "true" if c.hard else "false",
        })
        if not c.hard:
            c_elem.set("weight", str(c.weight))
        for eid in c.exam_ids:
            ET.SubElement(c_elem, "exam", {"id": eid})

    # Pretty-print
    xml_str = ET.tostring(root, encoding="unicode")
    try:
        reparsed = minidom.parseString(xml_str)
        pretty = reparsed.toprettyxml(indent="  ")
        # Remove the extra XML declaration added by minidom
        lines = pretty.split("\n")
        if lines[0].startswith("<?xml"):
            lines = lines[1:]
        pretty = "\n".join(lines)
    except Exception:
        pretty = xml_str

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(pretty)


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42

    print(f"Generating instance: n_exams={n}, seed={seed}")
    instance = generate_instance(n_exams=n, seed=seed)

    print(f"Instance: {instance.name}")
    print(f"  Periods: {len(instance.periods)}")
    print(f"  Rooms:   {len(instance.rooms)}")
    print(f"  Exams:   {len(instance.exams)}")
    print(f"  Students: {len(instance.students)}")
    print(f"  Constraints: {len(instance.constraints)}")

    # Verify feasibility
    sol = greedy_solve(instance)
    print(f"  Greedy feasible: {sol is not None}")
    if sol:
        print(f"  Greedy assigns {len(sol)}/{len(instance.exams)} exams")

    # Save to XML
    out_path = f"/tmp/synthetic_n{n}_s{seed}.xml"
    save_instance_xml(instance, out_path)
    print(f"  Saved to: {out_path}")

    # Reload and verify
    from parse_exam import parse_instance
    reloaded = parse_instance(out_path)
    print(f"  Reloaded: {len(reloaded.exams)} exams, {len(reloaded.students)} students")
    sol2 = greedy_solve(reloaded)
    print(f"  Reloaded feasible: {sol2 is not None}")
