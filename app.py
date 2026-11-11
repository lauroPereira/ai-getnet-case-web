from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime

import altair as alt
import httpx
import pandas as pd
import streamlit as st

from getnet_web.api import (
    DEFAULT_TIMEOUT_SECONDS,
    WEB_CLIENT_ID,
    APIError,
    close_session_client,
    get_session_client,
    session_ended,
    token_claims,
)
from getnet_web.oauth import AuthorizationAttempt, authorization_endpoint, exchange_code, refresh
from getnet_web.oauth_bridge import login_popup, return_from_popup
from getnet_web.simulated import SimulatedAPI
from getnet_web.ticketing import (
    STATUSES,
    close_ticketing_client,
    get_ticketing_client,
    new_idempotency_key,
    ticketing_url,
)

MODE_LABELS = {
    "real": "Execução real (provedores via backend)",
    "simulated": "Execução simulada (sem backend nem provedores)",
    "recorded": "Execução gravada (resposta salva reapresentada)",
}
RECOVERY_BY_STATUS = {
    401: "A sessão expirou ou foi encerrada. Entre novamente.",
    403: "Esta ação não é permitida para a sessão atual. Peça ao administrador.",
    404: "O item não foi encontrado. Atualize a lista e selecione novamente.",
    409: "Os dados mudaram desde a última leitura. Atualize (consulta sem custo) e confirme de novo.",
    422: "Revise a seleção ou os dados informados e tente novamente.",
    429: "O limite de consumo foi atingido. Aguarde ou peça ao administrador para revisar o limite.",
    503: "O serviço está temporariamente indisponível. Aguarde alguns instantes e tente novamente.",
}
RECOVERY_BY_CODE = {
    "invalid_grant": "Usuário ou senha incorretos. Confira e tente de novo.",
    "invalid_client": "A interface não está registrada como cliente no backend. Peça ao administrador.",
    "invalid_request": "O pedido de login foi recusado. Confira usuário e senha.",
    "too_many_attempts": "Muitas tentativas de login. Aguarde o tempo indicado e tente de novo.",
    "token_required": "A sessão expirou ou foi encerrada. Entre novamente.",
    "invalid_token": "A sessão expirou ou foi encerrada. Entre novamente.",
    "insufficient_scope": "Esta ação não é permitida para o seu papel.",
    "claimed_by_other": "Outro operador já assumiu este caso. Atualize a fila e escolha outro ticket.",
    "already_resumed": "Este caso já recebeu uma ação de atendente. Atualize para ver o estado atual.",
    "delivery_pending": "Um envio anterior deste ticket ainda está pendente. Atualize e tente depois.",
    "ticket_solved": "O ticket já foi resolvido. Atualize a fila.",
    "idempotency_key_reused": "Esta tentativa já foi usada para outra ação. Atualize e envie de novo.",
    "unknown_operator": "O seu usuário não é um operador ativo no assistente. Peça ao administrador.",
    "case_not_found": "O caso não existe mais no assistente. Atualize a fila.",
    "assistant_rejected": "O assistente recusou a credencial do serviço de ticketing. Peça ao administrador.",
    "invalid_response": "O ticketing respondeu num formato inesperado. Use Atualizar para conferir o estado antes de "
    "repetir; repetir a mesma ação reaproveita a mesma chave.",
    "demo_message_limit_reached": "Esta sessão demo atingiu o limite de mensagens. Encerre a sessão e entre de novo, "
    "ou peça ao administrador.",
    "comparison_running": "Já há uma comparação em andamento. Use “Atualizar comparação”.",
    "estimate_stale": "A estimativa mudou. Estime novamente antes de confirmar.",
    "evaluator_price_unknown": "O preço do avaliador é desconhecido; a avaliação fica bloqueada.",
}
TRANSPORT_RECOVERY = "A conexão com o backend falhou. Nada foi reenviado; atualize por consulta (GET) mais tarde."
SESSION_EXPIRED = "Sua sessão expirou ou foi encerrada. Entre novamente."
REVOKE_UNCONFIRMED = (
    "Você saiu desta interface, mas o backend não confirmou a revogação do token: ele pode continuar válido até "
    "expirar. Se a sessão foi usada em um computador compartilhado, avise o administrador."
)
LOGIN_REFUSED = "Não foi possível entrar. Clique em Entrar e tente novamente."
LOGIN_EXPIRED = "A tentativa de login expirou. Clique em Entrar e tente novamente."
LOGIN_UNREACHABLE = "Backend inacessível. Confira a URL da API e se o backend está em execução."
API_URL_CHANGED = "A URL da API mudou: a sessão anterior foi encerrada. Entre novamente."
SESSION_BUSY = "A sessão ainda está concluindo um login ou uma renovação. Aguarde alguns segundos e tente de novo."
LOGIN_UNPAIRED = (
    "O login só está disponível para a URL da API configurada nesta interface ({url}), a do emissor {issuer}. "
    "Volte a essa URL para entrar."
)
LOGIN_URL_INVALID = "O endereço público de login (AUTH_AUTHORIZE_URL) é inválido. Peça ao administrador."
# Login by redirection (backend v0.13.0, task 97): authorization code with PKCE S256 and a rotating refresh token.
# AUTH_ISSUER is compared exactly with the `iss` of the redirect; AUTH_REDIRECT_URI must be registered, exactly, in the
# backend's OAUTH_REDIRECT_URIS, and the interface must be opened at that address (the popup reaches this tab only on
# the same origin). The issuer is paired with BACKEND_API_URL: the login is offered only at that address, so another
# backend typed in the sidebar never receives a code and its verifier (mix-up, RFC 9207).
BACKEND_API_URL = os.environ.get("BACKEND_API_URL", "http://localhost:8000")
AUTH_ISSUER = os.environ.get("AUTH_ISSUER", "http://127.0.0.1:8000")
AUTH_REDIRECT_URI = os.environ.get("AUTH_REDIRECT_URI", "http://localhost:8501/")
# Behind the demonstration tunnel (backend v1.0.0, ADR 0026) the browser reaches the login page at a public address
# while the server keeps talking to BACKEND_API_URL. Empty: the login page of BACKEND_API_URL, as before.
AUTH_AUTHORIZE_URL = os.environ.get("AUTH_AUTHORIZE_URL", "").strip()
OAUTH_ATTEMPT_SECONDS = 300  # an unfinished login starts over with a new state and verifier
REFRESH_MARGIN_SECONDS = 30  # the access token is renewed this long before it expires
STATUS_LABELS = {"new": "Recepção", "open": "Atendimento", "pending": "Envio", "solved": "Finalização"}
ATTENDANT_ACTIONS = {"approve": "rascunho aprovado", "reply": "resposta do atendente", "reject": "refeita após rejeição"}
ACTION_LABELS = {"approve": "Aprovar o rascunho", "reply": "Responder", "reject": "Rejeitar com instrução"}


def explain(exc: APIError) -> str:
    action = RECOVERY_BY_CODE.get(exc.code) or RECOVERY_BY_STATUS.get(
        exc.status_code, "Tente novamente; se persistir, peça ao administrador."
    )
    return f"{literal(str(exc))} — {action} (código: {exc.code})"


def explain_any(exc) -> str:
    return explain(exc) if isinstance(exc, APIError) else TRANSPORT_RECOVERY


KB_CONFIRMATION_CONTEXTS = (
    "kb_build_confirmation_context",
    "kb_test_confirmation_context",
    "kb_activate_confirmation_context",
    "kb_rollback_confirmation_context",
)


def kb_unconfirmed(action):
    """A KB write whose answer was lost: drop the estimate and every confirmation, never repeat it blindly (P3-05)."""
    st.session_state.pop("kb_estimate", None)
    for key in KB_CONFIRMATION_CONTEXTS:
        st.session_state[key] = None
    st.session_state.admin_notice = (
        f"{action}: o backend não confirmou a resposta e pode ter concluído a operação. A estimativa e as "
        "confirmações foram descartadas; recarregue a página para consultar as versões (GET) antes de repetir."
    )
    st.rerun()


def open_runs(request_ids):
    """Read recorded runs by GET (no cost) and select the first one in Resultados."""
    opened = []
    for request_id in request_ids:
        try:
            remember_run(request_id, api.run(request_id) | {"request_id": request_id})
            opened.append(request_id)
        except APIError as exc:
            st.error(f"Execução {request_id} indisponível: {explain(exc)}")
        except httpx.TransportError:
            st.warning(TRANSPORT_RECOVERY)
    if opened:
        st.session_state.pending_run_selection = opened[0]
        st.rerun()


PROFILE_NAMES = {"reference": "Referência", "hybrid": "Híbrido", "adjusted": "Ajustado"}
PROFILE_ORDER = {"reference": 0, "hybrid": 1, "adjusted": 2}


def literal(text):
    """Markdown with literal dollar signs: "R$ 80,00 … R$ 148,06" must not become a formula (P3-QA-02)."""
    return (text or "").replace("$", "\\$")


def profile_name(item):
    return PROFILE_NAMES.get(item["profile_id"], item["name"])


# v1.1.0 (backend task 111, D3): what this backend can run, from GET /v1/profiles. A backend before v1.1.0 sends no
# availability fields: every profile and both Routers stay available, as before.
PROVIDER_KEYS = {"openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY", "typesafe": "TYPESAFE_API_KEY"}
UNAVAILABLE_HERE = "indisponível neste backend"
JEV_UNAVAILABLE = "JEV indisponível: falta `TYPESAFE_API_KEY` no backend."


def load_profiles(api):
    """Profiles and whether the JEV is available. Simulated and recorded modes have no availability: all available."""
    listing = getattr(api, "profile_listing", None)
    if listing is None:
        return api.profiles(), True
    body = listing()
    mechanisms = body.get("router_mechanisms")
    return body["profiles"], not (isinstance(mechanisms, dict) and mechanisms.get("jev") is False)


def profile_available(item):
    return item.get("available") is not False


def profile_unavailable_note(item):
    providers = item.get("missing_providers") or ()
    keys = sorted({PROVIDER_KEYS.get(provider, f"{provider.upper()}_API_KEY") for provider in providers})
    missing = " e ".join(f"`{key}`" for key in keys) or "a chave de um provedor"
    return f"Perfil {profile_name(item)} indisponível: {'faltam' if len(keys) > 1 else 'falta'} {missing} no backend."


def keep_llm_without_jev(key, jev_available):
    """A Router choice left on JEV goes back to LLM when this backend has no JEV (before its widget is drawn)."""
    if not jev_available and st.session_state.get(key) == "jev":
        st.session_state[key] = "llm"


def money(value):
    return f"{float(value):.6f}" if value is not None else "desconhecido"


def usd(value):
    return f"US$ {float(value):.6f}" if value is not None else "desconhecido"


def reset_confirmation_context(context_key, context, *widget_keys):
    if st.session_state.get(context_key) != context:
        for widget_key in widget_keys:
            st.session_state[widget_key] = False
        st.session_state[context_key] = context


def ensure_conversation_id():
    if not st.session_state.conversation_id:
        st.session_state.conversation_id = f"conv_{uuid.uuid4().hex[:20]}"
    return st.session_state.conversation_id


def remember_run(request_id, run):
    st.session_state.runs = {request_id: run} | {
        key: value for key, value in st.session_state.runs.items() if key != request_id
    }


