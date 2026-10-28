from __future__ import annotations

import os
import uuid

import altair as alt
import httpx
import pandas as pd
import streamlit as st

from getnet_web.api import APIClient, APIError, close_session_client, get_session_client
from getnet_web.simulated import SimulatedAPI

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
    "demo_message_limit_reached": "Esta sessão demo atingiu o limite de mensagens. Encerre a sessão e entre de novo, "
    "ou peça ao administrador.",
    "comparison_running": "Já há uma comparação em andamento. Use “Atualizar comparação”.",
    "estimate_stale": "A estimativa mudou. Estime novamente antes de confirmar.",
    "evaluator_price_unknown": "O preço do avaliador é desconhecido; a avaliação fica bloqueada.",
}
TRANSPORT_RECOVERY = "A conexão com o backend falhou. Nada foi reenviado; atualize por consulta (GET) mais tarde."


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
        [{"role": "user", "text": message_text}, {"role": "assistant", "text": reply["answer"]}]
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
    return True


def find_pending_reply(conversation, pending):
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

    for item in new_messages[merchant_index + 1 :]:
        if item.get("role") == "merchant":
            return None
        if item.get("role") == "assistant":
            return item
    return None


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


st.set_page_config(page_title="Getnet Merchant Assistant", page_icon="💬", layout="wide")
st.title("Assistente Getnet")
st.caption("Atendimento, resultados rastreáveis e administração. Chaves de provedores ficam no backend.")

for key, default in {
    "used_comparisons": set(),
    "chat_history": [],
    "conversation_id": None,
    "backend_message_count": 0,
    "comparison": None,
    "latest_run": None,
    "pending_message": None,
    "recovered_without_request_id": False,
    "api_token": None,
    "api_role": None,
    "runs": {},
    "case_view": None,
    "evaluation_estimate": None,
    "comparison_estimate": None,
    "profile_comparison_id": None,
    "profile_comparison": None,
    "pending_run_selection": None,
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
        value=os.environ.get("BACKEND_API_URL", "http://localhost:8000"),
        # A shared (public) interface keeps the backend address fixed by the operator.
        disabled=os.environ.get("LOCK_BACKEND_URL", "").lower() in {"1", "true", "yes"},
    )
    if not st.session_state.api_token:
        access_key = st.sidebar.text_input("Chave de acesso demo/admin", type="password")
        if st.sidebar.button("Entrar", key="login"):
            try:
                login_client = APIClient(base_url)
                try:
                    session = login_client.login(access_key)
                finally:
                    login_client.close()
                st.session_state.api_token = session["access_token"]
                st.session_state.api_role = session["role"]
                role = session["role"]
                st.rerun()
            except APIError as exc:
                st.sidebar.error(f"Não foi possível entrar: {explain(exc)}")
            except httpx.TransportError:
                st.sidebar.error("Backend inacessível. Confira a URL da API e se o backend está em execução.")
    else:
        st.sidebar.success(f"Sessão ativa · papel {st.session_state.api_role}")
        if st.sidebar.button("Encerrar sessão", key="logout"):
            logout_client = get_session_client(st.session_state, base_url, st.session_state.api_token)
            try:
                logout_client.logout()
            except (APIError, httpx.TransportError):
                pass
            finally:
                close_session_client(st.session_state)
            st.session_state.api_token = None
            st.session_state.api_role = None
            st.rerun()
if mode == "Real (API)" and not st.session_state.api_token:
    st.warning("Entre com uma chave de acesso configurada no backend para abrir a sessão.")
    st.stop()
if mode == "Real (API)":
    api = get_session_client(st.session_state, base_url, st.session_state.api_token)

try:
    profile_items = api.profiles()
except APIError as exc:
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

tab_names = ["Atendimento", "Resultados"] + (["Administração"] if role == "admin" else [])
tabs = st.tabs(tab_names)
attendance_tab, results_tab = tabs[0], tabs[1]

