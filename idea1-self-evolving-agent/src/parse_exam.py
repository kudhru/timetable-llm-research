"""
Parser for UniTime examination timetabling XML format.
Handles the Purdue exam dataset format from https://www.unitime.org/exam_datasets.php
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set


@dataclass
class Period:
    id: str
    length: int       # minutes
    day: str
    time: str
    penalty: int      # penalty for assigning an exam here


@dataclass
class Room:
    id: str
    size: int
    alt: int          # alternate (alt-seating) capacity
    coordinates: str  # "lat,lon" string
    # period-specific penalties: {period_id: penalty}
    period_penalties: Dict[str, int] = field(default_factory=dict)


@dataclass
class Exam:
    id: str
    length: int
    alt_seating: bool
    max_rooms: int
    available_periods: List[str]  # period ids
    available_rooms: List[str]    # room ids
    student_count: int = 0


@dataclass
class Student:
    id: str
    exam_ids: List[str]
    unavailable_periods: List[str] = field(default_factory=list)


@dataclass
class DistributionConstraint:
    id: str
    type: str         # same-period, different-period, precedence, same-room, etc.
    exam_ids: List[str]
    hard: bool = True
    weight: int = 0


@dataclass
class ExamInstance:
    name: str
    periods: List[Period]
    rooms: List[Room]
    exams: List[Exam]
    students: List[Student]
    constraints: List[DistributionConstraint]
    # derived lookup dicts (built after parse)
    period_map: Dict[str, Period] = field(default_factory=dict)
    room_map: Dict[str, Room] = field(default_factory=dict)
    exam_map: Dict[str, Exam] = field(default_factory=dict)
    student_map: Dict[str, Student] = field(default_factory=dict)
    # exam -> set of student ids
    exam_students: Dict[str, Set[str]] = field(default_factory=dict)

    def build_indexes(self):
        """Build lookup maps and exam_students index after parsing."""
        self.period_map = {p.id: p for p in self.periods}
        self.room_map = {r.id: r for r in self.rooms}
        self.exam_map = {e.id: e for e in self.exams}
        self.student_map = {s.id: s for s in self.students}

        self.exam_students = {e.id: set() for e in self.exams}
        for student in self.students:
            for eid in student.exam_ids:
                if eid in self.exam_students:
                    self.exam_students[eid].add(student.id)

        # Update student counts on Exam objects
        for exam in self.exams:
            exam.student_count = len(self.exam_students.get(exam.id, set()))


def parse_instance(xml_path: str, name: str = None) -> ExamInstance:
    """
    Parse a UniTime exam timetabling XML file.

    The format (from unitime.org) has:
      <examtt>
        <parameters> ... </parameters>
        <periods>
          <period id="1" length="120" day="Mon 12/15" time="8:00a - 10:00a" penalty="0"/>
        </periods>
        <rooms>
          <room id="1" size="14" alt="7" coordinates="40.4,-86.9">
            <period id="1" penalty="4"/>   ← per-room period penalties (if any)
          </room>
        </rooms>
        <exams>
          <exam id="1" length="120" alt="true" printOffset="0" average="14">
            <period id="1"/>               ← available periods
            <room id="135" maxPenalty="3"/> ← available rooms
          </exam>
        </exams>
        <students>
          <student id="1">
            <exam id="302"/>              ← enrolled exams
          </student>
        </students>
        <constraints>
          <same-period id="1">
            <exam id="314"/>
            <exam id="879"/>
          </same-period>
        </constraints>
      </examtt>
    """
    if name is None:
        import os
        name = os.path.splitext(os.path.basename(xml_path))[0]

    tree = ET.parse(xml_path)
    root = tree.getroot()

    # --- Parse periods ---
    periods = []
    periods_elem = root.find('periods')
    if periods_elem is not None:
        for p in periods_elem:
            periods.append(Period(
                id=p.get('id'),
                length=int(p.get('length', 120)),
                day=p.get('day', ''),
                time=p.get('time', ''),
                penalty=int(p.get('penalty', 0)),
            ))

    # --- Parse rooms ---
    rooms = []
    rooms_elem = root.find('rooms')
    if rooms_elem is not None:
        for r in rooms_elem:
            period_penalties = {}
            for pp in r:
                if pp.tag == 'period':
                    period_penalties[pp.get('id')] = int(pp.get('penalty', 0))
            rooms.append(Room(
                id=r.get('id'),
                size=int(r.get('size', 0)),
                alt=int(r.get('alt', 0)),
                coordinates=r.get('coordinates', ''),
                period_penalties=period_penalties,
            ))

    # --- Parse exams ---
    exams = []
    exams_elem = root.find('exams')
    if exams_elem is not None:
        for e in exams_elem:
            avail_periods = []
            avail_rooms = []
            for child in e:
                if child.tag == 'period':
                    avail_periods.append(child.get('id'))
                elif child.tag == 'room':
                    avail_rooms.append(child.get('id'))
            alt_str = e.get('alt', 'false').lower()
            exams.append(Exam(
                id=e.get('id'),
                length=int(e.get('length', 120)),
                alt_seating=(alt_str == 'true'),
                max_rooms=int(e.get('maxRooms', 4)),
                available_periods=avail_periods,
                available_rooms=avail_rooms,
            ))

    # --- Parse students ---
    students = []
    students_elem = root.find('students')
    if students_elem is not None:
        for s in students_elem:
            exam_ids = []
            unavail_periods = []
            for child in s:
                if child.tag == 'exam':
                    exam_ids.append(child.get('id'))
                elif child.tag == 'period':
                    unavail_periods.append(child.get('id'))
            students.append(Student(
                id=s.get('id'),
                exam_ids=exam_ids,
                unavailable_periods=unavail_periods,
            ))

    # --- Parse constraints ---
    constraints = []
    constraints_elem = root.find('constraints')
    if constraints_elem is not None:
        for c in constraints_elem:
            exam_ids = [child.get('id') for child in c if child.tag == 'exam']
            # Determine hardness from attribute or default
            hard_str = c.get('hard', 'true').lower()
            hard = (hard_str != 'false')
            weight = int(c.get('weight', 0)) if not hard else 0
            constraints.append(DistributionConstraint(
                id=c.get('id', ''),
                type=c.tag,
                exam_ids=exam_ids,
                hard=hard,
                weight=weight,
            ))

    # Also check <distributionConstraints> tag (alternate format)
    dc_elem = root.find('distributionConstraints')
    if dc_elem is not None:
        for c in dc_elem:
            exam_ids = [child.get('id') for child in c if child.tag == 'exam']
            hard_str = c.get('hard', 'true').lower()
            hard = (hard_str != 'false')
            weight = int(c.get('weight', 0)) if not hard else 0
            constraints.append(DistributionConstraint(
                id=c.get('id', ''),
                type=c.get('type', c.tag),
                exam_ids=exam_ids,
                hard=hard,
                weight=weight,
            ))

    instance = ExamInstance(
        name=name,
        periods=periods,
        rooms=rooms,
        exams=exams,
        students=students,
        constraints=constraints,
    )
    instance.build_indexes()
    return instance


def print_summary(instance: ExamInstance):
    """Print a human-readable summary of the instance."""
    print(f"Instance: {instance.name}")
    print(f"  Periods: {len(instance.periods)}")
    print(f"  Rooms:   {len(instance.rooms)} (sizes {min(r.size for r in instance.rooms)}–{max(r.size for r in instance.rooms)})")
    print(f"  Exams:   {len(instance.exams)}")
    print(f"  Students: {len(instance.students)}")
    print(f"  Distribution constraints: {len(instance.constraints)}")

    total_enrollments = sum(len(s.exam_ids) for s in instance.students)
    print(f"  Total enrollments: {total_enrollments}")

    if instance.exams:
        sizes = [e.student_count for e in instance.exams]
        print(f"  Exam sizes: min={min(sizes)}, max={max(sizes)}, mean={sum(sizes)/len(sizes):.1f}")

    period_penalties = [p.penalty for p in instance.periods]
    print(f"  Period penalties: {set(period_penalties)}")

    if instance.constraints:
        from collections import Counter
        ctypes = Counter(c.type for c in instance.constraints)
        print(f"  Constraint types: {dict(ctypes)}")


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "data/purdue_exam/pu-exam-fal08.xml"
    print(f"Parsing {path} ...")
    instance = parse_instance(path)
    print_summary(instance)