def send_chat_message(api, payload, message_text):
    is_real = api.execution_mode == "real"
    conversation_id = ensure_conversation_id() if is_real else st.session_state.conversation_id
    pending = {
        "conversation_id": conversation_id,
        "message": message_text,
        "baseline_message_count": st.session_state.backend_message_count,
    }
    if is_real:
        st.session_state.pending_message = pending
    try:
        result = api.send_message(payload | {"conversation_id": conversation_id})
    except httpx.TransportError:
        return False
    except APIError:
        st.session_state.pending_message = None
        raise

    reply = result["message"]
    st.session_state.conversation_id = reply.get("conversation_id") or conversation_id
    st.session_state.chat_history.extend(
        [
            {"role": "user", "text": message_text},
            assistant_turn(reply["answer"], reply),
        ]
    )
    request_id = reply.get("request_id")
    if request_id and result.get("run"):
        st.session_state.latest_run = result["run"] | {"request_id": request_id}
        stored = st.session_state.latest_run | {"result": reply}
        reader = getattr(api, "run", None)
        if reader is not None:  # a GET (no cost) adds the steps, checks and evaluations of the persisted run
            try:
                stored = reader(request_id) | stored
            except (APIError, httpx.TransportError):
                pass
        remember_run(request_id, stored)
        st.session_state.pending_run_selection = request_id
    st.session_state.case_view = {
        "status": reply.get("status"),
        "route": reply.get("route"),
        "state": (reply.get("resolution") or {}).get("state"),
        "protocol": (reply.get("handoff") or {}).get("protocol"),
        "knowledge_base_version_id": (result.get("run") or {}).get("knowledge_base_version_id"),
    }
    if is_real:
        st.session_state.backend_message_count += 2
        st.session_state.pending_message = None
    st.session_state.recovered_without_request_id = False
    st.session_state.comparison = None
    st.session_state.resumed = None  # the notice of a resumed conversation lasts until its next message
    return True


def is_operator_turn(item):
    """A reply written or approved by a human attendant (spec v0.7.0, D8): via=operator, route human_reply."""
    return item.get("via") == "operator" or item.get("route") == "human_reply"


def assistant_turn(text, reply):
    """The assistant turn kept in the chat. `reply` is the `message` object of a /v1/messages answer
    (`{"message": {..., "via": ...}, "run": ...}`, MessageResponse in the OpenAPI) or a turn of
    GET /v1/conversations/{id}; the latter also carries operator_action and protocol, kept for the label."""
    turn = {"role": "assistant", "text": text}
    if is_operator_turn(reply):
        turn["via"] = "operator"
        action = reply.get("operator_action")
        protocol = reply.get("protocol") or (reply.get("handoff") or {}).get("protocol")
        if action:
            turn["operator_action"] = action
        if protocol:
            turn["protocol"] = protocol
    return turn


def attendant_label(turn):
    parts = ["Atendente humano", "rota human_reply"]
    if turn.get("operator_action"):
        parts.append(f"ação {ATTENDANT_ACTIONS.get(turn['operator_action'], turn['operator_action'])}")
    if turn.get("protocol"):
        parts.append(f"protocolo {turn['protocol']}")
    return " · ".join(parts)


def find_pending_reply(conversation, pending):
    """The reply to the pending question and the backend message count the chat has now consumed, or None.

    The reply is the first assistant turn after the question. Spec v0.7.0 D8 records a human reply BEFORE the
    merchant message that delivers it ([merchant, assistant(agent), assistant(operator), merchant]), so when no
    assistant follows the question, the last operator turn between the baseline and the question is the reply. The
    returned count ends at the reply (or at the question), so a turn already shown is never taken again."""
    messages = conversation.get("messages")
    baseline = pending["baseline_message_count"]
    if not isinstance(messages, list) or baseline < 0 or baseline > len(messages):
        return None

    new_messages = messages[baseline:]
    merchant_index = next(
        (index for index, item in enumerate(new_messages) if item.get("role") == "merchant"),
        None,
    )
    if merchant_index is None or new_messages[merchant_index].get("text") != pending["message"]:
        return None

    for index in range(merchant_index + 1, len(new_messages)):
        role = new_messages[index].get("role")
        if role == "merchant":
            break
        if role == "assistant":
            return new_messages[index], baseline + index + 1
    delivered = [
        item for item in new_messages[:merchant_index] if item.get("role") == "assistant" and is_operator_turn(item)
    ]
    return (delivered[-1], baseline + merchant_index + 1) if delivered else None


RESUME_ROLES = ("merchant", "demo")  # roles whose token has a merchant of its own (backend v1.1.0, D2)
RESUME_FAILED = "Não foi possível retomar a última conversa. Uma conversa nova foi iniciada."
RESUME_PENDING_REPLY = (
    "A resposta da atendente ainda não tinha sido entregue nesta conversa: ela volta como resposta da sua próxima "
    "mensagem."
)


def stamp(value):
    """An ISO date of the backend as dd/mm/aaaa hh:mm; anything else as it came."""
    try:
        return datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return literal(str(value or "—"))


def history_from_conversation(conversation):
    """The chat as GET /v1/conversations/{id} records it: the merchant's turns and the assistant's, these with the
    attendant's marks (via, action, protocol) when a human wrote them."""
    turns = []
    for item in conversation.get("messages") or []:
        if item.get("role") == "merchant":
            turns.append({"role": "user", "text": item.get("text") or ""})
        elif item.get("role") == "assistant":
            turns.append(assistant_turn(item.get("text") or "", item))
    return turns


def resume_last_conversation(api):
    """After a login with no conversation in the session, brings back the merchant's latest one (GET only, no cost):
    the list gives its id, the conversation by id its history, and the next message goes to the same id. No
    conversation keeps the current behaviour; a failure says so briefly and leaves a new conversation."""
    st.session_state.resume_checked = True
    try:
        latest = next(iter(api.list_conversations(limit=1)), None)
        if latest is None:
            return
        conversation = api.conversation(latest["conversation_id"])
        messages = list(conversation["messages"])
        history = history_from_conversation(conversation)
    except APIError as exc:
        if session_ended(exc):
            end_session(SESSION_EXPIRED)
            st.rerun()
        st.session_state.resume_notice = RESUME_FAILED
        return
    except (httpx.TransportError, KeyError, TypeError, AttributeError):
        st.session_state.resume_notice = RESUME_FAILED
        return
    last = next((item for item in reversed(messages) if item.get("role") == "assistant"), {})
    st.session_state.update(
        chat_history=history,
        conversation_id=latest["conversation_id"],
        backend_message_count=len(messages),
        case_view={
            "status": last.get("status"),
            "route": last.get("route"),
            "state": conversation.get("state"),
            "protocol": latest.get("handoff_protocol"),
            "knowledge_base_version_id": None,
        },
        resumed={
            "conversation_id": latest["conversation_id"],
            "updated_at": latest.get("updated_at"),
            "pending_operator_reply": bool(latest.get("pending_operator_reply")),
        },
    )


def start_new_conversation():
    """Nova conversa: the chat of this session starts over; nothing is deleted at the backend, and the runs of this
    session stay in Resultados."""
    st.session_state.update(
        chat_history=[],
        conversation_id=None,
        backend_message_count=0,
        pending_message=None,
        recovered_without_request_id=False,
        case_view=None,
        comparison=None,
        resumed=None,
        resume_notice=None,
        resume_checked=True,  # a new conversation is a choice: it is not replaced by the latest one again
    )


def load_scenarios(api):
    loader = getattr(api, "scenarios", None)
    if loader is None:
        return [], None
    try:
        return loader(), None
    except APIError as exc:
        return [], explain(exc)
    except httpx.TransportError:
        return [], TRANSPORT_RECOVERY


def render_agent_configuration(initial_agents, model_catalog, key_prefix):
    configured = {}
    for agent, current in initial_agents.items():
        st.markdown(f"**{agent.title()}**")
        choices = [
            item
            for item in model_catalog
            if agent in item.get("agents", []) and not (agent == "router" and item["provider"] == "typesafe")
        ]
        if not choices:
            st.warning(f"Nenhum modelo compatível foi retornado para {agent}.")
            configured[agent] = current
            continue
        labels = [f"{item['provider']}:{item['model']}" for item in choices]
        current_label = f"{current['provider']}:{current['model']}"
        selected_index = labels.index(current_label) if current_label in labels else 0
        selected = st.selectbox(
            "Provedor e modelo",
            labels,
            index=selected_index,
            key=f"{key_prefix}_{agent}_model",
        )
        model = choices[labels.index(selected)]
        parameters = {}
        output_limit = model.get("parameters", {}).get("max_output_tokens")
        maximum = output_limit.get("maximum_by_agent", {}).get(agent) if output_limit else None
        if agent == "evaluation":
            st.caption("Evaluation: campo preservado no perfil, não usado no atendimento; parâmetros não configuráveis.")
        elif output_limit and maximum:
            existing = current.get("parameters", {}).get("max_output_tokens")
            enabled = st.checkbox(
                "Definir limite de tokens de saída",
                value=existing is not None,
                key=f"{key_prefix}_{agent}_output_enabled",
            )
            value = min(max(int(existing or maximum), output_limit["minimum"]), maximum)
            tokens = st.number_input(
                "Limite de saída",
                min_value=output_limit["minimum"],
                max_value=maximum,
                value=value,
                step=16,
                help="Reduz o limite atual do agente; não pode ampliá-lo nem sua reserva de orçamento.",
                disabled=not enabled,
                key=f"{key_prefix}_{agent}_output_tokens",
            )
            if enabled:
                parameters["max_output_tokens"] = int(tokens)
                st.caption(f"[{model['provider']} · documentação oficial]({output_limit['documentation_url']})")
        configured[agent] = {
            "provider": model["provider"],
            "model": model["model"],
            "parameters": parameters,
        }
    return configured


def render_checks(checks):
    st.markdown("**Verificações determinísticas**")
    if not checks or checks.get("status") == "unavailable":
        st.info("Verificações indisponíveis para esta execução (trace ou resposta ausente).")
        return
    origin = checks.get("origin") or ""
    if origin.startswith("scenario:"):
        st.caption(f"Origem: rótulos do cenário {origin.removeprefix('scenario:')}.")
    else:
        st.caption("Origem: mensagem livre, sem rótulo; só verificações independentes de rótulo.")
    rows = [
        {"verificação": key, "resultado": "aprovada" if value else "reprovada"}
        for key, value in checks.get("results", {}).items()
    ]
    if rows:
        st.dataframe(rows, hide_index=True)
    if checks.get("not_evaluated"):
        st.caption("Não avaliado (sem rótulo aplicável): " + ", ".join(checks["not_evaluated"]))
    for failure in checks.get("critical_failures", []):
        st.error(f"Falha crítica: {failure['code']} (verificação {failure.get('check')})")
    st.caption(f"Aprovadas: {checks.get('passed', 0)} de {checks.get('total', 0)}.")


