"""Analysis dashboard queries: group/document drill-down, progress, errors."""


class DashboardMixin:
    """Dashboard views for analysis pipeline monitoring."""

    def get_group_dashboard(self) -> list[dict]:
        """Return groups with their gate states for the analysis dashboard.

        Returns one row per group with permitted state per step and member count.
        """
        steps = self.get_analysis_steps()
        step_names = [s["name"] for s in steps]

        groups = self.get_lesson_groups()

        if not groups:
            return []

        group_ids = [g["id"] for g in groups]

        # Bulk fetch all group gates
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT group_id, step_name, permitted
                FROM group_analysis_gate
                WHERE group_id = ANY(%s)
            """, (group_ids,))
            gate_rows = cursor.fetchall()

        gate_lookup = {}
        for row in gate_rows:
            gate_lookup[(row["group_id"], row["step_name"])] = row["permitted"]

        for group in groups:
            gates = {}
            for sn in step_names:
                gates[sn] = gate_lookup.get((group["id"], sn), False)
            group["gates"] = gates

        return groups

    def get_document_dashboard(self, group_id: int) -> list[dict]:
        """Return documents in a group with their analysis states for drill-down view.

        Returns one row per document with completion state per step.
        """
        steps = self.get_analysis_steps()
        step_names = [s["name"] for s in steps]
        step_versions = {s["name"]: s["current_version"] for s in steps}

        members = self.get_group_members(group_id)
        if not members:
            return []

        doc_ids = [str(m["document_id"]) for m in members]

        # Bulk fetch all states for these documents
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT das.document_id::text, das.step_name,
                       das.completed_version, das.stale, das.error, das.completed_at
                FROM document_analysis_state das
                WHERE das.document_id = ANY(%s::uuid[])
            """, (doc_ids,))
            state_rows = cursor.fetchall()

        state_lookup = {}
        for row in state_rows:
            state_lookup[(row["document_id"], row["step_name"])] = dict(row)

        # Check which steps are permitted for this group
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT step_name, permitted FROM group_analysis_gate
                WHERE group_id = %s AND permitted = TRUE
            """, (group_id,))
            permitted_steps = {row["step_name"] for row in cursor.fetchall()}

        for doc in members:
            doc_id = str(doc["document_id"])
            gates = {}
            for sn in step_names:
                s = state_lookup.get((doc_id, sn))
                permitted = sn in permitted_steps
                if s:
                    cv = s.get("completed_version")
                    sv = step_versions[sn]
                    needs_work = cv is None or cv < sv or s.get("stale", False)
                    if s.get("error"):
                        state = "error"
                    elif not needs_work:
                        state = "current"
                    elif permitted and needs_work:
                        state = "stale"
                    else:
                        state = "inactive"
                    gates[sn] = {
                        "state": state,
                        "permitted": permitted,
                        "completed_version": cv,
                        "current_version": sv,
                        "stale": s.get("stale", False),
                        "error": s.get("error"),
                        "completed_at": str(s.get("completed_at") or ""),
                    }
                else:
                    gates[sn] = {
                        "state": "inactive",
                        "permitted": permitted,
                        "completed_version": None,
                        "current_version": step_versions[sn],
                        "stale": False,
                        "error": None,
                        "completed_at": "",
                    }
            doc["gates"] = gates

        return members

    def clear_all_errors(self) -> int:
        """Clear all errors in document_analysis_state. Returns count cleared."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE document_analysis_state
                SET error = NULL, updated_at = NOW()
                WHERE error IS NOT NULL
            """)
            return cursor.rowcount

    def get_pipeline_progress(self) -> list[dict]:
        """Return per-group, per-step completion counts for permitted steps.

        - total: number of docs in the group (same across all steps in a row)
        - completed: docs where this step AND all its deps are completed at current version
        - stale/errors: as before
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT lg.id as group_id, lg.name as group_name,
                       gag.step_name,
                       s.current_version,
                       COUNT(lgm.document_id) as total,
                       COUNT(CASE WHEN das.completed_version = s.current_version
                                   AND NOT COALESCE(das.stale, FALSE)
                                   AND das.error IS NULL
                                   AND NOT EXISTS (
                                       SELECT 1 FROM unnest(s.depends_on) dep(name)
                                       WHERE NOT EXISTS (
                                           SELECT 1 FROM document_analysis_state das2
                                           WHERE das2.document_id = lgm.document_id
                                             AND das2.step_name = dep.name
                                             AND das2.completed_version IS NOT NULL
                                       )
                                   )
                                   THEN 1 END) as completed,
                       COUNT(CASE WHEN das.stale = TRUE AND das.error IS NULL THEN 1 END) as stale,
                       COUNT(CASE WHEN das.error IS NOT NULL THEN 1 END) as errors
                FROM lesson_group lg
                JOIN group_analysis_gate gag ON gag.group_id = lg.id AND gag.permitted = TRUE
                JOIN analysis_step s ON s.name = gag.step_name
                JOIN lesson_group_member lgm ON lgm.group_id = lg.id
                LEFT JOIN document_analysis_state das
                    ON das.document_id = lgm.document_id AND das.step_name = gag.step_name
                GROUP BY lg.id, lg.name, gag.step_name, s.current_version
                ORDER BY lg.name, gag.step_name
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_completion_rate(self, minutes: int = 5) -> float:
        """Return steps completed per minute over the last N minutes."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM document_analysis_state
                WHERE completed_at > NOW() - INTERVAL '%s minutes'
            """ % minutes)  # safe: minutes is always an int from our code
            count = cursor.fetchone()["cnt"]
            return count / minutes if minutes > 0 else 0

    def get_pipeline_errors(self, limit: int = 50) -> list[dict]:
        """Return documents with errors across all steps."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT das.document_id, d.title, das.step_name, das.error, das.updated_at,
                       ds.name as source_name
                FROM document_analysis_state das
                JOIN document d ON d.id = das.document_id
                JOIN document_source ds ON ds.id = d.source_id
                WHERE das.error IS NOT NULL
                ORDER BY das.updated_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
