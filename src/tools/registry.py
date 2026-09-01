"""Single source of truth for the tool names and record kinds that exist
in this sandbox, so the scenario validator, the diagnosis prompt, and
anything else that needs to know what's real don't each hardcode their own
copy.

See PROJECT_SPEC.md section 2.
"""

from __future__ import annotations

KNOWN_TOOLS = {
    "restart_service",
    "scale_service",
    "delete_records",
    "clear_cache",
    "apply_config_change",
    "rollback_deployment",
}

RECORD_KINDS = {"order", "session", "cache_entry"}