def render_evaluations(evaluations):
    st.markdown("**Avaliação por IA (sob demanda)**")
    if not evaluations:
        st.caption("Não avaliado.")
        return
    for item in evaluations:
        evaluator = item.get("evaluator", {})
        header = (
            f"{item.get('state')} · rubrica {item.get('rubric')} v{item.get('rubric_version')} · "
            f"avaliador {evaluator.get('provider')}:{evaluator.get('model')} {evaluator.get('parameters', {})} · "
            f"custo da avaliação {usd(item.get('cost_usd'))} (separado do atendimento)"
        )
        st.write(header)
        if item.get("state") == "completed":
            result = item.get("result", {})
            st.write(
                f"Responde à pergunta {result.get('answers_question')} · clareza e tom "
                f"{result.get('clarity_and_tone')} · honestidade {result.get('honesty')} — {literal(result.get('comment'))}"
            )
        elif item.get("state") == "running":
            st.caption("Avaliação em andamento; atualize a execução (GET) para ver o resultado.")
        else:
            st.caption("A avaliação falhou e não gerou notas.")
        for failure in item.get("critical_failures", []):
            st.error(f"Falha crítica registrada: {failure['code']}")


def render_run(run):
    st.write(f"**{MODE_LABELS.get(run.get('execution_mode'), run.get('execution_mode', 'modo desconhecido'))}**")
    result = run.get("result") or {}
    left, middle, right = st.columns(3)
    left.metric("Rota", run.get("route") or result.get("route") or "—")
    duration = run.get("duration_ms")
    middle.metric("Duração", f"{duration} ms" if duration is not None else "—")
    cost = run.get("cost_estimate_usd")
    right.metric("Custo estimado", f"US$ {float(cost):.6f}" if cost is not None else "—")
    st.write(
        f"Perfil `{run.get('profile_id')}` v{run.get('profile_version')} · Router `{run.get('router_mechanism')}` · "
        f"base `{run.get('knowledge_base_version_id') or '—'}` · cenário `{run.get('scenario_id') or 'livre'}` · "
        f"finalidade `{run.get('purpose') or 'attendance'}`"
    )
    prompts = run.get("prompts_used") or [f"{key}@{value}" for key, value in (run.get("prompt_versions") or {}).items()]
    st.caption("Prompts: " + (", ".join(prompts) or "—"))
    usage_rows = [
        {
            "agente": agent,
            "modelos": ", ".join(usage.get("models", [])),
            "chamadas": usage.get("calls"),
            "tokens de entrada": usage.get("input_tokens"),
            "tokens de saída": usage.get("output_tokens"),
            "custo (US$)": usage.get("cost_usd"),
        }
        for agent, usage in (run.get("models_by_agent") or {}).items()
    ]
    if usage_rows:
        st.markdown("**Modelo efetivo, tokens e custo por agente**")
        st.dataframe(usage_rows, hide_index=True)
    steps = run.get("steps") or []
    if steps:
        st.markdown("**Etapas**")
        st.dataframe(
            [
                {
                    "#": step["seq"],
                    "etapa": step["step"],
                    "status": step["status"],
                    "ms": step["duration_ms"],
                    "prompt": step.get("prompt", ""),
                }
                for step in steps
            ],
            hide_index=True,
        )
    st.caption("Ferramentas: " + (", ".join(run.get("tools") or []) or "nenhuma"))
    sources = run.get("sources") or []
    if sources:
        st.caption("Fontes: " + literal("; ".join(f"{item.get('title')} ({item.get('type')})" for item in sources)))
    hits = [hit for step in steps for hit in step.get("hits", [])]
    if hits:
        st.markdown("**Trechos recuperados**")
        for hit in hits:
            st.caption(f"{hit.get('doc')} · {hit.get('chunk_id')} · score {hit.get('score')}")
            st.text(hit.get("excerpt", ""))
    render_checks(run.get("checks"))
    render_evaluations(run.get("evaluations") or [])


def render_comparison(result):
    st.write(f"Comparação `{result['comparison_id']}` · estado **{result['state']}**")
    for failure in result.get("failures", []):
        st.warning(literal(f"Falha registrada: {failure.get('code')} {failure.get('message', '')}".strip()))
    aggregates = result.get("aggregates") or {}
    if aggregates.get("exploratory"):
        st.warning("Exploratória: os perfis diferem em mais de um agente; a diferença não isola uma troca de modelo.")
    profiles = aggregates.get("profiles", [])
    st.caption(
        "Qualidade = taxa de verificações determinísticas aprovadas sob os mesmos rótulos. Nota do avaliador por IA "
        "só aparece quando todas as execuções do perfil foram avaliadas com a mesma configuração."
    )
    plotted = [
        item
        for item in profiles
        if item.get("deterministic_quality") is not None and item.get("sample_size") and item.get("cost_mean_usd") is not None
    ]
    for item in profiles:
        if item not in plotted:
            reason = "qualidade não avaliada" if item.get("deterministic_quality") is None else "custo desconhecido"
            st.caption(f"{item['profile_id']}: fora do gráfico ({reason}).")
    if plotted:
        frame = pd.DataFrame(
            [
                {
                    "perfil": f"{item['profile_id']} v{item['profile_version']}",
                    "custo médio (US$)": item["cost_mean_usd"],
                    "qualidade": item["deterministic_quality"],
                    "rótulo": f"{item['profile_id']} · n={item['sample_size']} · {item['duration_mean_ms']} ms",
                    "duração média (ms)": item["duration_mean_ms"],
                    "amostra": item["sample_size"],
                }
                for item in plotted
            ]
        )
        points = (
            alt.Chart(frame)
            .mark_circle(size=220)
            .encode(
                x="custo médio (US$):Q",
                y=alt.Y("qualidade:Q", scale=alt.Scale(domain=[0, 1])),
                color=alt.Color("rótulo:N", title="perfil · amostra · duração média"),
                tooltip=["perfil", "custo médio (US$)", "qualidade", "duração média (ms)", "amostra"],
            )
        )
        st.markdown("**Custo × qualidade por perfil** (duração média e amostra na legenda)")
        st.altair_chart(points, width="stretch")
        point_labels = [f"{item['profile_id']} v{item['profile_version']}" for item in plotted]
        point = st.selectbox("Ponto selecionado (perfil)", point_labels, key="cmp_point_choice")
        chosen = plotted[point_labels.index(point)]
        point_runs = [
            cell["request_id"]
            for cell in result.get("cells", [])
            if cell.get("request_id")
            and (cell["profile_id"], cell["profile_version"]) == (chosen["profile_id"], chosen["profile_version"])
        ]
        st.caption("Execuções deste ponto: " + (", ".join(point_runs) or "nenhuma"))
        if st.button("Abrir execuções deste ponto em Resultados", key="cmp_open_point", disabled=not point_runs):
            open_runs(point_runs)
    st.dataframe(
        [
            {
                "perfil": f"{item['profile_id']} v{item['profile_version']}",
                "amostra": f"{item['sample_size']} de {item['planned']}",
                "qualidade determinística": item.get("deterministic_quality"),
                "nota do avaliador": ", ".join(f"{k} {v}" for k, v in (item.get("model_quality") or {}).items())
                or "não avaliado",
                "falhas críticas": item.get("critical_failures"),
                "custo médio (US$)": money(item.get("cost_mean_usd")),
            }
            for item in profiles
        ],
        hide_index=True,
    )
    bars = aggregates.get("cost_by_agent_model", [])
    if bars:
        def bar_cost(bar):  # a bar whose cost is (partly) unknown is never drawn or labelled as a number
            return None if bar.get("cost_unknown") else bar.get("cost_usd")

        st.markdown("**Custo por agente e modelo**")
        known = [bar for bar in bars if bar_cost(bar) is not None]
        if known:
            frame = pd.DataFrame(
                [
                    {
                        "agente/modelo": f"{bar['agent']} · {bar['model']}",
                        "perfil": f"{bar['profile_id']} v{bar['profile_version']}",
                        "custo (US$)": bar_cost(bar),
                    }
                    for bar in known
                ]
            )
            chart = alt.Chart(frame).mark_bar().encode(x="custo (US$):Q", y="agente/modelo:N", color="perfil:N")
            st.altair_chart(chart, width="stretch")
        for bar in bars:
            if bar_cost(bar) is None:
                st.caption(
                    f"{bar['profile_id']} v{bar['profile_version']} · {bar['agent']} · {bar['model']}: "
                    "fora do gráfico (custo desconhecido)."
                )
        labels = [
            f"{bar['profile_id']} v{bar['profile_version']} · {bar['agent']} · {bar['model']} · "
            + (usd(bar_cost(bar)) if bar_cost(bar) is not None else "custo desconhecido")
            for bar in bars
        ]
        choice = st.selectbox("Barra selecionada", labels, key="cmp_bar_choice")
        selected = bars[labels.index(choice)]
        st.caption("Execuções desta barra: " + ", ".join(selected["request_ids"]))
        if st.button("Abrir execuções desta barra em Resultados", key="cmp_open_runs"):
            open_runs(selected["request_ids"])
    st.markdown("**Células**")
    st.dataframe(
        [
            {
                "cenário": cell["scenario_id"],
                "perfil": f"{cell['profile_id']} v{cell['profile_version']}",
                "rep.": cell.get("repetition"),
                "origem": cell.get("origin") or cell.get("source"),
                "resultado": cell.get("status") + (f" ({cell['error']})" if cell.get("error") else ""),
                "verificações": f"{(cell.get('checks') or {}).get('passed', 0)}/{(cell.get('checks') or {}).get('total', 0)}",
                "duração (ms)": cell.get("duration_ms"),
                "custo (US$)": money(cell.get("cost_usd")),
                "execução": cell.get("request_id"),
            }
            for cell in result.get("cells", [])
        ],
        hide_index=True,
    )
    openable = [cell for cell in result.get("cells", []) if cell.get("request_id")]
    if openable:
        cell_labels = [
            f"{cell['scenario_id']} · {cell['profile_id']} v{cell['profile_version']} · rep. {cell.get('repetition')} · "
            f"{cell.get('status')} · {cell['request_id']}"
            for cell in openable
        ]
        cell_choice = st.selectbox("Célula selecionada", cell_labels, key="cmp_cell_choice")
        if st.button("Abrir a execução desta célula em Resultados", key="cmp_open_cell"):
            open_runs([openable[cell_labels.index(cell_choice)]["request_id"]])


SESSION_KEYS = ("api_token", "api_role", "api_principal", "api_token_exp", "api_refresh_token", "api_base_url")
TICKET_STATE_KEYS = (
    "ticket_attempt",
    "ticket_notice",
    "ticket_confirm_context",
    "ticket_solve_confirm_context",
)
# Per-user state beyond the token: the conversation, its runs and the analyses started from them. It belongs to
# `conversation_owner` (the `sub` of the token that created it) and must never reach another principal. Besides the
# defaults below, these keys hold what the user typed, picked, confirmed or estimated; they are dropped with the
# conversation. Streamlit keeps the state of input widgets across the login screen (the script stops before them), and
# one without a `key` gets an automatic id that survives too: so every input widget of this file has a key listed here
# (or one built from a prefix covered by `is_user_scratch`), except the few left by design in the audit test
# (`tests/test_v070_interface.py`): the backend URL, the mode and the simulated role.
USER_SCRATCH_KEYS = (
    # Atendimento and Resultados
    "message_input",
    "compare_input",
    "profile",
    "scenario_choice",
    "comparison_selection",
    "results_run_choice",
    "eval_confirm",
    "eval_repeat",
    "cmp_profiles",
    "cmp_scenarios",
    "cmp_repetitions",
    "cmp_mechanism",
    "cmp_confirm",
    "cmp_bar_choice",
    "cmp_point_choice",
    "cmp_cell_choice",
    # Administração
    "profile_draft_name",
    "profile_allow_demo",
    "profile_draft_choice",
    "kb_document_choice",
    "kb_candidate_choice",
    "kb_build_max_cost",
    "kb_test_max_cost",
    "kb_confirm_build",
    "kb_confirm_test",
    "kb_confirm_activate",
    "kb_confirm_rollback",
    # Ticketing (the operator's draft, queue, ticket, action and confirmations)
    "ticket_status",
    "ticket_choice",
    "ticket_action",
    "ticket_confirm",
    "ticket_solve_confirm",
    # estimates, confirmation contexts and notices derived from the above
    "eval_confirm_context",
    "cmp_confirm_context",
    "kb_estimate",
    "admin_notice",
    *KB_CONFIRMATION_CONTEXTS,
)


