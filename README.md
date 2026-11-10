# Getnet Merchant Assistant Web



Streamlit interface for the Getnet Merchant Assistant. It talks only to the backend `/v1` API and never reads backend files or provider credentials.



## Run



Install this project and start Streamlit:



```powershell

python -m pip install -e ".[test]"

streamlit run app.py

```



Choose **Simulado** for a fully local fixture flow, **Gravado** to replay a saved synthetic response, or **Real (API)** and use **Entrar** to sign in (v0.13.0: authorization code with PKCE S256 and a rotating refresh token; the interface has no user or password field and never uses the password grant). **Entrar** opens the backend's login page (`/v1/auth/authorize`) in a popup, so allow popups for the interface. After the login the backend sends the popup back to `AUTH_REDIRECT_URI` with `code`, `state` and `iss`; that page renders nothing of the app, hands the parameters to the waiting tab and closes. Only the waiting session holds the PKCE verifier: it checks `state` and the exact `iss`, refuses repeated parameters or an `error`, and only then exchanges the code. An unfinished login starts over after five minutes.



The access and refresh tokens stay in the Streamlit session on the server and are never shown. The access token is renewed 30 seconds before it expires; the old refresh token is sent once and never retried, so a failed or lost renewal, or one that answers for another principal, ends the session (for a lost one the interface first tries, once, to revoke the login with the old refresh token; this only works while the access token is still valid, so after a long idle period the unreceived rotation stays valid until the refresh token expires). Changing the backend URL ends the session and revokes it at the backend that issued it. **Encerrar sessão** revokes the access token and the whole login (its refresh family); if the backend does not confirm it, the screen says so. Provider keys are never entered in this interface.



The backend README explains how to run the backend with Docker; its [Streamlit interface](https://github.com/lauroPereira/ai-getnet-case#streamlit-interface) section covers the backend this interface signs in to. Its `OAUTH_REDIRECT_URIS` must list `AUTH_REDIRECT_URI` exactly, and `AUTH_ISSUER` must be its `JWT_ISSUER`: `http://127.0.0.1:8000` by default, `http://api:8000` with its `docker compose`. Open the interface at the address of `AUTH_REDIRECT_URI` (for example `http://localhost:8501/`, not `http://127.0.0.1:8501/`): the popup reaches the waiting tab only on the same origin. The login is offered only while the backend URL is `BACKEND_API_URL`, the address paired with `AUTH_ISSUER`: another address typed in the sidebar gets a warning and never receives a code or verifier. Sharing by public IP is described in the v0.6.0 [demonstration guide](https://github.com/lauroPereira/ai-getnet-case/blob/67736304ef948db41b082aea9b1b1b20c3f912b5/docs/guia-demonstracao.md), kept in the backend history.



## Areas (v0.6.0)



- **Atendimento:** catalog scenario or free message, published profile, LLM/JEV Router toggle, conversation and case state, GET-only recovery of a pending question, and the Router-only comparison in its own section.

- **Resultados:** the selected run of this session with mode (real, simulated, recorded), route, steps, tools, effective model, tokens and cost per agent, profile/prompt/knowledge-base versions, sources and retrieved passages, deterministic checks with their origin and critical failures, and on-demand AI evaluation (estimate, confirmation, evaluation of the stored reply, separate cost; "not evaluated" otherwise). Profile comparison: estimate, confirmation, start, refresh by GET, a cost x quality chart, a cost by agent/model chart whose selected bar opens its runs, and a table of cells.

- **Administração:** profiles and the versioned knowledge base, shown only to admin sessions; the backend still refuses admin writes from demo sessions.
- **Ticketing (v0.7.0, operators only):** talks to the ticketing service (`TICKETING_URL`), not to the assistant. Queues by state (Recepção `new`, Atendimento `open`, Envio `pending`, Finalização `solved`); ticket detail with case data, draft reply, masked conversation, history and deliveries in both directions; Assumir; Aprovar, Responder and Rejeitar with confirmation and one `idempotency_key` per click, reused when the same action is retried after a transport failure; Resolver. Conflicts (`409 claimed_by_other`, `already_resumed`) and "envio pendente" become messages with a refresh action.

Areas by role: merchant and demo see Atendimento and Resultados; operators see only Ticketing; admins see Atendimento, Resultados and Administração. The backend and the ticketing service still decide what each token may do. In Atendimento, a reply written by a human attendant (`via=operator`, route `human_reply`) is marked as such.



Errors show what happened and the recovery action (refresh by GET, wait, start a new session or ask the administrator); no internal error is displayed. A transport failure (timeout) during a candidate build, test, activation or rollback discards the estimate and the confirmations and asks for a reload (GET) before any repetition, so a paid action is never repeated blindly. When `LOCK_BACKEND_URL` is on, Simulado offers only the demo role.



## Configuration



| Variable | Default | Meaning |

| --- | --- | --- |

| `BACKEND_API_URL` | `http://localhost:8000` | Backend address shown in Real (API) mode |

| `LOCK_BACKEND_URL` | empty | `true` keeps the backend address fixed (use it when the interface is shared) |

| `BACKEND_TIMEOUT_SECONDS` | `20` | Reads and administrative calls |

| `TICKETING_URL` | `http://127.0.0.1:8002` | Ticketing service used by the operator's Ticketing area |
| `AUTH_CLIENT_ID` | `merchant-assistant-web` | Public client registered in the backend |
| `AUTH_ISSUER` | `http://127.0.0.1:8000` | The backend's `JWT_ISSUER`, compared exactly with the `iss` of the redirect (`http://api:8000` with the backend's `docker compose`) |
| `AUTH_REDIRECT_URI` | `http://localhost:8501/` | This interface's address, registered exactly in the backend's `OAUTH_REDIRECT_URIS` |
| `BACKEND_MESSAGE_TIMEOUT_SECONDS` | `105` | Calls that wait for paid work (message, Router comparison, evaluation, candidate build/test): backend `MAX_REQUEST_SECONDS` (90) plus a margin |



