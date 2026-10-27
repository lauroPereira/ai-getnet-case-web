"""Generated from openapi-v1.json by tools/generate_client.py. Do not edit."""

OPERATIONS = {
    "create_session": ("POST", "/v1/sessions"),
    "revoke_session": ("DELETE", "/v1/sessions/current"),
    "model_catalog": ("GET", "/v1/catalog/models"),
    "list_profiles": ("GET", "/v1/profiles"),
    "create_profile": ("POST", "/v1/profiles"),
    "update_profile": ("PUT", "/v1/profiles/{profile_id}/draft"),
    "publish_profile": ("POST", "/v1/profiles/{profile_id}/publish"),
    "compare_routers": ("POST", "/v1/router/compare"),
    "send_v1_message": ("POST", "/v1/messages"),
    "get_run": ("GET", "/v1/runs/{request_id}"),
    "get_conversation": ("GET", "/v1/conversations/{conversation_id}"),
}