def is_user_scratch(key):
    """Widget keys built at run time: the Router choice of each profile, the ticket texts, the knowledge-base editor
    of each document and the admin profile forms (`create_*`/`update_*`, built by `render_agent_configuration`)."""
    return (
        key in USER_SCRATCH_KEYS
        or key.startswith(("router_choice_", "ticket_text_", "kb_draft_text_", "kb_source_text_"))
        or (
            key.startswith(("create_", "update_"))
            and key.endswith(("_model", "_output_enabled", "_output_tokens"))
        )
    )


def conversation_defaults():
    return {
        "chat_history": [],
        "conversation_id": None,
        "backend_message_count": 0,
        "pending_message": None,
        "recovered_without_request_id": False,
        "latest_run": None,
        "runs": {},
        "case_view": None,
        "comparison": None,
        "evaluation_estimate": None,
        "comparison_estimate": None,
        "profile_comparison_id": None,
        "profile_comparison": None,
        "pending_run_selection": None,
        "resume_checked": False,  # the latest conversation is looked up once per login (v1.1.0)
        "resumed": None,
        "resume_notice": None,
    }


def forget_conversation():
    """Drop the previous user's conversation, runs and analyses (P2-1)."""
    st.session_state.update(conversation_defaults())
    for key in [key for key in st.session_state if isinstance(key, str) and is_user_scratch(key)]:
        st.session_state.pop(key, None)
    st.session_state.conversation_owner = None


def end_session(notice=None, forget=False):
    """Forget the token and every client bound to it; the next run shows the login form.

    An expiry keeps the conversation, so the same principal can come back to it; a logout (`forget`) drops it."""
    close_session_client(st.session_state)
    close_ticketing_client(st.session_state)
    for key in (*SESSION_KEYS, *TICKET_STATE_KEYS):
        st.session_state[key] = None
    st.session_state.token_rotation = {}
    st.session_state.oauth_attempt = None  # the next login starts a new attempt
    st.session_state.oauth_outcome = None
    if forget:
        forget_conversation()
    st.session_state.login_notice = notice


def ticket_notice(kind, text):
    st.session_state.ticket_notice = {"kind": kind, "text": text}


def ticket_failure(exc):
    """Map a ticketing failure to a message with its recovery action. Returns True when the session ended."""
    if session_ended(exc):
        end_session(SESSION_EXPIRED)
        return True
    if isinstance(exc, APIError):
        ticket_notice("error", explain(exc))
    else:
        ticket_notice(
            "warning",
            "O ticketing não confirmou o envio (falha de conexão). Clique de novo no mesmo botão para reenviar a "
            "mesma ação com a mesma chave, ou use Atualizar para conferir o estado.",
        )
    return False


def ticket_outcome(result, done_text):
    delivery = (result or {}).get("delivery") or {}
    if delivery.get("status") == "delivered":
        ticket_notice("success", done_text)
        return True
    ticket_notice(
        "warning",
        "Envio pendente: o ticketing guardou a ação e vai tentar de novo com o assistente. Use Atualizar para "
        "acompanhar; clicar de novo reaproveita a mesma chave e não cria outra ação.",
    )
    return False


def render_ticket_detail(detail):
    status = detail.get("status")
    st.subheader(f"{detail.get('protocol') or detail['ticket_id']} · {STATUS_LABELS.get(status, status)}")
    st.caption(
        f"Ticket {detail['ticket_id']} · caso {detail.get('case_id')} · lojista {detail.get('customer_id') or '—'} · "
        f"idioma {detail.get('language') or '—'} · motivo {detail.get('reason') or '—'} · responsável "
        f"{detail.get('assignee') or '—'} · caso no assistente {detail.get('case_state') or '—'} · fila "
        f"{detail.get('queue_state') or '—'}"
    )
    delivery = detail.get("delivery") or {}
    if delivery.get("status") in {"pending", "failed"}:
        st.warning(
            f"Envio pendente ({delivery.get('event_type')}, {delivery.get('attempts')} tentativa(s), estado "
            f"{delivery.get('status')}). Use Atualizar para acompanhar."
        )
    if detail.get("reply_delivered_at"):
        st.success(f"Resposta entregue ao lojista em {detail['reply_delivered_at']}.")
        if detail.get("reply_text"):
            st.markdown(literal(detail["reply_text"]))
    if detail.get("closed_at"):
        st.info(f"Caso fechado no assistente em {detail['closed_at']} por {detail.get('closed_by') or '—'}.")
    st.markdown(f"**Resumo:** {literal(detail.get('summary') or '—')}")
    st.markdown(f"**Pergunta pendente:** {literal(detail.get('pending_question') or '—')}")
    st.markdown(f"**Rascunho do atendente:** {literal(detail.get('draft_reply') or '—')}")
    with st.expander("Conversa (mascarada)", expanded=False):
        for turn in detail.get("transcript") or []:
            with st.chat_message("user" if turn.get("role") in {"user", "merchant"} else "assistant"):
                st.caption(turn.get("at") or "")
                st.markdown(literal(turn.get("text")))
    with st.expander("Histórico do ticket", expanded=False):
        st.dataframe(
            [
                {
                    "quando": event.get("at"),
                    "evento": event.get("kind"),
                    "de": event.get("from_status"),
                    "para": event.get("to_status"),
                    "por": event.get("actor"),
                }
                for event in detail.get("events") or []
            ],
            hide_index=True,
        )
    with st.expander("Entregas nos dois sentidos", expanded=False):
        deliveries = detail.get("deliveries") or {}
        st.caption("Recebidas do assistente")
        st.dataframe(deliveries.get("received") or [], hide_index=True)
        st.caption("Enviadas ao assistente")
        st.dataframe(
            [
                {
                    "evento": sent.get("event_type"),
                    "estado": sent.get("status"),
                    "resposta": sent.get("response_status"),
                    "tentativas": len(sent.get("attempts") or []),
                    "chave": sent.get("idempotency_key"),
                }
                for sent in deliveries.get("sent") or []
            ],
            hide_index=True,
        )


def render_ticket_actions(client, detail):
    ticket_id, status = detail["ticket_id"], detail.get("status")
    if status == "solved":
        st.info("Ticket resolvido. Nenhuma ação disponível.")
        return
    if status == "new" and st.button("Assumir", key="ticket_assign"):
        try:
            ticket_outcome(client.assign(ticket_id), "Ticket assumido. Ele aparece na fila Atendimento.")
        except (APIError, httpx.TransportError) as exc:
            ticket_failure(exc)
        st.rerun()
    if status not in {"open", "pending"}:
        return

    st.markdown("**Ação do atendente**")
    action = st.radio("Ação", list(ACTION_LABELS), format_func=ACTION_LABELS.get, horizontal=True, key="ticket_action")
    reset_confirmation_context("ticket_confirm_context", (ticket_id, action), "ticket_confirm")
    if action == "approve":
        st.caption("Aprovar envia ao lojista o rascunho mostrado acima, sem alterações.")
        text = ""
    else:
        label = "Resposta ao lojista" if action == "reply" else "Instrução para o assistente refazer a resposta"
        text = st.text_area(label, key=f"ticket_text_{action}", height=90)
    confirmed = st.checkbox("Confirmo o envio desta ação ao assistente", key="ticket_confirm")
    ready = confirmed and (action == "approve" or bool(text.strip()))
    if st.button("Enviar ação", key="ticket_send", disabled=not ready):
        signature = [ticket_id, action, text]
        attempt = st.session_state.ticket_attempt
        key = attempt["key"] if attempt and attempt["signature"] == signature else new_idempotency_key()
        st.session_state.ticket_attempt = {"signature": signature, "key": key}
        try:
            if ticket_outcome(client.act(ticket_id, action, text, key), "Ação enviada e aceita pelo assistente."):
                st.session_state.ticket_attempt = None
            st.session_state.ticket_confirm_context = None
        except httpx.TransportError as exc:
            ticket_failure(exc)  # keep the attempt: the next click resends the same key
        except APIError as exc:
            if exc.code != "invalid_response":  # an unreadable 2xx: the outcome is unknown, keep the key
                st.session_state.ticket_attempt = None
                st.session_state.ticket_confirm_context = None
            ticket_failure(exc)
        st.rerun()

    st.markdown("**Finalização**")
    reset_confirmation_context("ticket_solve_confirm_context", ticket_id, "ticket_solve_confirm")
    solve_confirmed = st.checkbox("Confirmo que o atendimento terminou", key="ticket_solve_confirm")
    if st.button("Resolver", key="ticket_solve", disabled=not solve_confirmed):
        try:
            ticket_outcome(client.solve(ticket_id), "Ticket resolvido; o caso foi fechado no assistente.")
        except (APIError, httpx.TransportError) as exc:
            ticket_failure(exc)
        st.rerun()


def render_ticketing(token):
    url = ticketing_url()
    client = get_ticketing_client(st.session_state, url, token)
    st.caption(f"Serviço de ticketing: {url} · operador {st.session_state.api_principal or '—'}")
    notice = st.session_state.ticket_notice
    if notice:
        getattr(st, notice["kind"])(notice["text"])
        st.session_state.ticket_notice = None
    status = st.radio("Fila", STATUSES, format_func=STATUS_LABELS.get, horizontal=True, key="ticket_status")
    if st.button("Atualizar", key="ticket_refresh"):
        st.rerun()
    try:
        tickets = client.tickets(status)
        if not tickets:
            st.info(f"Nenhum ticket na fila {STATUS_LABELS[status]}.")
            return
        labels = {
            f"{item.get('protocol') or item['ticket_id']} · {item.get('customer_id') or '—'} · "
            f"{(item.get('summary') or '')[:60]}": item["ticket_id"]
            for item in tickets
        }
        choice = st.selectbox("Ticket", list(labels), key="ticket_choice")
        detail = client.ticket(labels[choice])
    except APIError as exc:
        if session_ended(exc):
            end_session(SESSION_EXPIRED)
            st.rerun()
        st.error(f"Não foi possível ler o ticketing: {explain(exc)}")
        return
    except httpx.TransportError:
        st.error("Ticketing inacessível. Confira TICKETING_URL e se o serviço está em execução; depois use Atualizar.")
        return
    render_ticket_detail(detail)
    render_ticket_actions(client, detail)


# When the browser asks for a rerun (a repeated delivery of the redirect, a click), Streamlit (1.66, fast reruns on
# by default) starts a new run at once and only asks the running one to stop, which it does at its next yield point
# (any session-state read or write, any `st` call). Two runs of the same session may therefore overlap, and a run may
# die right after the token endpoint answered. So:
# - the token steps of a run (refresh, code exchange, installing, logout) hold the session's `token_lock`: a new run
#   waits there while an earlier one is still waiting for the token endpoint, instead of acting on half-written state;
# - an answer of the token endpoint is kept, right after the call and before any yield point, in a plain dict the
#   session already holds (`oauth_outcome`, `token_rotation`); the session state is written from it afterwards, by
#   that run or, if it was stopped, by the next one. Installing is safe to repeat; a code is never exchanged twice.
TOKEN_LOCK_SECONDS = DEFAULT_TIMEOUT_SECONDS + 10  # longer than any token call; never a deadlock if exceeded