To share the interface, run `streamlit run app.py --server.address 0.0.0.0 --server.port 8501` (add `--server.sslCertFile` and `--server.sslKeyFile` for HTTPS) with the backend API kept on `127.0.0.1`. Since v0.13.0 the login popup opens the backend's `/v1/auth/authorize` in the user's browser, so remote users also need that page reachable at `BACKEND_API_URL` (for example behind the same HTTPS proxy), and the interface's public `https` address must be in `AUTH_REDIRECT_URI` and in the backend's `OAUTH_REDIRECT_URIS` (plain `http` is accepted only on the loopback).



## Contract and client



`openapi-v1.json` is the backend contract snapshot (v0.7.0: backend commit `7079920`, with `/v1/auth/token` and `/v1/auth/revoke`; `/v1/sessions` is gone). The ticketing service has its own client in `src/getnet_web/ticketing.py`. Regenerate the route map after an approved API change:



```powershell

python tools/generate_client.py

```



The Streamlit interface uses explicit form/button actions. A rerun only redraws the current session; it does not repeat a chat message, comparison, selected comparison use, profile update, or publication. A comparison is bound to its selected Router. Changing the selection invalidates its result and requires an explicit new comparison before it can be used. Admin sessions can create drafts, add a new immutable version to a draft, and publish that version. Published profiles are never edited in place.



The admin editor reads the authenticated backend catalog and offers only documented agent/model combinations. The current optional parameter is `max_output_tokens` for `openai:gpt-6-luna` and `deepseek:deepseek-flash`; its per-agent maximum equals the existing application cap, so it can lower a reservation but cannot raise it. TypeSafe/JEV has no configurable parameter. The profile `evaluation` field is kept in the schema but not used by the attendance; the on-demand evaluator is fixed by the backend.



Navigation, tab changes, reruns and chart selections do not send POST requests, inferences, publications, builds, tests or activations. Paid actions require an explicit click after an estimate and a confirmation; a consumed estimate is cleared so the same click cannot be repeated.



## Validation



Install the test extra (`python -m pip install -e ".[test]"`, which includes Ruff) and run `python tools/scripts/lint_and_tests.py --strict`. v0.6.0 passed **42 web tests** and Ruff (strict script exit 0) with fixtures and fake transports; the backend strict script passed **349 tests**. Independent review and real QA through this interface are apt ([result](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v060-resultado.md)). Earlier results are in the backend evidence ([v0.5.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v050-resultado.md)).



The redirect login (v0.13.0) is covered by `tests/test_oauth_primitives.py` and `tests/test_oauth_interface.py` (a fake issuer on `httpx.MockTransport`); `tests/test_oauth_backend_integration.py` runs the same client against the backend in process when the backend package is on the path, and is skipped otherwise.



This is the private [ai-getnet-case-web repository](https://github.com/lauroPereira/ai-getnet-case-web), integrated through its own pull request. Paid calls only happen in the backend under its persistent budget guard.

