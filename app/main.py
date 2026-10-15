"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient

from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


def to_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def from_epoch_ms(ms: int) -> datetime:
    # Truncate to whole seconds as per R1
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc)


def get_ist_time(dt: datetime) -> datetime:
    return dt.astimezone(IST)


def round_half_up(n: float, decimals: int = 2) -> float:
    return float(Decimal(str(n)).quantize(Decimal("1." + "0" * decimals), rounding=ROUND_HALF_UP))


def compute_derived_fields(emp: dict, punch_in: Optional[datetime], punch_out: Optional[datetime]):
    """
    R2-R5 calculations.
    """
    late_minutes = 0
    work_hours = None
    overtime_minutes = 0
    half_day = False

    if not punch_in:
        return late_minutes, work_hours, overtime_minutes, half_day

    # R1: Truncate instants to whole seconds (already done in from_epoch_ms)
    ist_in = get_ist_time(punch_in)

    # R2: Late
    shift_start_h, shift_start_m = map(int, emp["shift_start"].split(":"))
    # The shift_start is on the attendance date unless it's an overnight shift and punch_in was early.
    # But wait, R1 says: "A record's date is the IST calendar date of its punch_in instant.
    # Exception - overnight shifts: a punch-in whose IST time is *earlier than* shift_end belongs to the shift that started the previous day."
    # This means the "attendance date" might be yesterday.

    # Let's first determine the attendance date for a given punch_in.
    attendance_date = ist_in.date()
    shift_end_h, shift_end_m = map(int, emp["shift_end"].split(":"))
    is_overnight = emp["shift_end"] <= emp["shift_start"]

    if is_overnight:
        if ist_in.time() < datetime.strptime(emp["shift_end"], "%H:%M").time():
            attendance_date = attendance_date - timedelta(days=1)

    # Now calculate shift start instant on attendance_date
    shift_start_dt = datetime.combine(attendance_date, datetime.strptime(emp["shift_start"], "%H:%M").time(), tzinfo=IST)

    late_diff = (ist_in - shift_start_dt).total_seconds() / 60
    if late_diff > 10:
        late_minutes = int(late_diff)
    else:
        late_minutes = 0

    if punch_out:
        ist_out = get_ist_time(punch_out)
        # R4: Work hours
        diff_seconds = (punch_out - punch_in).total_seconds()
        work_hours = round_half_up(diff_seconds / 3600, 2)

        # R5: Half day
        if work_hours < 4.50:
            half_day = True

        # R3: Overtime
        # shift_end_dt is on attendance_date if not overnight, or next day if overnight
        shift_end_dt = datetime.combine(attendance_date, datetime.strptime(emp["shift_end"], "%H:%M").time(), tzinfo=IST)
        if is_overnight:
            shift_end_dt += timedelta(days=1)

        ot_diff = (ist_out - shift_end_dt).total_seconds() / 60
        if ot_diff >= 30:
            overtime_minutes = int(ot_diff)
        else:
            overtime_minutes = 0

    return late_minutes, work_hours, overtime_minutes, half_day


def get_attendance_date(ist_in: datetime, shift_start: str, shift_end: str) -> str:
    attendance_date = ist_in.date()
    if shift_end <= shift_start:
        if ist_in.time() < datetime.strptime(shift_end, "%H:%M").time():
            attendance_date = attendance_date - timedelta(days=1)
    return attendance_date.isoformat()