@contextmanager
def token_flow():
    """Holds the session's token lock for the token steps of this run. If another run keeps it past the limit, this
    run touches no token state (it could spend or orphan a login): it says so and stops."""
    lock = st.session_state.token_lock
    if not lock.acquire(timeout=TOKEN_LOCK_SECONDS):
        st.sidebar.warning(SESSION_BUSY)
        st.stop()
    try:
        yield
    finally:
        lock.release()


def install_token_response(session, base_url, *, expected_principal=None, received=None):
    """Keeps the tokens of a code exchange or a refresh in this Streamlit session (never on the page). `api_token` is
    written last, and `received` (the plain dict that kept the answer) is marked installed right after it, with no
    yield point between the two: a run stopped earlier leaves the session closed and the answer still to install. A
    refresh that answers for another principal raises ValueError before anything is kept."""
    access, next_refresh = session.get("access_token"), session.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(next_refresh, str) or not next_refresh:
        raise ValueError("Incomplete token response")
    claims = token_claims(access)
    principal = claims.get("sub")
    if expected_principal is not None and principal != expected_principal:
        raise ValueError("Principal changed during refresh")
    if not principal or principal != st.session_state.conversation_owner:
        forget_conversation()
    st.session_state.conversation_owner = principal
    st.session_state.api_refresh_token = next_refresh
    st.session_state.api_role = claims.get("role")
    st.session_state.api_principal = principal
    expires_in = session.get("expires_in")
    st.session_state.api_token_exp = claims.get("exp") or (time.time() + float(expires_in) if expires_in else None)
    st.session_state.api_base_url = base_url
    st.session_state.login_notice = None
    st.session_state.api_token = access  # last: the session is open only once everything above is kept
    if received is not None:
        received.clear()
        received["status"] = "installed"


def show_login_notice():
    """A login notice is shown once: a newer one, or the next attempt, never appears beside it."""
    notice = st.session_state.login_notice
    if notice:
        st.session_state.login_notice = None
        st.sidebar.warning(notice)


def start_login_attempt(context):
    """A new state and verifier, held only by this session, and an empty outcome. The context (backend, issuer,
    redirect, client) is part of the attempt: changing any of them starts another one."""
    _base_url, issuer, redirect_uri, client_id = context
    st.session_state.oauth_attempt = AuthorizationAttempt.create(issuer, redirect_uri, client_id)
    st.session_state.oauth_outcome = {"status": "idle"}
    st.session_state.oauth_context = context
    st.session_state.oauth_started = time.monotonic()


def exchange_once(outcome, attempt, callback, base_url, expired):
    """Validates the redirect against this session's attempt (state, iss, a single code, no error) and only then
    exchanges the code with the verifier. It reads no session state and calls no `st` function: nothing can stop it
    between the token endpoint's answer and the moment the answer is kept in `outcome`, and `outcome` leaves the idle
    status before the call, so a repeated delivery of the same redirect never exchanges again."""
    if expired:
        outcome.update(status="failed", notice=LOGIN_EXPIRED)
        return
    outcome["status"] = "exchanging"  # an unexpected error from here on leaves it, and the attempt is not reused
    try:
        code = attempt.validate_callback(callback)
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=DEFAULT_TIMEOUT_SECONDS) as client:
            outcome.update(status="done", response=exchange_code(client, attempt, code), base_url=base_url)
    except httpx.TransportError:
        outcome.update(status="failed", notice=LOGIN_UNREACHABLE)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        outcome.update(status="failed", notice=LOGIN_REFUSED)
    finally:
        if outcome.get("status") == "exchanging":  # an unexpected error: the attempt is spent all the same
            outcome.update(status="failed", notice=LOGIN_REFUSED)


def install_login(outcome):
    """Installs a login kept in `outcome`, for the backend that issued it (the address the code was exchanged at)."""
    try:
        install_token_response(outcome["response"], outcome["base_url"], received=outcome)
    except (ValueError, KeyError, TypeError):
        outcome.clear()
        outcome.update(status="failed", notice=LOGIN_REFUSED)


def settle_login(outcome):
    """Applies the outcome of the attempt, kept by this run or by one that a rerun stopped: opens the session or
    leaves the notice, then starts over. Every step is safe to repeat."""
    if outcome.get("status") == "done":
        install_login(outcome)
    installed = outcome.get("status") == "installed"
    st.session_state.login_notice = None if installed else (outcome.get("notice") or LOGIN_REFUSED)
    st.session_state.oauth_attempt = None
    st.rerun()


def renew_tokens(base_url, rotation):
    """Rotates the refresh token. The old one is dropped before the call and never sent again to the token endpoint: a
    lost answer may mean the rotation happened, and a reuse would revoke the whole login. The answer is kept in
    `rotation` before any yield point (see above) and installed by finish_rotation. Any failure ends the session;
    a lost answer first asks the backend, once, to revoke the login (POST /v1/auth/revoke with the access token and the
    old refresh token). That revocation only takes effect while the access token is still valid: after its expiry
    (a tab left idle) the backend answers 401, nothing is revoked, and the rotation nobody received stays usable until
    the refresh token expires."""
    old_refresh, principal, access = (
        st.session_state.api_refresh_token, st.session_state.api_principal, st.session_state.api_token
    )
    st.session_state.api_refresh_token = None  # from here to the kept answer there is no yield point
    rotation.clear()
    try:
        if not old_refresh or not principal:
            raise ValueError("No refresh token or principal")
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=DEFAULT_TIMEOUT_SECONDS) as client:
            rotation.update(response=refresh(client, WEB_CLIENT_ID, old_refresh), base_url=base_url, principal=principal)
    except httpx.TransportError:
        try:
            get_session_client(st.session_state, base_url, access).logout(old_refresh)
        except (APIError, httpx.TransportError):
            pass  # best effort: the family expires on its own
        finally:
            end_session(SESSION_EXPIRED)
        return
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        end_session(SESSION_EXPIRED)
        return
    finish_rotation(rotation)


def finish_rotation(rotation):
    """Installs a rotation kept by this run or by one that a rerun stopped; one for another principal ends the
    session."""
    try:
        install_token_response(
            rotation["response"], rotation["base_url"], expected_principal=rotation["principal"], received=rotation
        )
    except (ValueError, KeyError, TypeError):
        rotation.clear()
        end_session(SESSION_EXPIRED)


def logout_and_end(base_url, notice=None):
    """Revokes the access token and the refresh family at the backend that issued them, then ends the session and
    drops the conversation. An unconfirmed revocation is announced instead of `notice`."""
    try:
        client = get_session_client(st.session_state, base_url, st.session_state.api_token)
        client.logout(st.session_state.api_refresh_token)
    except (APIError, httpx.TransportError):
        notice = REVOKE_UNCONFIRMED
    finally:
        end_session(notice, forget=True)


def session_sidebar(base_url):
    """The session part of the sidebar in Real (API) mode: a kept rotation, another backend address, the renewal, the
    login by redirection and the logout. Runs under token_flow()."""
    # First, tokens a stopped run kept: installed for the backend that issued them, before any other check. Then
    # every check below sees them; in particular another backend address revokes them where they were issued.
    rotation = st.session_state.token_rotation
    if rotation.get("response"):
        finish_rotation(rotation)
    outcome = st.session_state.get("oauth_outcome")
    if not st.session_state.api_token and outcome and outcome.get("status") == "done":
        install_login(outcome)
    token_exp = st.session_state.api_token_exp
    if st.session_state.api_token and st.session_state.api_base_url != base_url:
        # The tokens belong to the backend that issued them: revoked there, never sent to the new address.
        if st.session_state.api_base_url:
            logout_and_end(st.session_state.api_base_url, API_URL_CHANGED)
        else:
            end_session(API_URL_CHANGED, forget=True)
    elif st.session_state.api_token and token_exp and time.time() >= token_exp - REFRESH_MARGIN_SECONDS:
        renew_tokens(base_url, rotation)
    if not st.session_state.api_token and base_url.rstrip("/") != BACKEND_API_URL.rstrip("/"):
        # Not the backend paired with AUTH_ISSUER: no attempt, so no code or verifier can reach it.
        st.session_state.oauth_attempt = None
        show_login_notice()
        st.sidebar.warning(LOGIN_UNPAIRED.format(url=BACKEND_API_URL, issuer=AUTH_ISSUER))
    elif not st.session_state.api_token:
        show_login_notice()
        try:
            # only the browser goes to the public address; the code, the tokens and the logout stay on base_url
            authorize_url = authorization_endpoint(base_url, AUTH_AUTHORIZE_URL)
        except ValueError:
            st.session_state.oauth_attempt = None
            st.sidebar.error(LOGIN_URL_INVALID)
            return
        context = (base_url, AUTH_ISSUER, AUTH_REDIRECT_URI, WEB_CLIENT_ID)
        outcome = st.session_state.get("oauth_outcome")
        if (
            st.session_state.get("oauth_attempt") is None
            or st.session_state.get("oauth_context") != context
            or not outcome
            or outcome.get("status") == "installed"
        ):
            start_login_attempt(context)
        attempt, outcome = st.session_state.oauth_attempt, st.session_state.oauth_outcome
        if outcome["status"] != "idle":
            settle_login(outcome)  # kept by a run that a rerun stopped before applying it; never exchanged again
        age = time.monotonic() - st.session_state.oauth_started
        with st.sidebar:
            callback = login_popup(
                attempt.authorization_url(authorize_url),
                attempt.state,
                expires_in=max(0.0, OAUTH_ATTEMPT_SECONDS - age),
            )
        expired = age > OAUTH_ATTEMPT_SECONDS
        if callback is not None:
            # The attempt is spent before any network call, whatever the outcome; the next run starts a new one.
            # The button of a tab left open past the deadline asks for a new attempt instead of opening the popup.
            expired = expired or (isinstance(callback, dict) and callback.get("expired") is True)
            exchange_once(outcome, attempt, callback, base_url, expired)
            settle_login(outcome)
        elif expired:
            st.session_state.oauth_attempt = None  # the button must not keep a stale state and verifier
            st.rerun()
    else:
        principal = st.session_state.api_principal
        st.sidebar.success(
            f"Sessão ativa · papel {st.session_state.api_role}" + (f" · {principal}" if principal else "")
        )
        if st.sidebar.button("Encerrar sessão", key="logout"):
            logout_and_end(base_url)
            st.rerun()


st.set_page_config(page_title="Getnet Merchant Assistant", page_icon="💬", layout="wide")
# The callback page runs in the popup, in a different Streamlit session. It forwards the
# response to the original session, which alone holds the verifier and validates state.
if any(key in st.query_params for key in ("code", "state", "iss", "error")):
    callback = {key: st.query_params.get_all(key) for key in st.query_params}
    return_from_popup(callback)
    st.stop()
st.title("Assistente Getnet")
st.caption("Atendimento, resultados rastreáveis e administração. Chaves de provedores ficam no backend.")