with attendance_tab:
    profile_labels = {f"{profile_name(item)} · v{item['version']}": item for item in published_profiles}
    selected_profile_label = st.selectbox("Perfil publicado", list(profile_labels), key="profile")
    profile = profile_labels[selected_profile_label]
    profile_id, profile_version = profile["profile_id"], profile["version"]

    mechanism_default = "jev" if profile_id == "hybrid" else "llm"
    mechanism = st.radio(
        "Router do atendimento",
        ["llm", "jev"],
        index=["llm", "jev"].index(mechanism_default),
        format_func=lambda value: "LLM" if value == "llm" else "JEV",
        horizontal=True,
    )
    scenarios, scenarios_error = load_scenarios(api)
    if scenarios_error:
        st.caption(f"Catálogo de cenários indisponível: {scenarios_error}")
    scenario_labels = {"Mensagem livre": None} | {item["title"]: item for item in scenarios}
    chosen_label = st.selectbox("Cenário do catálogo", list(scenario_labels), key="scenario_choice")
    chosen_scenario = scenario_labels[chosen_label]

    for turn in st.session_state.chat_history:
        with st.chat_message(turn["role"]):
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
            disabled=st.session_state.pending_message is not None,
        )
        if send_scenario:
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
            disabled=st.session_state.pending_message is not None,
        )
    if send and message_text.strip():
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
                recovered = find_pending_reply(conversation, pending)
                if recovered is None:
                    st.info("Ainda não há resposta nova para esta pergunta. Atualize novamente mais tarde.")
                else:
                    st.session_state.chat_history.extend(
                        [
                            {"role": "user", "text": pending["message"]},
                            {"role": "assistant", "text": recovered.get("text")},
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
                    st.session_state.backend_message_count += 2
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
        selected_mechanism = st.radio(
            "Decisão a usar depois da comparação",
            ["llm", "jev"],
            format_func=lambda value: "LLM" if value == "llm" else "JEV",
            horizontal=True,
            key="comparison_selection",
        )
        with st.form("compare_form"):
            compare_text = st.text_area("Mensagem para comparar", key="compare_input", height=90)
            compare_action = st.form_submit_button("Comparar LLM e JEV", key="compare_routers")
        if compare_action and compare_text.strip():
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
                disabled=st.session_state.pending_message is not None,
            ):
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
    chosen_profiles = st.multiselect(
        "Perfis", list(profile_options), default=list(profile_options)[:2], max_selections=3, key="cmp_profiles"
    )
    scenario_options = {item["title"]: item for item in scenarios}
    chosen_scenarios = st.multiselect(
        "Cenários", list(scenario_options), default=list(scenario_options)[:1], max_selections=3, key="cmp_scenarios"
    )
    repetitions = int(st.number_input("Repetições", min_value=1, max_value=2, value=1, key="cmp_repetitions"))
    cmp_mechanism = st.radio(
        "Router nas execuções",
        ["llm", "jev"],
        format_func=lambda value: "LLM" if value == "llm" else "JEV",
        horizontal=True,
        key="cmp_mechanism",
    )
    selection = {
        "profiles": [
            {"profile_id": profile_options[label]["profile_id"], "profile_version": profile_options[label]["version"]}
            for label in chosen_profiles
        ],
        "scenario_ids": [scenario_options[label]["scenario_id"] for label in chosen_scenarios],
        "repetitions": repetitions,
        "router_mechanism": cmp_mechanism,
    }
    valid_selection = 2 <= len(selection["profiles"]) <= 3 and 1 <= len(selection["scenario_ids"]) <= 3
    if st.button("Estimar comparação (sem custo)", key="cmp_estimate", disabled=not valid_selection):
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
        if st.button("Executar comparação", key="cmp_run", disabled=not confirmed_comparison):
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
                draft_name = st.text_input("Nome do novo perfil", value="Ajustado")
                new_agents = render_agent_configuration(profile["agents"], catalog, f"create_{profile_id}")
                allow_demo = st.checkbox("Permitir seleção pela sessão demo", value=True)
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
                draft = st.selectbox("Rascunho para editar ou publicar", drafts, format_func=lambda item: item["name"])
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
                                "Fonte oficial · somente leitura", value=kb_detail["content"], height=260, disabled=True
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
