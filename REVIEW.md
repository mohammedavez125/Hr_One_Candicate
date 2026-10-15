# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health` | Doesn't check DB connection. | Returns 200 even if DB is down. | Added `client.admin.command("ping")`. |
| 2 | `create_employee` | Uses local time, no uniqueness enforcement. | `created_at` in local TZ; duplicate `emp_code` might pass check. | Use `datetime.now(timezone.utc)`, added unique index and try-except. |
| 3 | `list_employees` | Incorrect pagination and total. | `skip` is wrong, `total` doesn't filter by department. | Fixed `skip` calculation, filtered `total`, added sort. |
| 4 | `punch_in` | Lacks R1/R2, local time, no atomicity. | Incorrect `date` for overnight, naive datetimes, duplicate punch-in allowed. | Implemented R1/R2, use UTC, added unique index and atomic check. |
| 5 | `list_attendance` | Loads everything into memory. | Performance issues with 100k records. | Moved sorting and pagination to MongoDB query. |
| 6 | Helpers | Lacks rounding R8 and whole-second truncation. | Inaccurate `work_hours`, milliseconds stored. | Added `round_half_up` and truncated instants. |
| 7 | `department_trend` | Used `$documents` with collection-level command instead of database-level. | 500 error: `'$documents' can only be run with {aggregate: 1}`. | Changed aggregation command to `db.command("aggregate", 1, ...)` and formatted dates as `YYYY-MM-DD`. |

Also note anything you looked at and decided was **not** a defect, and why.
- **FastAPI / Pydantic schema coercion:** Checked if custom ISO 8601 parsing was necessary for query strings like `from` / `to` or `month`; standard string validations and standard `datetime.strptime` patterns matched the OpenAPI specifications appropriately without requiring third-party libraries.

## AI Assistance & Workflow
To efficiently solve, review, and refactor the code:
1. **Prompt Drafting:** I drafted a comprehensive context and specification prompt using ChatGPT, clarifying edge cases, MongoDB aggregation requirements, and API constraints.
2. **Solving & Refactoring:** I used Junie and Codex to generate the initial fixes, build MongoDB aggregation pipelines (such as `$setWindowFields`, `$documents`, `$lookup`), trace errors (including the `$documents` aggregate namespace fix), and verify compliance with business rules R1–R10.