for key, default in {
    "used_comparisons": set(),
    "api_token": None,
    "api_role": None,
    "api_principal": None,
    "api_token_exp": None,
    "api_refresh_token": None,
    "api_base_url": None,
    "conversation_owner": None,
    "login_notice": None,
    "ticket_attempt": None,
    "ticket_notice": None,
    "ticket_confirm_context": None,
    "ticket_solve_confirm_context": None,
    "token_rotation": {},
    "token_lock": threading.Lock(),
    **conversation_defaults(),
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

mode = st.sidebar.selectbox("Modo", ["Simulado", "Real (API)", "Gravado"], key="mode")
if mode != "Real (API)":
    close_session_client(st.session_state)
if mode == "Simulado":
    st.sidebar.info("Simulado · sem chamadas ao backend ou provedores")
    shared = os.environ.get("LOCK_BACKEND_URL", "").lower() in {"1", "true", "yes"}
    role = st.sidebar.selectbox("Papel simulado", ["demo"] if shared else ["demo", "admin"], key="sim_role")
    api = SimulatedAPI(used=st.session_state.used_comparisons)
elif mode == "Gravado":
    st.sidebar.info("Gravado · reapresenta uma resposta sintética salva")
    role = "demo"
    api = SimulatedAPI(recorded=True, used=st.session_state.used_comparisons)
else:
    role = st.session_state.api_role
    base_url = st.sidebar.text_input(
        "URL da API backend",
        value=BACKEND_API_URL,
        # A shared (public) interface keeps the backend address fixed by the operator.
        disabled=os.environ.get("LOCK_BACKEND_URL", "").lower() in {"1", "true", "yes"},
    )
    with token_flow():
        session_sidebar(base_url)
    role = st.session_state.api_role
if mode == "Real (API)" and not st.session_state.api_token:
    st.warning("Entre pela janela de login para abrir a sessão.")
    st.stop()
if mode == "Real (API)":
    api = get_session_client(st.session_state, base_url, st.session_state.api_token)
if mode == "Real (API)" and role == "operator":
    (ticketing_tab,) = st.tabs(["Ticketing"])
    with ticketing_tab:
        render_ticketing(st.session_state.api_token)
    st.stop()

try:
    profile_items, jev_available = load_profiles(api)
except APIError as exc:
    if session_ended(exc):
        end_session(SESSION_EXPIRED)
        st.rerun()
    st.error(f"Não foi possível carregar perfis: {explain(exc)}")
    st.stop()
except httpx.TransportError:
    st.error(f"Não foi possível carregar perfis. {TRANSPORT_RECOVERY}")
    st.stop()

published_profiles = sorted(
    (item for item in profile_items if item.get("state") == "published"),
    key=lambda item: (PROFILE_ORDER.get(item["profile_id"], len(PROFILE_ORDER)), item["name"]),
)
if not published_profiles:
    st.info("Nenhum perfil publicado está disponível para esta sessão. Peça ao administrador para publicar um perfil.")
    st.stop()

if (
    mode == "Real (API)"
    and role in RESUME_ROLES
    and not st.session_state.resume_checked
    and not st.session_state.conversation_id
    and not st.session_state.chat_history
    and st.session_state.pending_message is None
):
    resume_last_conversation(api)

tab_names = ["Atendimento", "Resultados"] + (["Administração"] if role == "admin" else [])
tabs = st.tabs(tab_names)
attendance_tab, results_tab = tabs[0], tabs[1]

with attendance_tab:
    profile_labels = {f"{profile_name(item)} · v{item['version']}": item for item in published_profiles}
    available_labels = [label for label, item in profile_labels.items() if profile_available(item)]
    selected_profile_label = st.selectbox(
        "Perfil publicado",
        list(profile_labels),
        index=list(profile_labels).index((available_labels or list(profile_labels))[0]),
        format_func=lambda label: (
            label if profile_available(profile_labels[label]) else f"{label} · {UNAVAILABLE_HERE}"
        ),
        key="profile",
    )
    profile = profile_labels[selected_profile_label]
    profile_id, profile_version = profile["profile_id"], profile["version"]
    profile_ok = profile_available(profile)
    if not profile_ok:
        st.warning(profile_unavailable_note(profile))

    keep_llm_without_jev(f"router_choice_{profile_id}", jev_available)
    mechanism_default = "jev" if profile_id == "hybrid" and jev_available else "llm"
    mechanism = st.radio(
        "Router do atendimento",
        ["llm", "jev"],
        index=["llm", "jev"].index(mechanism_default),
        format_func=lambda value: "LLM" if value == "llm" else "JEV",
        horizontal=True,
        disabled=not jev_available,
        key=f"router_choice_{profile_id}",  # one per profile, so the default still follows the profile
    )
    if not jev_available:
        st.caption(JEV_UNAVAILABLE)
    scenarios, scenarios_error = load_scenarios(api)
    if scenarios_error:
        st.caption(f"Catálogo de cenários indisponível: {scenarios_error}")
    scenario_labels = {"Mensagem livre": None} | {item["title"]: item for item in scenarios}
    chosen_label = st.selectbox("Cenário do catálogo", list(scenario_labels), key="scenario_choice")
    chosen_scenario = scenario_labels[chosen_label]

    resume_notice = st.session_state.resume_notice
    if resume_notice:
        st.session_state.resume_notice = None  # shown once
        st.warning(resume_notice)
    resumed = st.session_state.resumed
    if resumed and resumed["conversation_id"] == st.session_state.conversation_id:
        st.info(f"Conversa retomada · última atividade em {stamp(resumed['updated_at'])}")
        if resumed["pending_operator_reply"]:
            st.caption(RESUME_PENDING_REPLY)
    if (st.session_state.conversation_id or st.session_state.chat_history) and st.button(
        "Nova conversa", key="new_conversation"
    ):
        start_new_conversation()
        st.rerun()

    for turn in st.session_state.chat_history:
        attendant = turn.get("via") == "operator"
        with st.chat_message(turn["role"], avatar="🧑‍💼" if attendant else None):
            if attendant:
                st.caption(attendant_label(turn))
            st.markdown(literal(turn["text"]))
    case_view = st.session_state.case_view
    if case_view:
        st.caption(
            f"Estado do caso: {case_view.get('state') or '—'} · status {case_view.get('status') or '—'} · "
            f"rota {case_view.get('route') or '—'} · base {case_view.get('knowledge_base_version_id') or '—'}"
            + (f" · protocolo {case_view['protocol']}" if case_view.get("protocol") else "")
        )

    if chosen_scenario:
        st.info(f"Mensagem do cenário: {literal(chosen_scenario['message'])}")
        send_scenario = st.button(
            "Enviar cenário",
            key="send_scenario",
            disabled=st.session_state.pending_message is not None or not profile_ok,
        )
        if send_scenario and profile_ok:
            payload = {
                "message": chosen_scenario["message"],
                "profile_id": profile_id,
                "profile_version": profile_version,
                "router_mechanism": mechanism,
                "scenario_id": chosen_scenario["scenario_id"],
            }
            try:
                if send_chat_message(api, payload, chosen_scenario["message"]):
                    st.rerun()
                else:
                    st.warning("Não foi possível confirmar o envio. A pergunta está pendente; atualize a conversa abaixo.")
            except APIError as exc:
                st.error(f"Falha ao enviar: {explain(exc)} O método selecionado não foi trocado automaticamente.")

    with st.form("message_form", clear_on_submit=True):
        message_text = st.text_area(
            "Mensagem livre", key="message_input", height=90, placeholder="Ex.: Quando recebo o valor das minhas vendas?"
        )
        send = st.form_submit_button(
            "Enviar mensagem",
            key="send_message",
            disabled=st.session_state.pending_message is not None or not profile_ok,
        )
    if send and message_text.strip() and profile_ok:
        payload = {
            "message": message_text,
            "profile_id": profile_id,
            "profile_version": profile_version,
            "router_mechanism": mechanism,
        }
        try:
            if send_chat_message(api, payload, message_text):
                st.rerun()
            else:
                st.warning("Não foi possível confirmar o envio. A pergunta está pendente; atualize a conversa abaixo.")
        except APIError as exc:
            st.error(f"Falha ao enviar: {explain(exc)} O método selecionado não foi trocado automaticamente.")

    pending = st.session_state.pending_message
    if pending:
        st.warning("O envio pode ter sido concluído no backend. A interface não o reenviará automaticamente.")
        st.write(f"Pergunta pendente: {literal(pending['message'])}")
        if mode != "Real (API)":
            st.info("Volte ao modo Real (API) para atualizar esta conversa.")
        elif st.button("Atualizar conversa", key="recover_pending_conversation"):
            try:
                conversation = api.conversation(pending["conversation_id"])
                found = find_pending_reply(conversation, pending)
                if found is None:
                    st.info("Ainda não há resposta nova para esta pergunta. Atualize novamente mais tarde.")
                else:
                    recovered, consumed = found
                    st.session_state.chat_history.extend(
                        [
                            {"role": "user", "text": pending["message"]},
                            assistant_turn(recovered.get("text"), recovered),
                        ]
                    )
                    st.session_state.case_view = {
                        "status": recovered.get("status"),
                        "route": recovered.get("route"),
                        "state": conversation.get("state"),
                        "protocol": None,
                        "knowledge_base_version_id": None,
                    }
                    st.session_state.pending_message = None
                    st.session_state.backend_message_count = consumed
                    st.session_state.comparison = None
                    st.session_state.recovered_without_request_id = True
                    st.rerun()
            except APIError as exc:
                if exc.status_code == 404:
                    st.info("A conversa ainda não está disponível. A pergunta continua pendente.")
                else:
                    st.warning(f"Não foi possível atualizar a conversa (HTTP {exc.status_code}). Tente novamente.")
            except httpx.TransportError as exc:
                st.warning(
                    f"A consulta falhou ({type(exc).__name__}). A pergunta continua pendente; tente atualizar novamente."
                )

    with st.expander("Comparação só dos Routers (LLM × JEV)", expanded=False):
        st.write(
            "Chama somente os dois Routers; não é comparável ao atendimento completo nem entra na comparação de "
            "perfis. O atendimento só começa após você usar a decisão."
        )
        compare_ready = jev_available and profile_ok  # it always calls both Routers
        if not jev_available:
            st.caption(f"{JEV_UNAVAILABLE} A comparação só dos Routers precisa dele.")
        elif not profile_ok:
            st.caption(profile_unavailable_note(profile))
        keep_llm_without_jev("comparison_selection", jev_available)
        selected_mechanism = st.radio(
            "Decisão a usar depois da comparação",
            ["llm", "jev"],
            format_func=lambda value: "LLM" if value == "llm" else "JEV",
            horizontal=True,
            disabled=not jev_available,
            key="comparison_selection",
        )
        with st.form("compare_form"):
            compare_text = st.text_area("Mensagem para comparar", key="compare_input", height=90)
            compare_action = st.form_submit_button(
                "Comparar LLM e JEV", key="compare_routers", disabled=not compare_ready
            )
        if compare_action and compare_text.strip() and compare_ready:
            try:
                comparison = api.compare(
                    {
                        "message": compare_text,
                        "conversation_id": st.session_state.conversation_id,
                        "profile_id": profile_id,
                        "profile_version": profile_version,
                        "selected_mechanism": selected_mechanism,
                    }
                )
                st.session_state.comparison = comparison | {
                    "message": compare_text,
                    "profile_id": profile_id,
                    "profile_version": profile_version,
                }
            except APIError as exc:
                st.error(f"Comparação indisponível: {explain(exc)}")
            except httpx.TransportError:
                st.error(f"Comparação sem confirmação. {TRANSPORT_RECOVERY}")
        comparison = st.session_state.comparison
        if comparison and selected_mechanism != comparison["selected_mechanism"]:
            st.session_state.comparison = None
            comparison = None
            st.info(
                "A escolha mudou; o resultado anterior foi invalidado. Compare novamente para criar uma nova decisão."
            )
        if comparison:
            st.caption(f"Modo: {comparison['execution_mode']} · seleção: {comparison['selected_mechanism'].upper()}")
            left, right = st.columns(2)
            for column, method in ((left, "llm"), (right, "jev")):
                decision = comparison["decisions"][method]["decision"]
                with column:
                    st.subheader(method.upper())
                    st.write(f"Rota: `{decision['route']}`")
                    st.write(f"Idioma: `{decision['language']}` · tópico: `{decision['topic']}`")
                    st.write(f"Modelo: `{comparison['decisions'][method]['model']}`")
            if st.button(
                "Usar esta decisão e iniciar atendimento",
                key="use_comparison",
                disabled=st.session_state.pending_message is not None or not compare_ready,
            ) and compare_ready:
                selected = comparison["selected_mechanism"]
                try:
                    sent = send_chat_message(
                        api,
                        {
                            "message": comparison["message"],
                            "profile_id": profile_id,
                            "profile_version": profile_version,
                            "router_mechanism": selected,
                            "comparison_id": comparison["comparison_id"],
                        },
                        comparison["message"],
                    )
                    if sent:
                        st.rerun()
                    else:
                        st.warning("Não foi possível confirmar o envio. A pergunta está pendente; atualize a conversa.")
                except APIError as exc:
                    st.error(f"A decisão não foi usada: {explain(exc)}")

with results_tab:
    if st.session_state.pending_run_selection in st.session_state.runs:
        st.session_state["results_run_choice"] = st.session_state.pending_run_selection
    st.session_state.pending_run_selection = None
    runs = st.session_state.runs
    if runs:
        request_id = st.selectbox("Execução", list(runs), key="results_run_choice")
        current = runs[request_id]
        render_run(current)
        if mode == "Real (API)" and st.button("Atualizar execução", key="refresh_run"):
            try:
                remember_run(request_id, api.run(request_id) | {"request_id": request_id})
                st.rerun()
            except APIError as exc:
                st.error(f"Execução indisponível: {explain(exc)}")
            except httpx.TransportError:
                st.warning(TRANSPORT_RECOVERY)

        estimate = st.session_state.evaluation_estimate
        if estimate and estimate.get("request_id") != request_id:
            estimate = None
        if st.button("Estimar avaliação por IA (sem custo)", key="eval_estimate"):
            try:
                estimate = api.estimate_evaluation(request_id) | {"request_id": request_id}
                st.session_state.evaluation_estimate = estimate
            except APIError as exc:
                st.session_state.evaluation_estimate = estimate = None
                st.error(f"Estimativa indisponível: {explain(exc)}")
            except httpx.TransportError:
                st.warning(TRANSPORT_RECOVERY)
        if estimate:
            evaluator = estimate["evaluator"]
            st.info(
                f"Método {estimate['method']} · rubrica {estimate['rubric']} v{estimate['rubric_version']} · avaliador "
                f"{evaluator['provider']}:{evaluator['model']} {evaluator['parameters']} · evidências "
                f"{', '.join(estimate['evidence_fields'])} · estimativa US$ {estimate['estimated_cost_usd']:.6f}. "
                "Avalia a resposta já persistida; o atendimento não é repetido."
            )
            repeat = False
            if estimate.get("existing_evaluation_id"):
                st.caption(f"Já existe a avaliação {estimate['existing_evaluation_id']} com a mesma configuração.")
                repeat = st.checkbox("Repetir mesmo assim (nova chamada paga)", key="eval_repeat")
            reset_confirmation_context(
                "eval_confirm_context", (request_id, estimate["estimate_digest"], repeat), "eval_confirm"
            )
            confirmed_evaluation = st.checkbox("Confirmo a avaliação e o custo estimado", key="eval_confirm")
            if st.button("Avaliar com IA", key="eval_run", disabled=not confirmed_evaluation):
                try:
                    evaluation = api.evaluate(
                        request_id,
                        {
                            "confirmed": True,
                            "max_cost_usd": estimate["estimated_cost_usd"],
                            "expected_estimate_digest": estimate["estimate_digest"],
                            "repeat": repeat,
                        },
                    )
                    previous = current.get("evaluations") or []
                    if evaluation.get("evaluation_id") not in [item.get("evaluation_id") for item in previous]:
                        remember_run(request_id, current | {"evaluations": [*previous, evaluation]})
                    st.session_state.evaluation_estimate = None
                    st.rerun()
                except APIError as exc:
                    st.session_state.evaluation_estimate = None
                    st.error(f"Avaliação não concluída: {explain(exc)}")
                except httpx.TransportError:
                    st.session_state.evaluation_estimate = None
                    st.warning(f"Avaliação sem confirmação. {TRANSPORT_RECOVERY}")
        with st.expander("Dados completos da execução (JSON)", expanded=False):
            st.json(current)
    else:
        st.info("Envie uma mensagem no Atendimento para inspecionar a primeira execução.")
    if st.session_state.recovered_without_request_id:
        st.caption("Resposta recuperada pela conversa; o ID da execução não foi retornado e não foi inferido.")

    st.divider()
    st.subheader("Comparar perfis")
    st.caption(
        "Dois ou três perfis publicados × até três cenários × até duas repetições. Execuções compatíveis já gravadas "
        "são reaproveitadas; novas execuções só após estimativa e confirmação, sem alterar o atendimento original."
    )
    profile_options = {f"{profile_name(item)} · v{item['version']}": item for item in published_profiles}
    available_options = [label for label, item in profile_options.items() if profile_available(item)]
    chosen_profiles = st.multiselect(
        "Perfis",
        list(profile_options),
        default=available_options[:2],
        max_selections=3,
        format_func=lambda label: (
            label if profile_available(profile_options[label]) else f"{label} · {UNAVAILABLE_HERE}"
        ),
        key="cmp_profiles",
    )
    chosen_unavailable = [profile_options[label] for label in chosen_profiles if label not in available_options]
    for item in chosen_unavailable:
        st.warning(profile_unavailable_note(item))
    if len(available_options) < 2 and len(available_options) < len(profile_options):
        st.caption("A comparação precisa de dois perfis disponíveis neste backend.")
    scenario_options = {item["title"]: item for item in scenarios}
    chosen_scenarios = st.multiselect(
        "Cenários", list(scenario_options), default=list(scenario_options)[:1], max_selections=3, key="cmp_scenarios"
    )
    repetitions = int(st.number_input("Repetições", min_value=1, max_value=2, value=1, key="cmp_repetitions"))
    keep_llm_without_jev("cmp_mechanism", jev_available)
    cmp_mechanism = st.radio(
        "Router nas execuções",
        ["llm", "jev"],
        format_func=lambda value: "LLM" if value == "llm" else "JEV",
        horizontal=True,
        disabled=not jev_available,
        key="cmp_mechanism",
    )
    if not jev_available:
        st.caption(JEV_UNAVAILABLE)
    selection = {
        "profiles": [
            {"profile_id": profile_options[label]["profile_id"], "profile_version": profile_options[label]["version"]}
            for label in chosen_profiles
        ],
        "scenario_ids": [scenario_options[label]["scenario_id"] for label in chosen_scenarios],
        "repetitions": repetitions,
        "router_mechanism": cmp_mechanism,
    }
    valid_selection = (
        2 <= len(selection["profiles"]) <= 3
        and 1 <= len(selection["scenario_ids"]) <= 3
        and not chosen_unavailable
        and (cmp_mechanism == "llm" or jev_available)
    )
    if st.button("Estimar comparação (sem custo)", key="cmp_estimate", disabled=not valid_selection) and valid_selection:
        try:
            st.session_state.comparison_estimate = {
                "selection": selection,
                "estimate": api.estimate_comparison(selection),
            }
        except APIError as exc:
            st.session_state.comparison_estimate = None
            st.error(f"Estimativa indisponível: {explain(exc)}")
        except httpx.TransportError:
            st.warning(TRANSPORT_RECOVERY)
    stored = st.session_state.comparison_estimate
    if stored and stored["selection"] != selection:
        st.session_state.comparison_estimate = stored = None
        st.info("A seleção mudou; estime novamente antes de executar.")
    if stored:
        estimate = stored["estimate"]
        st.info(
            f"{estimate['sample_size']} células · {estimate['new_executions']} novas · "
            f"{estimate['reused_executions']} reaproveitadas (gravadas) · estimativa máxima "
            f"US$ {estimate['estimated_cost_usd']:.6f} · base {estimate['fixed_factors'].get('knowledge_base_version_id')}"
        )
        if estimate.get("exploratory"):
            st.warning("Exploratória: os perfis diferem em mais de um agente.")
        reset_confirmation_context(
            "cmp_confirm_context", (estimate["estimate_digest"], estimate["estimated_cost_usd"]), "cmp_confirm"
        )
        confirmed_comparison = st.checkbox("Confirmo a comparação e o custo máximo estimado", key="cmp_confirm")
        if st.button(
            "Executar comparação", key="cmp_run", disabled=not confirmed_comparison or not valid_selection
        ) and valid_selection:
            try:
                created = api.create_comparison(
                    selection
                    | {
                        "confirmed": True,
                        "max_cost_usd": estimate["estimated_cost_usd"],
                        "expected_estimate_digest": estimate["estimate_digest"],
                    }
                )
                st.session_state.profile_comparison_id = created["comparison_id"]
                st.session_state.profile_comparison = None
                st.session_state.comparison_estimate = None
                st.rerun()
            except APIError as exc:
                st.session_state.comparison_estimate = None
                st.error(f"Comparação não iniciada: {explain(exc)}")
            except httpx.TransportError:
                st.session_state.comparison_estimate = None
                st.warning(f"Início sem confirmação. {TRANSPORT_RECOVERY}")
    if st.session_state.profile_comparison_id:
        if st.button("Atualizar comparação", key="cmp_refresh"):
            try:
                st.session_state.profile_comparison = api.comparison(st.session_state.profile_comparison_id)
            except APIError as exc:
                st.error(f"Comparação indisponível: {explain(exc)}")
            except httpx.TransportError:
                st.warning(TRANSPORT_RECOVERY)
        result = st.session_state.profile_comparison
        if result:
            render_comparison(result)
        else:
            st.info("Comparação iniciada. Use “Atualizar comparação” para acompanhar (consulta sem custo).")

if role == "admin":
    with tabs[2]:
        with st.expander("Perfis", expanded=True):
            st.write("Perfis publicados são imutáveis. Configure modelos compatíveis e limites de saída por agente.")
            try:
                catalog = api.model_catalog()["models"]
            except (APIError, httpx.TransportError) as exc:
                st.error(f"Catálogo de modelos indisponível: {explain_any(exc)}")
                catalog = []
            with st.form("admin_profile_form"):
                draft_name = st.text_input("Nome do novo perfil", value="Ajustado", key="profile_draft_name")
                new_agents = render_agent_configuration(profile["agents"], catalog, f"create_{profile_id}")
                allow_demo = st.checkbox("Permitir seleção pela sessão demo", value=True, key="profile_allow_demo")
                create_action = st.form_submit_button("Criar rascunho")
            if create_action:
                try:
                    result = api.create_profile({"name": draft_name, "agents": new_agents, "demo_allowed": allow_demo})
                    st.success(f"Rascunho {result['profile_id']} · v{result['version']} criado.")
                except (APIError, httpx.TransportError) as exc:
                    st.error(f"Não foi possível criar o perfil: {explain_any(exc)}")
                except KeyError as exc:
                    st.error(f"Não foi possível criar o perfil: {exc}")
            drafts = [item for item in profile_items if item.get("state") == "draft"]
            if drafts:
                draft = st.selectbox(
                    "Rascunho para editar ou publicar",
                    drafts,
                    format_func=lambda item: item["name"],
                    key="profile_draft_choice",
                )
                with st.form("update_profile_form"):
                    updated_agents = render_agent_configuration(
                        draft["agents"], catalog, f"update_{draft['profile_id']}_{draft['version']}"
                    )
                    update_action = st.form_submit_button("Salvar nova versão do rascunho")
                if update_action:
                    try:
                        result = api.update_profile(
                            draft["profile_id"],
                            {"expected_version": draft["version"], "agents": updated_agents},
                        )
                        st.success(f"Rascunho atualizado · v{result['version']}.")
                        st.rerun()
                    except (APIError, httpx.TransportError) as exc:
                        st.error(f"Não foi possível atualizar o rascunho: {explain_any(exc)}")
                    except KeyError as exc:
                        st.error(f"Não foi possível atualizar o rascunho: {exc}")
                if st.button("Publicar versão de rascunho", key="publish_profile"):
                    try:
                        result = api.publish_profile(draft["profile_id"], draft["version"])
                        st.success(f"Perfil publicado · v{result['version']}.")
                        st.rerun()
                    except (APIError, httpx.TransportError) as exc:
                        st.error(f"Publicação recusada: {explain_any(exc)}")

        if mode != "Real (API)":
            st.info("A base de conhecimento versionada é administrada no modo Real (API).")
        else:
            with st.expander("Base de conhecimento versionada", expanded=True):
                st.caption("Edite somente orientações fictícias. Cada build cria uma coleção candidata separada.")
                notice = st.session_state.pop("admin_notice", None)
                if notice:
                    st.warning(notice)
                try:
                    with st.spinner("Carregando documentos e versões..."):
                        kb_documents = api.kb_documents()
                        kb_state = api.kb_versions()
                except (APIError, httpx.TransportError) as exc:
                    st.error(f"Não foi possível carregar a base: {explain_any(exc)}")
                    kb_documents, kb_state = [], {"versions": []}
                if not kb_documents:
                    st.info("Nenhum documento registrado está disponível.")
                else:
                    st.caption(
                        f"Versão ativa: `{kb_state.get('active_version_id')}` · anterior: "
                        f"`{kb_state.get('previous_version_id') or '—'}`"
                    )
                    labels = {f"{item['title']} · {item['source_kind']}": item for item in kb_documents}
                    selected_doc = st.selectbox("Documento", list(labels), key="kb_document_choice")
                    document = labels[selected_doc]
                    try:
                        with st.spinner("Carregando documento..."):
                            kb_detail = api.kb_document(document["document_id"])
                    except (APIError, httpx.TransportError) as exc:
                        kb_detail = None
                        st.error(f"Documento indisponível: {explain_any(exc)}")
                    if kb_detail:
                        st.write(
                            f"Origem: `{kb_detail['source_kind']}` · hash publicado: `{kb_detail['content_hash'][:16]}`"
                        )
                        if document["editable"]:
                            current_draft = kb_detail.get("draft") or {}
                            draft_rev = current_draft.get("revision", 0)
                            with st.form("kb_draft_form"):
                                edited_text = st.text_area(
                                    "Conteúdo fictício",
                                    value=current_draft.get("content", kb_detail["content"]),
                                    height=260,
                                    # one per document and draft revision: a saved draft or another document shows its own text
                                    key=f"kb_draft_text_{document['document_id']}_{draft_rev}",
                                )
                                save_draft = st.form_submit_button("Salvar rascunho", disabled=role != "admin")
                            if save_draft:
                                try:
                                    saved = api.kb_save_draft(
                                        document["document_id"], {"content": edited_text, "expected_revision": draft_rev}
                                    )
                                    st.session_state.pop("kb_estimate", None)
                                    st.success(f"Rascunho salvo · revisão {saved['revision']}.")
                                    st.rerun()
                                except (APIError, httpx.TransportError) as exc:
                                    st.error(f"Rascunho não salvo: {explain_any(exc)}")
                            if current_draft:
                                try:
                                    st.code(api.kb_diff(document["document_id"])["diff"], language="diff")
                                except (APIError, httpx.TransportError) as exc:
                                    st.error(f"Diff indisponível: {explain_any(exc)}")
                        else:
                            st.text_area(
                                "Fonte oficial · somente leitura",
                                value=kb_detail["content"],
                                height=260,
                                disabled=True,
                                key=f"kb_source_text_{document['document_id']}",
                            )

                    revisions = {
                        item["document_id"]: item["draft_revision"]
                        for item in kb_documents
                        if item.get("draft_revision") is not None
                    }
                    if st.button("Estimar candidata", key="kb_estimate_action", disabled=not revisions):
                        with st.spinner("Calculando estimativa sem chamar o modelo..."):
                            try:
                                st.session_state.kb_estimate = api.kb_estimate(revisions)
                            except (APIError, httpx.TransportError) as exc:
                                st.session_state.pop("kb_estimate", None)
                                st.error(f"Estimativa indisponível: {explain_any(exc)}")
                    estimate = st.session_state.get("kb_estimate")
                    if estimate:
                        st.info(
                            f"{estimate['documents']} documentos · {estimate['chunks']} trechos · "
                            f"{estimate['embedding_model']} · estimativa US$ {estimate['estimated_cost_usd']:.6f}"
                        )
                        max_cost = st.number_input(
                            "Teto desta operação (US$)",
                            min_value=0.0,
                            max_value=5.0,
                            value=float(estimate["estimated_cost_usd"]),
                            format="%.6f",
                            key="kb_build_max_cost",
                        )
                        reset_confirmation_context(
                            "kb_build_confirmation_context",
                            (
                                estimate["digest"],
                                estimate["active_version_id"],
                                tuple(sorted(estimate["revisions"].items())),
                                float(max_cost),
                            ),
                            "kb_confirm_build",
                        )
                        confirmed_build = st.checkbox(
                            "Confirmo o build e o custo máximo informado",
                            key="kb_confirm_build",
                        )
                        build_action = st.button(
                            "Construir candidata",
                            key="kb_build_action",
                            disabled=not confirmed_build or max_cost < float(estimate["estimated_cost_usd"]),
                        )
                        if build_action:
                            try:
                                with st.spinner("Construindo coleção candidata..."):
                                    candidate = api.kb_build(
                                        {
                                            "revisions": estimate["revisions"],
                                            "expected_active_version_id": estimate["active_version_id"],
                                            "expected_digest": estimate["digest"],
                                            "confirmed": True,
                                            "max_cost_usd": max_cost,
                                        }
                                    )
                                st.success(
                                    f"Candidata pronta para teste: {candidate['version_id']} · {candidate['state']}."
                                )
                                st.session_state.pop("kb_estimate", None)
                                st.rerun()
                            except httpx.TransportError:
                                kb_unconfirmed("Build da candidata")
                            except APIError as exc:
                                st.session_state.pop("kb_estimate", None)
                                st.error(f"Build recusado ou interrompido: {explain_any(exc)}")
                    candidates = [
                        item for item in kb_state.get("versions", []) if item["state"] in ("testing", "ready")
                    ]
                    if candidates:
                        candidate = st.selectbox(
                            "Candidata para testar/ativar",
                            candidates,
                            format_func=lambda item: f"{item['version_id']} · {item['state']}",
                            key="kb_candidate_choice",
                        )
                        test_default = float((candidate.get("manifest") or {}).get("estimated_cost_usd") or 0.0)
                        test_cap = st.number_input(
                            "Teto do teste (US$)",
                            min_value=0.0,
                            max_value=5.0,
                            value=min(test_default, 5.0),
                            format="%.6f",
                            key="kb_test_max_cost",
                            help="Pré-preenchido com a estimativa do build da candidata, limite superior de uma consulta.",
                        )
                        reset_confirmation_context(
                            "kb_test_confirmation_context",
                            (candidate["version_id"], kb_state["active_version_id"], float(test_cap)),
                            "kb_confirm_test",
                        )
                        confirm_test = st.checkbox("Confirmo o teste da candidata", key="kb_confirm_test")
                        test_action = st.button(
                            "Testar candidata",
                            key="kb_test_action",
                            disabled=not confirm_test or candidate["state"] not in ("testing", "ready"),
                        )
                        if test_action:
                            try:
                                with st.spinner("Testando a candidata..."):
                                    test_result = api.kb_test(
                                        candidate["version_id"], {"confirmed": True, "max_cost_usd": test_cap}
                                    )
                                st.success(
                                    f"Teste concluído · {'aprovado' if test_result['passed'] else 'sem resultado'}."
                                )
                                st.rerun()
                            except httpx.TransportError:
                                kb_unconfirmed("Teste da candidata")
                            except APIError as exc:
                                st.error(f"Teste interrompido: {explain_any(exc)}")
                        reset_confirmation_context(
                            "kb_activate_confirmation_context",
                            (candidate["version_id"], kb_state["active_version_id"]),
                            "kb_confirm_activate",
                        )
                        confirm_activate = st.checkbox(
                            "Confirmo a ativação desta versão",
                            key="kb_confirm_activate",
                        )
                        activate_action = st.button(
                            "Ativar versão",
                            key="kb_activate_action",
                            disabled=not confirm_activate or not candidate.get("test_result", {}).get("passed"),
                        )
                        if activate_action:
                            try:
                                api.kb_activate(
                                    candidate["version_id"],
                                    {"expected_active_version_id": kb_state["active_version_id"], "confirmed": True},
                                )
                                st.success("Versão ativada para os próximos atendimentos.")
                                st.rerun()
                            except httpx.TransportError:
                                kb_unconfirmed("Ativação")
                            except APIError as exc:
                                st.error(f"Ativação recusada: {explain_any(exc)}")
                    else:
                        st.info("Não há candidatas prontas para testar ou ativar.")
                    if kb_state.get("previous_version_id"):
                        reset_confirmation_context(
                            "kb_rollback_confirmation_context",
                            (kb_state["previous_version_id"], kb_state["active_version_id"]),
                            "kb_confirm_rollback",
                        )
                        confirm_rollback = st.checkbox(
                            "Confirmo o retorno à versão anterior",
                            key="kb_confirm_rollback",
                        )
                        rollback_action = st.button(
                            "Retornar à versão anterior",
                            key="kb_rollback_action",
                            disabled=not confirm_rollback,
                        )
                        if rollback_action:
                            try:
                                api.kb_rollback(
                                    kb_state["previous_version_id"],
                                    {"expected_active_version_id": kb_state["active_version_id"], "confirmed": True},
                                )
                                st.success("Versão anterior restaurada para os próximos atendimentos.")
                                st.rerun()
                            except httpx.TransportError:
                                kb_unconfirmed("Retorno à versão anterior")
                            except APIError as exc:
                                st.error(f"Retorno recusado: {explain_any(exc)}")
