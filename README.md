# Getnet Merchant Assistant Web

The web interface of the [Getnet Merchant Assistant](https://github.com/lauroPereira/ai-getnet-case): merchants talk to
the assistant and follow their cases, attendants work the human-support queue, and whoever demonstrates the product
sees each answer's route, sources, cost and quality. It talks only to the backend's versioned `/v1` API and to the
ticketing service; it never reads backend files and never holds a provider key.

| For | What it delivers |
| --- | --- |
| Merchants | A conversation in Portuguese or English with the case state and the attendant's reply marked **Atendente humano**; the latest conversation comes back after a reload, a new tab or a new sign-in |
| Attendants | A Ticketing screen with queues by state; they claim a case, approve the assistant's draft, reply in their own words or reject it with an instruction, each action confirmed and safe to retry |
| Product demonstrations | Each run with route, steps, tools, model, tokens and cost per agent, sources and checks; on-demand AI evaluation; profile comparison with cost × quality charts; profile and knowledge-base administration |

**Release:** interface of backend v1.1.0 (2026-11-11) · [backend README](https://github.com/lauroPereira/ai-getnet-case#readme) · [results by version](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/README.md)

## Results at a glance

Each row links to the versioned record in the backend repository. All data is synthetic.

| Area | Measured result | Record |
| --- | --- | --- |
| End-to-end with a real model | The 10-minute walkthrough through this interface: redirect sign-in, a receivables question answered with dates and amounts, a handoff with protocol, the operator's reply from Ticketing and the AI evaluation of the assistant's answer (scores 5, 5 and 5) | [v1.0.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v100-resultado.md) |
| Conversation resume and availability | Ten moments through the interface with Docker and a phone-width browser: the missing key named, the conversation resumed with the attendant's reply, the next question answered on its own, ticket lookups and the JEV Router with its key configured | [v1.1.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v110-resultado.md) |
| Attendant's reply | Identical text in the interface, in `GET /v1/conversations/{id}` and in the assistant's ticket tool; resuming a conversation calls no model | [v1.1.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v110-resultado.md) |
| Sign-in | 10 logins in a real browser: 10 sessions, no orphan login; renewal, refresh reuse and logout with revocation | [v0.13.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v0130-resultado.md) |
| Access from another network | A phone on a mobile network through the quick tunnel: sign-in, attendance, handoff and the operator's reply at the public address | [v1.0.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v100-resultado.md) |
| Human support queue | A case becomes a ticket; reply, approve and reject from the screen reach the merchant as a turn marked `via=operator`; resolving closes the case | [v0.7.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v070-resultado.md) |
| Automated tests | 203 test cases offline plus 6 integration cases against the backend API in process, from 157 test functions; no network and no paid call | [Tests](#tests) |

## Run

Install this project and start Streamlit:

```powershell
python -m pip install -e ".[test]"
streamlit run app.py
```

Choose a mode in the sidebar:

- **Simulado** runs a local fixture flow, with no backend.
- **Gravado** replays a saved synthetic response.
- **Real (API)** talks to a running backend; **Entrar** signs in on the backend's login page.

The backend runs with Docker as described in its [Streamlit interface](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/wiki/guides/interface-and-tunnel.md#streamlit-interface)
section. The two sides are paired by three settings:

- The backend's `OAUTH_REDIRECT_URIS` lists `AUTH_REDIRECT_URI` exactly.
- `AUTH_ISSUER` is the backend's `JWT_ISSUER`: `http://127.0.0.1:8000` by default, `http://api:8000` with its `docker compose`.
- The interface opens at the address of `AUTH_REDIRECT_URI` (for example `http://localhost:8501/`, not `http://127.0.0.1:8501/`), because the login popup reaches the waiting tab on the same origin.

## Sign-in and session

The interface never sees a password or a provider key: the user signs in on the backend's own login page, with the
OAuth 2.0 authorization code flow, PKCE S256 and a rotating refresh token. There is no user or password field and no
password grant.

- **Entrar** opens the backend's `/v1/auth/authorize` in a popup; allow popups for the interface. After the login the
  backend sends the popup to `AUTH_REDIRECT_URI` with `code`, `state` and `iss`. That page renders nothing of the app,
  hands the parameters once to the waiting tab and closes; a browser that keeps it open shows a request to close it.
- The waiting session alone holds the PKCE verifier. It checks `state` and the exact `iss`, refuses repeated
  parameters or an `error`, and exchanges the code once per attempt, even if the same redirect arrives again. An
  unfinished login starts over after five minutes.
- The login is offered only while the backend URL is `BACKEND_API_URL`, the address paired with `AUTH_ISSUER`; another
  address typed in the sidebar gets a warning and never receives a code or a verifier.
- Access and refresh tokens stay in the Streamlit session on the server and are never displayed. The access token is
  renewed 30 seconds before it expires; each refresh token is sent once and never retried.
- A failed or lost renewal, or one that answers for another principal, ends the session. After a lost renewal the
  interface revokes the login once with the old refresh token while the access token is still valid; past that, the
  unreceived token lapses with the backend's refresh-token lifetime.
- Changing the backend URL ends the session and revokes it at the backend that issued it. **Encerrar sessão** revokes
  the access token and the whole login (its refresh family), and the screen says when the backend does not confirm it.

## Areas by role

Each role sees the work it does. The backend and the ticketing service still decide what each token may do.

| Role | Areas |
| --- | --- |
| Merchant, demo | Atendimento, Resultados |
| Operator | Ticketing |
| Admin | Atendimento, Resultados, Administração |

- **Atendimento:** a catalog scenario or a free message, the published profile, the LLM/JEV Router toggle, the
  conversation and the case state, GET-only recovery of a pending question, and the Router-only comparison in its own
  section. A reply written by a human attendant (`via=operator`, route `human_reply`) is marked as such.
- **Resultados:** the selected run of this session with its mode (real, simulated, recorded), route, steps, tools,
  effective model, tokens and cost per agent, profile, prompt and knowledge-base versions, sources and retrieved
  passages, deterministic checks with their origin and critical failures, and on-demand AI evaluation (estimate,
  confirmation, evaluation of the stored reply, separate cost; "not evaluated" otherwise).
- **Profile comparison** (in Resultados): estimate, confirmation, start, refresh by GET, a cost × quality chart, a cost
  by agent and model chart whose selected bar opens its runs, and a table of cells.
- **Administração:** profiles and the versioned knowledge base; the backend refuses admin writes from demo sessions.
- **Ticketing:** talks to the ticketing service (`TICKETING_URL`), not to the assistant.
  - Queues by state: Recepção (`new`), Atendimento (`open`), Envio (`pending`), Finalização (`solved`).
  - Ticket detail with the case data, the draft reply, the masked conversation, the history and the deliveries in both
    directions.
  - **Assumir**; **Aprovar**, **Responder** and **Rejeitar** with confirmation and one `idempotency_key` per click,
    reused when the same action is retried after a transport failure; **Resolver**.
  - Conflicts (`409 claimed_by_other`, `already_resumed`) and "envio pendente" become messages with a refresh action.

## Conversations that survive a reload

A merchant who reloads the page, opens a new tab or signs in again finds the conversation, and the attendant's reply,
where it was. The backend keeps the conversation; the interface brings it back.

- After a merchant or demo login with no conversation in the session, the interface reads the latest conversation of
  the signed-in merchant (`GET /v1/conversations?limit=1`, then `GET /v1/conversations/{id}`, both without cost). It
  shows the history with the attendant's turns marked **Atendente humano**, says **Conversa retomada** with the date
  of the last activity, and sends the next message to the same conversation.
- An attendant's reply that the backend has not delivered yet shows in the history once: the next message carries its
  `message_id` (`displayed_operator_message_id`), the backend counts it as delivered, and that message gets its own
  answer.
- **Nova conversa** starts another conversation in this session; nothing is deleted in the backend, and Resultados
  keeps the runs of this session.
- A new conversation starts in admin sessions, in Simulado and Gravado, when the merchant has no earlier conversation,
  and with a backend before v1.1.0, which has no conversation list. When the list or the conversation cannot be read,
  a short notice appears and a new conversation starts.

## Profiles and Router engines the backend can run

The interface offers only what the backend can execute and names the key that is missing.

- `GET /v1/profiles` says which profiles have a key for the provider of each of their agents and whether the JEV
  Router has one, never the key itself.
- A profile without its key shows **indisponível neste backend** and is not sent. Without `TYPESAFE_API_KEY` the JEV
  is disabled in Atendimento, in the Router-only comparison (which always calls both Routers) and in the profile
  comparison.
- A sentence names the missing key: `DEEPSEEK_API_KEY` for Hybrid and Adjusted, `TYPESAFE_API_KEY` for the JEV.
- The defaults are an available profile and Router: Hybrid starts on the LLM Router when the JEV is unavailable.
- With a backend before v1.1.0, and in Simulado and Gravado, every option is offered.

## Paid actions and recovery

No paid action runs without an explicit click, and none repeats blindly. Paid calls happen in the backend, under its
persistent budget ledger.

- Navigation, tab changes, reruns and chart selections send no POST request, inference, publication, build, test or
  activation. A rerun redraws the current session and repeats no chat message, comparison, selected comparison use,
  profile update or publication.
- A paid action needs an explicit click after an estimate and a confirmation; a consumed estimate is cleared, so the
  same click cannot run twice.
- A Router comparison is bound to its selected Router. Changing the selection invalidates the result and requires a
  new explicit comparison before it can be used.
- Errors show what happened and the recovery action (refresh by GET, wait, start a new session or ask the
  administrator); no internal error is displayed.
- A transport failure (timeout) during a candidate build, test, activation or rollback discards the estimate and the
  confirmations and asks for a reload (GET) before any repetition.
- With `LOCK_BACKEND_URL` on, Simulado offers the demo role alone.

## Profile administration

Admin sessions create drafts, add a new immutable version to a draft and publish that version; a published profile is
never edited in place.

- The editor reads the authenticated backend catalog and offers only the documented agent and model combinations.
- The optional parameter is `max_output_tokens` for `openai:gpt-6-luna` and `deepseek:deepseek-flash`. Its per-agent
  maximum equals the application cap, so it lowers a reservation and never raises it. TypeSafe/JEV has no
  configurable parameter.
- The profile `evaluation` field stays in the schema and is not used by the attendance; the backend fixes the
  on-demand evaluator.

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
| `AUTH_AUTHORIZE_URL` | empty (`BACKEND_API_URL` + `/v1/auth/authorize`) | Public address of the backend's login page, opened by the browser; set it behind the demonstration tunnel (https, or http on the loopback; no credentials, query or fragment; an invalid value offers no login). The code exchange, renewal and logout always use `BACKEND_API_URL` |
| `BACKEND_MESSAGE_TIMEOUT_SECONDS` | `105` | Calls that wait for paid work (message, Router comparison, evaluation, candidate build/test): backend `MAX_REQUEST_SECONDS` (90) plus a margin |

### Share the interface

The backend's demonstration tunnel ([Open the demonstration through a tunnel](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/wiki/guides/interface-and-tunnel.md#open-the-demonstration-through-a-tunnel))
publishes one origin with this interface and the backend's login page, and no other backend route.
`docker compose --profile tunnel up` starts the proxy, and `cloudflared tunnel --url http://localhost:8080` gives it an
`https://<name>.trycloudflare.com` address. Start the interface with that address for the browser and the internal one
for the server, and keep port 8501 closed to other machines:

```bash
export BACKEND_API_URL=http://localhost:8000 LOCK_BACKEND_URL=true AUTH_ISSUER=http://api:8000
export AUTH_REDIRECT_URI=https://<name>.trycloudflare.com/
export AUTH_AUTHORIZE_URL=https://<name>.trycloudflare.com/v1/auth/authorize
streamlit run app.py
```

The browser opens the login at `AUTH_AUTHORIZE_URL` and comes back to `AUTH_REDIRECT_URI`; the code, the tokens and
the logout go to `BACKEND_API_URL` alone, and the login is offered only at that paired address. The tunnel address changes
at every opening and is never committed.

On its own address, the interface runs with `streamlit run app.py --server.address 0.0.0.0 --server.port 8501` (add
`--server.sslCertFile` and `--server.sslKeyFile` for HTTPS), with the backend API kept on `127.0.0.1`. Remote users
then reach the backend's `/v1/auth/authorize` at `BACKEND_API_URL` (for example behind the same HTTPS proxy), and the
interface's public `https` address goes in `AUTH_REDIRECT_URI` and in the backend's `OAUTH_REDIRECT_URIS` (plain
`http` is restricted to the loopback).

## Contract and client

`openapi-v1.json` is the snapshot of the backend contract for v1.1.0, with
`GET /v1/conversations`, the availability fields of `GET /v1/profiles` and `displayed_operator_message_id` in
`POST /v1/messages`, exported with the demonstration routes off. The ticketing service has its own client in
`src/getnet_web/ticketing.py`. After a change to the backend contract, regenerate the route map:

```powershell
python tools/generate_client.py
```

## Tests

The interface is tested offline at zero cost, with fixtures and scripted transports, and against the backend itself
in process. Install the test extra (`python -m pip install -e ".[test]"`, which includes Ruff) and run
`python tools/scripts/lint_and_tests.py --strict`.

| Area | Test files | Cases |
| --- | --- | --- |
| HTTP client, generated operations and the OpenAPI contract | `test_interface.py` | 20 |
| Areas, catalog scenarios, Resultados, evaluation, profile comparison and recovery messages | `test_v060_interface.py` | 22 |
| Areas by role and the Ticketing screen | `test_v070_interface.py` | 50 |
| Sign-in by redirect, renewal and logout | `test_oauth_interface.py`, `test_oauth_primitives.py`, `test_oauth_component_js.py` | 66 |
| Login at the tunnel's public address | `test_tunnel_interface.py` | 10 |
| Conversation resume | `test_v110_resume.py` | 24 |
| Profile and Router availability | `test_v111_availability.py` | 11 |
| The client against the backend in process (scripted model, no key, no network) | `test_oauth_backend_integration.py`, `test_v110_backend_integration.py`, `test_v111_backend_integration.py` | 6 |

The 203 offline cases come from 151 test functions, and the 6 integration cases from 6 more. The integration files run
when the backend package is importable (both `src` folders on the path, as their docstrings show) and are skipped
otherwise, so the two repositories stay independent. Validation with real models runs through the backend's
campaigns, recorded in its [results by version](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/README.md).
