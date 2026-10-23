"""Analysis dashboard queries: group/document drill-down, progress, errors."""


class DashboardMixin:
    """Dashboard views for analysis pipeline monitoring."""

    def get_group_dashboard(self, profile_key: str) -> list[dict]:
        """Return groups with one profile's gate states for the analysis dashboard.

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
                WHERE group_id = ANY(%s) AND profile_key = %s
            """, (group_ids, profile_key))
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

    def get_document_dashboard(self, group_id: int, profile_key: str) -> list[dict]:
        """Return documents in a group with their analysis states for drill-down view.

        Returns one row per document with completion state per step.
        """
        steps = self.get_analysis_steps()
        step_names = [s["name"] for s in steps]
        step_versions = {s["name"]: s["current_version"] for s in steps}
        profile = self.get_analysis_profile(profile_key) or {}
        profile_contract = profile.get("contract_version")

        members = self.get_group_members(group_id)
        if not members:
            return []

        doc_ids = [str(m["document_id"]) for m in members]

        # Bulk fetch all states for these documents
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT das.document_id::text, das.step_name,
                       das.completed_version, das.contract_version,
                       das.stale, das.error, das.completed_at
                FROM document_analysis_state das
                WHERE das.document_id = ANY(%s::uuid[]) AND das.profile_key = %s
            """, (doc_ids, profile_key))
            state_rows = cursor.fetchall()

        state_lookup = {}
        for row in state_rows:
            state_lookup[(row["document_id"], row["step_name"])] = dict(row)

        # Check which steps are permitted for this group
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT step_name, permitted FROM group_analysis_gate
                WHERE group_id = %s AND profile_key = %s AND permitted = TRUE
            """, (group_id, profile_key))
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
                    needs_work = (
                        cv is None or cv < sv or s.get("stale", False)
                        or s.get("contract_version") != profile_contract
                    )
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

    def clear_all_errors(self, profile_key: str | None = None) -> int:
        """Clear errors in document_analysis_state, for one profile or all. Returns count cleared."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE document_analysis_state
                SET error = NULL, updated_at = NOW()
                WHERE error IS NOT NULL AND (%s::text IS NULL OR profile_key = %s)
            """, (profile_key, profile_key))
            return cursor.rowcount

    def get_pipeline_progress(self, profile_key: str) -> list[dict]:
        """Return per-group, per-step completion counts for one profile's permitted steps.

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
                                   AND das.contract_version = p.contract_version
                                   AND NOT COALESCE(das.stale, FALSE)
                                   AND das.error IS NULL
                                   AND NOT EXISTS (
                                       SELECT 1 FROM unnest(s.depends_on) dep(name)
                                       WHERE NOT EXISTS (
                                           SELECT 1 FROM document_analysis_state das2
                                           WHERE das2.document_id = lgm.document_id
                                             AND das2.profile_key = gag.profile_key
                                             AND das2.step_name = dep.name
                                             AND das2.completed_version IS NOT NULL
                                       )
                                   )
                                   THEN 1 END) as completed,
                       COUNT(CASE WHEN das.stale = TRUE AND das.error IS NULL THEN 1 END) as stale,
                       COUNT(CASE WHEN das.error IS NOT NULL THEN 1 END) as errors
                FROM lesson_group lg
                JOIN group_analysis_gate gag
                    ON gag.group_id = lg.id AND gag.permitted = TRUE AND gag.profile_key = %s
                JOIN analysis_profile p ON p.key = gag.profile_key
                JOIN analysis_step s ON s.name = gag.step_name AND NOT s.retired
                JOIN lesson_group_member lgm ON lgm.group_id = lg.id
                LEFT JOIN document_analysis_state das
                    ON das.document_id = lgm.document_id AND das.step_name = gag.step_name
                   AND das.profile_key = gag.profile_key
                GROUP BY lg.id, lg.name, gag.step_name, s.current_version
                ORDER BY lg.name, gag.step_name
            """, (profile_key,))
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_completion_rate(self, minutes: int = 5, profile_key: str | None = None) -> float:
        """Return steps completed per minute over the last N minutes, for one profile or all."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM document_analysis_state
                WHERE completed_at > NOW() - INTERVAL '%s minutes'
                  AND (%%s::text IS NULL OR profile_key = %%s)
            """ % minutes, (profile_key, profile_key))  # safe: minutes is always an int from our code
            count = cursor.fetchone()["cnt"]
            return count / minutes if minutes > 0 else 0

    def get_pipeline_errors(self, limit: int = 50, profile_key: str | None = None) -> list[dict]:
        """Return documents with errors across all steps, for one profile or all."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT das.document_id, d.title, das.profile_key, das.step_name, das.error,
                       das.updated_at, ds.name as source_name
                FROM document_analysis_state das
                JOIN document d ON d.id = das.document_id
                JOIN document_source ds ON ds.id = d.source_id
                WHERE das.error IS NOT NULL AND (%s::text IS NULL OR das.profile_key = %s)
                ORDER BY das.updated_at DESC
                LIMIT %s
            """, (profile_key, profile_key, limit))
            return [dict(row) for row in cursor.fetchall()]
