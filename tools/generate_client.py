from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "openapi-v1.json"
OUTPUT = ROOT / "src" / "getnet_web" / "generated_api.py"
ALIASES = {
    "create_session": "create_session_v1_sessions_post",
    "revoke_session": "revoke_session_v1_sessions_current_delete",
    "model_catalog": "model_catalog_v1_catalog_models_get",
    "list_profiles": "profiles_v1_profiles_get",
    "create_profile": "profiles_create_v1_profiles_post",
    "update_profile": "profiles_update_v1_profiles__profile_id__draft_put",
    "publish_profile": "profiles_publish_v1_profiles__profile_id__publish_post",
    "compare_routers": "compare_v1_router_compare_post",
    "send_v1_message": "messages_v1_v1_messages_post",
    "get_run": "run_v1_runs__request_id__get",
    "get_conversation": "conversation_v1_conversations__conversation_id__get",
    "kb_documents": "kb_documents_v1_kb_documents_get",
    "kb_document": "kb_document_v1_kb_documents__document_id__get",
    "kb_save_draft": "kb_save_draft_v1_kb_drafts__document_id__put",
    "kb_diff": "kb_diff_v1_kb_drafts__document_id__diff_get",
    "kb_estimate": "kb_estimate_v1_kb_versions_estimate_post",
    "kb_build": "kb_build_v1_kb_versions_post",
    "kb_versions": "kb_versions_v1_kb_versions_get",
    "kb_version": "kb_version_v1_kb_versions__version_id__get",
    "kb_test": "kb_test_v1_kb_versions__version_id__test_post",
    "kb_activate": "kb_activate_v1_kb_versions__version_id__activate_post",
    "kb_rollback": "kb_rollback_v1_kb_versions__version_id__rollback_post",
    "list_scenarios": "scenarios_list_v1_scenarios_get",
    "estimate_evaluation": "evaluation_estimate_v1_runs__request_id__evaluations_estimate_post",
    "create_evaluation": "evaluation_create_v1_runs__request_id__evaluations_post",
    "estimate_comparison": "comparison_estimate_v1_comparisons_estimate_post",
    "create_comparison": "comparison_create_v1_comparisons_post",
    "get_comparison": "comparison_read_v1_comparisons__comparison_id__get",
}

schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
operations = {}
for path, path_item in schema["paths"].items():
    if not path.startswith("/v1/"):
        continue
    for method, details in path_item.items():
        if method.lower() in {"get", "post", "put", "delete"} and isinstance(details, dict):
            operation_id = details.get("operationId")
            if operation_id:
                operations[operation_id] = (method.upper(), path)
missing = set(ALIASES.values()) - operations.keys()
if missing:
    raise SystemExit(f"OpenAPI operations are missing: {sorted(missing)}")
selected = {alias: operations[operation_id] for alias, operation_id in ALIASES.items()}
lines = [
    '"""Generated from openapi-v1.json by tools/generate_client.py. Do not edit."""',
    "",
    "OPERATIONS = {",
]
for alias, operation in selected.items():
    lines.append(f"    {json.dumps(alias)}: ({json.dumps(operation[0])}, {json.dumps(operation[1])}),")
lines.append("}")
lines.append("")
OUTPUT.write_text("\n".join(lines), encoding="utf-8")
print(f"Generated {len(selected)} /v1 operations into {OUTPUT.relative_to(ROOT)}")
