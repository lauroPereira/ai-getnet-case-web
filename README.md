# Getnet Merchant Assistant Web

Streamlit interface for the Getnet Merchant Assistant. It talks only to the backend `/v1` API and never reads backend files or provider credentials.

## Run

Install this project and start Streamlit:

```powershell
python -m pip install -e ".[test]"
streamlit run app.py
```

Choose **Simulado** for a fully local fixture flow, **Gravado** to replay a saved synthetic response, or **Real (API)** and provide the backend URL plus a demo/admin access key configured on the backend. Provider keys are never entered in this interface.

## Contract and client

`openapi-v1.json` is the backend contract snapshot. Regenerate its route map after an approved API change:

```powershell
python tools/generate_client.py
```

The Streamlit interface uses explicit form/button actions. A rerun only redraws the current session; it does not repeat a chat message, comparison, selected comparison use, profile update, or publication. A comparison is bound to its selected Router. Changing the selection invalidates its result and requires an explicit new comparison before it can be used. Admin sessions can create drafts, add a new immutable version to a draft, and publish that version. Published profiles are never edited in place.

The admin editor reads the authenticated backend catalog and offers only documented agent/model combinations. The current optional parameter is `max_output_tokens` for `openai:gpt-6-luna` and `deepseek:deepseek-flash`; its per-agent maximum equals the existing application cap, so it can lower a reservation but cannot raise it. TypeSafe/JEV has no configurable parameter, and Evaluation parameter support remains reserved for v0.6.0.

## Validation

Create a separate environment for this project, install its declared dependencies, then run `python -m pytest`. The v0.4.0 baseline passed **14 web tests**, Ruff check/format and independent technical review and functional QA. The backend passed **216 tests**. Tests and QA cover recorded/simulated labels, compatible profile parameters, publication and snapshots, and both directions of Router selection changes without automatic comparison on rerun. See the backend [validation result](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v040-resultado.md) for commands, hashes and limits.

This is the private [ai-getnet-case-web repository](https://github.com/lauroPereira/ai-getnet-case-web), integrated through its own pull request. v0.4.0 used programmed models and no paid provider inference. Real JEV and authenticated model availability remain pending for the next version's authorized QA, subject to the backend's persistent global budget guard. This is source integration, not public deployment.