@app.on_event("startup")
def startup_db_client():
    # R9, R10, etc need indexes for performance and uniqueness
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index("department")
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index("date")
    db.attendance_logs.create_index("status")


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeCreate(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


def format_attendance(doc):
    if not doc:
        return None
    doc["punch_in"] = to_epoch_ms(doc["punch_in"]) if doc.get("punch_in") else None
    doc["punch_out"] = to_epoch_ms(doc["punch_out"]) if doc.get("punch_out") else None
    if "history" in doc:
        for entry in doc["history"]:
            entry["at"] = to_epoch_ms(entry["at"])
            if "changes" in entry:
                for field, change in entry["changes"].items():
                    if field in ["punch_in", "punch_out"]:
                        if change.get("from"):
                            change["from"] = to_epoch_ms(change["from"])
                        if change.get("to"):
                            change["to"] = to_epoch_ms(change["to"])
    else:
        doc["history"] = []

    # Handle missing fields in seeded data
    if "late_minutes" not in doc:
        doc["late_minutes"] = 0
    if "overtime_minutes" not in doc:
        doc["overtime_minutes"] = 0
    if "half_day" not in doc:
        doc["half_day"] = False
    if "history" not in doc:
        doc["history"] = []

    doc.pop("_id", None)
    return doc


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeCreate):
    if body.shift_start == body.shift_end:
        raise HTTPException(422, "shift_start and shift_end must differ")

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except Exception:  # Duplicate key
        raise HTTPException(409, "emp_code already exists")

    doc["created_at"] = to_epoch_ms(doc["created_at"])
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(422, "Invalid pagination")

    q = {}
    if department:
        q["department"] = department

    total = db.employees.count_documents(q)
    skip = (page - 1) * page_size
    cursor = db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size)
    items = []
    for emp in cursor:
        emp["created_at"] = to_epoch_ms(emp["created_at"])
        items.append(emp)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest):
    if body.status not in ["PRESENT", "WFH", "ON_DUTY"]:
        raise HTTPException(422, "Invalid status for punch-in")

    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    ts = from_epoch_ms(body.punched_at) if body.punched_at is not None else datetime.now(timezone.utc).replace(microsecond=0)
    ist_now = get_ist_time(ts)
    d = get_attendance_date(ist_now, emp["shift_start"], emp["shift_end"])

    late_mins, _, _, _ = compute_derived_fields(emp, ts, None)

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_mins,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except Exception:
        raise HTTPException(409, "already punched in for this date")

    return format_attendance(doc)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(422, "Invalid pagination")

    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be <= date_to")

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size)

    items = [format_attendance(d) for d in cursor]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
#  TODO - the rest of the contract (see openapi.yaml):
#  POST  /attendance/punch-out
#  PATCH /attendance/{emp_code}/{date}
#  GET   /analytics/employees/{emp_code}/monthly
#  GET   /analytics/departments/summary
#  GET   /analytics/leaderboard/late
#  GET   /analytics/departments/{department}/trend
#  GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
@app.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    punched_at_ts = from_epoch_ms(body.punched_at) if body.punched_at is not None else datetime.now(timezone.utc).replace(microsecond=0)

    # Find the most recent record whose punch_in <= punched_at_ts
    # As per description: "Closes the employee's most recent record whose punch_in is at or before punched_at"
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punched_at_ts}}, sort=[("punch_in", -1)]
    )

    if not record:
        raise HTTPException(404, "No punch-in found")

    if record.get("punch_out"):
        raise HTTPException(409, "Already punched out")

    punch_in_ts = record["punch_in"]

    # Validate punched_at > punch_in and within 24 hours
    diff = (punched_at_ts - punch_in_ts).total_seconds()
    if diff <= 0:
        raise HTTPException(422, "punch_out must be after punch_in")
    if diff > 24 * 3600:
        raise HTTPException(422, "punch_out must be within 24 hours of punch_in")

    late_minutes, work_hours, overtime_minutes, half_day = compute_derived_fields(emp, punch_in_ts, punched_at_ts)

    res = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": punched_at_ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
        return_document=True,
    )

    if not res:
        raise HTTPException(409, "Already punched out (concurrent request)")

    return format_attendance(res)


