# Getnet Merchant Assistant Web

Streamlit interface for the Getnet Merchant Assistant. It talks only to the backend `/v1` API and never reads backend files or provider credentials.

## Run

Install this project and start Streamlit:

```powershell
python -m pip install -e ".[test]"
streamlit run app.py
```

Choose **Simulado** for a fully local fixture flow, **Gravado** to replay a saved synthetic response, or **Real (API)** and provide the backend URL plus a demo/admin access key configured on the backend. Provider keys are never entered in this interface.

The backend README explains how to run the backend with Docker; its [Streamlit interface](https://github.com/lauroPereira/ai-getnet-case#streamlit-interface) section covers the `/v1` access keys this interface needs. Sharing by public IP is described in the v0.6.0 [demonstration guide](https://github.com/lauroPereira/ai-getnet-case/blob/67736304ef948db41b082aea9b1b1b20c3f912b5/docs/guia-demonstracao.md), kept in the backend history.

## Areas (v0.6.0)

- **Atendimento:** catalog scenario or free message, published profile, LLM/JEV Router toggle, conversation and case state, GET-only recovery of a pending question, and the Router-only comparison in its own section.
- **Resultados:** the selected run of this session with mode (real, simulated, recorded), route, steps, tools, effective model, tokens and cost per agent, profile/prompt/knowledge-base versions, sources and retrieved passages, deterministic checks with their origin and critical failures, and on-demand AI evaluation (estimate, confirmation, evaluation of the stored reply, separate cost; "not evaluated" otherwise). Profile comparison: estimate, confirmation, start, refresh by GET, a cost x quality chart, a cost by agent/model chart whose selected bar opens its runs, and a table of cells.
- **Administração:** profiles and the versioned knowledge base, shown only to admin sessions; the backend still refuses admin writes from demo sessions.

Errors show what happened and the recovery action (refresh by GET, wait, start a new session or ask the administrator); no internal error is displayed. A transport failure (timeout) during a candidate build, test, activation or rollback discards the estimate and the confirmations and asks for a reload (GET) before any repetition, so a paid action is never repeated blindly. When `LOCK_BACKEND_URL` is on, Simulado offers only the demo role.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `BACKEND_API_URL` | `http://localhost:8000` | Backend address shown in Real (API) mode |
| `LOCK_BACKEND_URL` | empty | `true` keeps the backend address fixed (use it when the interface is shared) |
| `BACKEND_TIMEOUT_SECONDS` | `20` | Reads and administrative calls |
| `BACKEND_MESSAGE_TIMEOUT_SECONDS` | `105` | Calls that wait for paid work (message, Router comparison, evaluation, candidate build/test): backend `MAX_REQUEST_SECONDS` (90) plus a margin |

To share the interface, run `streamlit run app.py --server.address 0.0.0.0 --server.port 8501` (add `--server.sslCertFile` and `--server.sslKeyFile` for HTTPS) with the backend API kept on `127.0.0.1`.

## Contract and client

`openapi-v1.json` is the backend contract snapshot. Regenerate its route map after an approved API change:

```powershell
python tools/generate_client.py
```

The Streamlit interface uses explicit form/button actions. A rerun only redraws the current session; it does not repeat a chat message, comparison, selected comparison use, profile update, or publication. A comparison is bound to its selected Router. Changing the selection invalidates its result and requires an explicit new comparison before it can be used. Admin sessions can create drafts, add a new immutable version to a draft, and publish that version. Published profiles are never edited in place.

The admin editor reads the authenticated backend catalog and offers only documented agent/model combinations. The current optional parameter is `max_output_tokens` for `openai:gpt-6-luna` and `deepseek:deepseek-flash`; its per-agent maximum equals the existing application cap, so it can lower a reservation but cannot raise it. TypeSafe/JEV has no configurable parameter. The profile `evaluation` field is kept in the schema but not used by the attendance; the on-demand evaluator is fixed by the backend.

Navigation, tab changes, reruns and chart selections do not send POST requests, inferences, publications, builds, tests or activations. Paid actions require an explicit click after an estimate and a confirmation; a consumed estimate is cleared so the same click cannot be repeated.

## Validation

Install the test extra (`python -m pip install -e ".[test]"`, which includes Ruff) and run `python tools/scripts/lint_and_tests.py --strict`. v0.6.0 passed **42 web tests** and Ruff (strict script exit 0) with fixtures and fake transports; the backend strict script passed **349 tests**. Independent review and real QA through this interface are apt ([result](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v060-resultado.md)). Earlier results are in the backend evidence ([v0.5.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v050-resultado.md)).

This is the private [ai-getnet-case-web repository](https://github.com/lauroPereira/ai-getnet-case-web), integrated through its own pull request. Paid calls only happen in the backend under its persistent budget guard.
