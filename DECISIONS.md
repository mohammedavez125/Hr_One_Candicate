# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** I created a unique index on `employees(emp_code)` to ensure identity integrity and a unique index on `attendance_logs(emp_code, date)` to prevent multiple records for the same day (R1). I added a compound index on `attendance_logs(date, emp_code)` for sorted pagination and an index on `employees(department)` for analytics filtering. I rejected indexing `late_minutes` alone as it's always queried with `date` or `department`.

2. **Punch-in race.** Both requests will calculate the same attendance date. The first `db.attendance_logs.insert_one` call succeeds. The second call triggers a `DuplicateKeyError` due to the unique index on `(emp_code, date)`, which is caught and returned as a 409 Conflict. This ensures atomicity without manual locks.

3. **Ties.** The leaderboard uses MongoDB's `$rank`. If two employees tie at rank 10, both are included in the output because the filter is `rank <= limit`. The next rank is skipped (e.g., 10, 10, 12). This follows standard competition ranking.

4. **Headcount.** The department summary starts with the `employees` collection (filtered by `joined_on` and `department`) and uses a `$lookup` to join attendance logs. This "left join" ensures every eligible employee is represented, even if their `logs` array is empty.

5. **Scaling 100x.** I would implement pre-aggregated monthly buckets for analytics to avoid scanning 10M+ records. For the daily trend, I'd use MongoDB's Time Series collections for attendance logs to optimize storage and query speed.

6. **AI Usage & Tooling.** I utilized an AI-assisted development workflow by first drafting a structured requirements and strategy prompt using ChatGPT, and then leveraging Junie and Codex for iterative code generation, debugging, aggregation pipeline construction, and systematic solution verification.