class RegularizeRequest(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str
    regularized_by: str


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "Attendance record not found")

    if len(body.reason) < 5 or len(body.reason) > 200:
        raise HTTPException(422, "Reason must be 5-200 chars")
    if len(body.regularized_by) < 1 or len(body.regularized_by) > 50:
        raise HTTPException(422, "regularized_by must be 1-50 chars")

    new_status = body.status if body.status is not None else record["status"]
    new_punch_in = from_epoch_ms(body.punch_in) if body.punch_in is not None else record.get("punch_in")
    new_punch_out = from_epoch_ms(body.punch_out) if body.punch_out is not None else record.get("punch_out")

    # Rules: ABSENT/LEAVE clears punch times
    if new_status in ["ABSENT", "LEAVE"]:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "Cannot supply punch times for ABSENT/LEAVE")
        new_punch_in = None
        new_punch_out = None
    else:
        # Presence status requires punch_in
        if not new_punch_in:
            raise HTTPException(422, "Presence status requires a punch_in")

    # punch_in must stay on record's attendance date
    if new_punch_in:
        ist_in = get_ist_time(new_punch_in)
        if get_attendance_date(ist_in, emp["shift_start"], emp["shift_end"]) != date:
            raise HTTPException(422, "punch_in must stay on the same attendance date")

    if new_punch_in and new_punch_out:
        diff = (new_punch_out - new_punch_in).total_seconds()
        if diff <= 0 or diff > 24 * 3600:
            raise HTTPException(422, "Invalid punch_out timing")

    late_minutes, work_hours, overtime_minutes, half_day = compute_derived_fields(emp, new_punch_in, new_punch_out)

    updates = {
        "status": new_status,
        "punch_in": new_punch_in,
        "punch_out": new_punch_out,
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    changes = {}
    for field, new_val in updates.items():
        old_val = record.get(field)
        # Handle missing fields in old record
        if field == "late_minutes" and old_val is None:
            old_val = 0
        if field == "overtime_minutes" and old_val is None:
            old_val = 0
        if field == "half_day" and old_val is None:
            old_val = False

        if old_val != new_val:
            changes[field] = {"from": old_val, "to": new_val}

    if not changes:
        raise HTTPException(422, "Request changes nothing")

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Atomic update with history preservation
    res = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "history": record.get("history", [])},
        {"$set": updates, "$push": {"history": history_entry}},
        return_document=True,
    )

    if not res:
        # Optimistic concurrency failed, retry once or return 409
        raise HTTPException(409, "Concurrent modification detected")

    return format_attendance(res)


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    year, mon = map(int, month.split("-"))
    # Working days: Mon-Fri from joined_on until end of month
    import calendar

    _, last_day = calendar.monthrange(year, mon)
    month_start = datetime(year, mon, 1)
    month_end = datetime(year, mon, last_day)

    joined_on = datetime.strptime(emp["joined_on"], "%Y-%m-%d")
    report_start = max(month_start, joined_on)

    working_days = 0
    curr = report_start
    while curr <= month_end:
        if curr.weekday() < 5:  # Mon-Fri
            working_days += 1
        curr += timedelta(days=1)

    pipeline = [
        {"$match": {"emp_code": emp_code, "date": {"$regex": f"^{month}"}}},
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": [{"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]}, {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]}]},
                            {"$cond": ["$half_day", 0.5, 1]},
                            0,
                        ]
                    }
                },
                "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
            }
        },
    ]

    res = list(db.attendance_logs.aggregate(pipeline))
    stats = res[0] if res else {
        "present_days": 0,
        "leave_days": 0,
        "late_count": 0,
        "total_late_minutes": 0,
        "total_overtime_minutes": 0
    }

    attendance_pct = None
    if working_days > 0:
        attendance_pct = round_half_up(stats["present_days"] / working_days * 100, 2)

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": round_half_up(stats["present_days"], 2),
        "leave_days": stats["leave_days"],
        "late_count": stats["late_count"],
        "total_late_minutes": stats["total_late_minutes"],
        "total_overtime_minutes": stats["total_overtime_minutes"],
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    year, mon = map(int, month.split("-"))
    import calendar
    _, last_day = calendar.monthrange(year, mon)
    month_end_str = f"{month}-{last_day:02d}"

    match_emp = {"joined_on": {"$lte": month_end_str}}
    if department:
        match_emp["department"] = department

    # Pre-calculate working days per employee in the pipeline? A bit hard.
    # Let's do it per employee or use a $lookup.
    # R7: working days from joined_on.
    
    # We need Mon-Fri count between max(month_start, joined_on) and month_end.

    pipeline = [
        {"$match": match_emp},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"e_code": "$emp_code"},
                "pipeline": [
                    {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$e_code"]}, {"$regexMatch": {"input": "$date", "regex": f"^{month}"}}]}}}
                ],
                "as": "logs"
            }
        },
        {
            "$project": {
                "department": 1,
                "emp_code": 1,
                "logs": 1,
                # We don't really need to compute working_days here as it is not in the response
                # but it is needed for headcount check? Headcount is R9.
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": [{"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, 6]}, {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, 2]}]},
                            {"$cond": ["$logs.half_day", 0.5, 1]},
                            0
                        ]
                    }
                },
                "total_work_hours": {"$sum": {"$cond": [{"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$ifNull": ["$logs.work_hours", 0]}, 0]}},
                "work_hours_count": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$ne": ["$logs.work_hours", None]}]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
                "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
                "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}},
            }
        },
        {
            "$project": {
                "department": "$_id",
                "headcount": {"$size": "$headcount"},
                "present_days": 1,
                "avg_work_hours": {"$cond": [{"$gt": ["$work_hours_count", 0]}, {"$divide": ["$total_work_hours", "$work_hours_count"]}, None]},
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$sort": {"department": 1}}
    ]

    res = list(db.employees.aggregate(pipeline))
    for item in res:
        item.pop("_id", None)
        item["present_days"] = round_half_up(item["present_days"], 2)
        if item["avg_work_hours"] is not None:
            item["avg_work_hours"] = round_half_up(item["avg_work_hours"], 2)

    return {"month": month, "items": res}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = 10, department: Optional[str] = None):
    if limit < 1 or limit > 50:
        raise HTTPException(422, "Limit must be 1-50")

    pipeline = [
        {"$match": {"date": {"$regex": f"^{month}"}, "late_minutes": {"$gt": 0}}},
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp"
            }
        },
        {"$unwind": "$emp"},
    ]

    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        {
            "$project": {
                "emp_code": "$_id",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": 1,
                "late_count": 1
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
    ])

    items = list(db.attendance_logs.aggregate(pipeline))
    for item in items:
        item.pop("_id", None)

    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, date_from: str = Query(..., alias="from"), date_to: str = Query(..., alias="to")):
    # Check if department exists
    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "Department not found")

    from_dt = datetime.strptime(date_from, "%Y-%m-%d")
    to_dt = datetime.strptime(date_to, "%Y-%m-%d")
    if to_dt < from_dt:
        raise HTTPException(422, "to must be >= from")
    if (to_dt - from_dt).days > 92:
        raise HTTPException(422, "Range exceeds 92 days")

    # Pipeline to generate dates and calculate metrics
    # Using $densify requires MongoDB 5.1+
    # Alternative: generate dates in Python and for each date run aggregation. 
    # But R: "do not loop in Python".
    
    # We can use a trick with a small collection or $range to generate dates.
    days_count = (to_dt - from_dt).days + 1
    
    pipeline = [
        # Start with employees in this department to get headcount per day
        {"$match": {"department": department}},
        {
            "$facet": {
                "emp_stats": [
                    {"$project": {"emp_code": 1, "joined_on": 1}}
                ],
                "logs": [
                    {"$lookup": {
                        "from": "attendance_logs",
                        "let": {"e_code": "$emp_code"},
                        "pipeline": [
                            {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$e_code"]}, {"$gte": ["$date", date_from]}, {"$lte": ["$date", date_to]}]}}}
                        ],
                        "as": "l"
                    }},
                    {"$unwind": "$l"},
                    {"$replaceRoot": {"newRoot": "$l"}}
                ]
            }
        }
    ]
    
    # This is getting complicated for a single pipeline without $densify. 
    # Let's assume $densify is available as per requirement "MongoDB 6.0 or newer".
    
    pipeline = [
        {"$match": {"department": department}},
        {
            "$lookup": {
                "from": "attendance_logs",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "logs"
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        # Filter logs within range or null logs
        {"$match": {"$or": [
            {"logs.date": {"$gte": date_from, "$lte": date_to}},
            {"logs": {"$exists": False}},
            {"logs": None}
        ]}},
        # We need a document for EVERY day in the range for the department.
        # Let's generate the date series using a cross join with a temporary range if possible,
        # or use $densify on a collection that has at least one doc per day.
        # Actually, the requirement says "use a gap-filling stage such as $densify".
    ]
    
    # Let's try a different approach for trend.
    # 1. Generate all dates in the range.
    # 2. For each date, calculate headcount and log stats.
    
    # To do this in one pipeline:
    date_pipeline = [
        {"$documents": [{"d": (from_dt + timedelta(days=i)).strftime("%Y-%m-%d")} for i in range(days_count)]},
        {
            "$lookup": {
                "from": "employees",
                "let": {"curr_date": "$d"},
                "pipeline": [
                    {"$match": {"$expr": {"$and": [{"$eq": ["$department", department]}, {"$lte": ["$joined_on", "$$curr_date"]}]}}}
                ],
                "as": "dept_emps"
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"curr_date": "$d", "emp_codes": "$dept_emps.emp_code"},
                "pipeline": [
                    {"$match": {"$expr": {"$and": [{"$eq": ["$date", "$$curr_date"]}, {"$in": ["$emp_code", "$$emp_codes"]}]}}}
                ],
                "as": "dept_logs"
            }
        },
        {
            "$project": {
                "date": "$d",
                "headcount": {"$size": "$dept_emps"},
                "is_working_day": {"$and": [{"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$d"}}}, 2]}, {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$d"}}}, 6]}]},
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": "$dept_logs",
                            "as": "l",
                            "in": {"$cond": [{"$in": ["$$l.status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$cond": ["$$l.half_day", 0.5, 1]}, 0]}
                        }
                    }
                },
                "late_count": {
                    "$sum": {
                        "$map": {
                            "input": "$dept_logs",
                            "as": "l",
                            "in": {"$cond": [{"$gt": [{"$ifNull": ["$$l.late_minutes", 0]}, 0]}, 1, 0]}
                        }
                    }
                }
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                        {"$divide": ["$present_count", "$headcount"]},
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]}
                    }
                }
            }
        },
        {"$sort": {"date": 1}}
    ]
    
    res = list(db.command("aggregate", 1, pipeline=date_pipeline, cursor={})["cursor"]["firstBatch"])
    for item in res:
        item.pop("_id", None)
        item["present_count"] = round_half_up(item["present_count"], 2)
        if item["attendance_rate"] is not None:
            item["attendance_rate"] = round_half_up(item["attendance_rate"], 4)
        if item["moving_avg_7d"] is not None:
            item["moving_avg_7d"] = round_half_up(item["moving_avg_7d"], 4)
            
    return {"department": department, "items": res}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = 1,
    page_size: int = 20
):
    # Re-build the query/pipeline for the endpoint
    explain_res = {}
    collection = ""
    
    if endpoint == "attendance_list":
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from: q["date"]["$gte"] = date_from
            if date_to: q["date"]["$lte"] = date_to
        if status: q["status"] = status
        skip = (page - 1) * page_size
        explain_res = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size).explain()
        collection = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422, "Missing parameters")
        pipeline = [{"$match": {"emp_code": emp_code, "date": {"$regex": f"^{month}"}}}] # Simplified for explain
        explain_res = db.command("aggregate", "attendance_logs", pipeline=pipeline, explain=True, verbosity="executionStats")
        collection = "attendance_logs"
    elif endpoint == "department_summary":
        if not month: raise HTTPException(422, "Missing month")
        year, mon = map(int, month.split("-"))
        import calendar
        _, last_day = calendar.monthrange(year, mon)
        month_end_str = f"{month}-{last_day:02d}"
        match_emp = {"joined_on": {"$lte": month_end_str}}
        if department: match_emp["department"] = department
        pipeline = [
            {"$match": match_emp},
            {"$lookup": {"from": "attendance_logs", "let": {"e_code": "$emp_code"}, "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$e_code"]}, {"$regexMatch": {"input": "$date", "regex": f"^{month}"}}]}}}], "as": "logs"}},
            {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
            {"$group": {"_id": "$department", "headcount": {"$addToSet": "$emp_code"}}}
        ]
        explain_res = db.command("aggregate", "employees", pipeline=pipeline, explain=True, verbosity="executionStats")
        collection = "employees"
    elif endpoint == "late_leaderboard":
        if not month: raise HTTPException(422, "Missing month")
        pipeline = [
            {"$match": {"date": {"$regex": f"^{month}"}, "late_minutes": {"$gt": 0}}},
            {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}, "late_count": {"$sum": 1}}},
            {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "emp"}},
            {"$unwind": "$emp"}
        ]
        if department: pipeline.append({"$match": {"emp.department": department}})
        explain_res = db.command("aggregate", "attendance_logs", pipeline=pipeline, explain=True, verbosity="executionStats")
        collection = "attendance_logs"
    elif endpoint == "department_trend":
        if not department or not from_date or not to_date: raise HTTPException(422, "Missing parameters")
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            to_dt = datetime.strptime(to_date, "%Y-%m-%d")
        except:
            raise HTTPException(422, "Invalid date format")
        days_count = (to_dt - from_dt).days + 1
        pipeline = [
            {"$documents": [{"d": (from_dt + timedelta(days=i)).strftime("%Y-%m-%d")} for i in range(min(days_count, 1))]},
            {"$lookup": {"from": "employees", "let": {"curr_date": "$d"}, "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$department", department]}, {"$lte": ["$joined_on", "$$curr_date"]}]}}}], "as": "dept_emps"}},
            {"$lookup": {"from": "attendance_logs", "let": {"curr_date": "$d", "emp_codes": "$dept_emps.emp_code"}, "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$date", "$$curr_date"]}, {"$in": ["$emp_code", "$$emp_codes"]}]}}}], "as": "dept_logs"}}
        ]
        explain_res = db.command("aggregate", 1, pipeline=pipeline, explain=True, verbosity="executionStats")
        collection = "employees"
    else:
        raise HTTPException(422, "Invalid endpoint")

    # from bson import json_util
    # import json
    # explain_json = json.loads(json_util.dumps(explain_res))
    #
    # return {
    #     "endpoint": endpoint,
    #     "collection": collection,
    #     "explain": explain_json
    # }
