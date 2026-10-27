from __future__ import annotations

import os
import uuid

import httpx
import streamlit as st

from getnet_web.api import APIClient, APIError, close_session_client, get_session_client
from getnet_web.simulated import SimulatedAPI


def reset_confirmation_context(context_key, context, *widget_keys):
    if st.session_state.get(context_key) != context:
        for widget_key in widget_keys:
            st.session_state[widget_key] = False
        st.session_state[context_key] = context


def ensure_conversation_id():
    if not st.session_state.conversation_id:
        st.session_state.conversation_id = f"conv_{uuid.uuid4().hex[:20]}"
    return st.session_state.conversation_id


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
            return item.get("text")
    return None


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
            st.caption("Evaluation permanece reservado para a v0.6.0; parâmetros não podem ser configurados.")
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


st.set_page_config(page_title="Getnet Merchant Assistant", page_icon="💬", layout="wide")
st.title("Assistente Getnet")
st.caption("Converse, compare os Routers e inspecione a execução. Chaves de provedores ficam no backend.")

if "used_comparisons" not in st.session_state:
    st.session_state.used_comparisons = set()
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "backend_message_count" not in st.session_state:
    st.session_state.backend_message_count = 0
if "comparison" not in st.session_state:
    st.session_state.comparison = None
if "latest_run" not in st.session_state:
    st.session_state.latest_run = None
if "pending_message" not in st.session_state:
    st.session_state.pending_message = None
if "recovered_without_request_id" not in st.session_state:
    st.session_state.recovered_without_request_id = False
if "api_token" not in st.session_state:
    st.session_state.api_token = None
if "api_role" not in st.session_state:
    st.session_state.api_role = None

mode = st.sidebar.selectbox("Modo", ["Simulado", "Real (API)", "Gravado"], key="mode")
if mode != "Real (API)":
    close_session_client(st.session_state)
if mode == "Simulado":
    st.sidebar.info("Simulado · sem chamadas ao backend ou provedores")
    role = st.sidebar.selectbox("Papel simulado", ["demo", "admin"], key="sim_role")
    api = SimulatedAPI(used=st.session_state.used_comparisons)
elif mode == "Gravado":
    st.sidebar.info("Gravado · reapresenta uma resposta sintética salva")
    role = "demo"
    api = SimulatedAPI(recorded=True, used=st.session_state.used_comparisons)
else:
    role = st.session_state.api_role
    base_url = st.sidebar.text_input(
        "URL da API backend", value=os.environ.get("BACKEND_API_URL", "http://localhost:8000")
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
                st.sidebar.error(f"Não foi possível entrar: {exc}")
    else:
        st.sidebar.success(f"Sessão ativa · papel {st.session_state.api_role}")
        if st.sidebar.button("Encerrar sessão", key="logout"):
            logout_client = get_session_client(st.session_state, base_url, st.session_state.api_token)
            try:
                logout_client.logout()
            except APIError:
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
    st.error(f"Não foi possível carregar perfis: {exc}")
    st.stop()

published_profiles = [item for item in profile_items if item.get("state") == "published"]
if not published_profiles:
    st.info("Nenhum perfil publicado está disponível para esta sessão.")
    st.stop()
profile_labels = {f"{item['name']} · v{item['version']}": item for item in published_profiles}
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

chat_tab, compare_tab, inspect_tab = st.tabs(["Conversa", "Comparar Routers", "Inspecionar execução"])
with chat_tab:
    for turn in st.session_state.chat_history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["text"])
    with st.form("message_form", clear_on_submit=True):
        message_text = st.text_area(
            "Mensagem", key="message_input", height=90, placeholder="Ex.: Quando recebo o valor das minhas vendas?"
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
            st.error(f"Falha ao enviar: {exc}. O método selecionado não foi trocado automaticamente.")

    pending = st.session_state.pending_message
    if pending:
        st.warning("O envio pode ter sido concluído no backend. A interface não o reenviará automaticamente.")
        st.write(f"Pergunta pendente: {pending['message']}")
        if mode != "Real (API)":
            st.info("Volte ao modo Real (API) para atualizar esta conversa.")
        elif st.button("Atualizar conversa", key="recover_pending_conversation"):
            try:
                conversation = api.conversation(pending["conversation_id"])
                recovered_answer = find_pending_reply(conversation, pending)
                if recovered_answer is None:
                    st.info("Ainda não há resposta nova para esta pergunta. Atualize novamente mais tarde.")
                else:
                    st.session_state.chat_history.extend(
                        [
                            {"role": "user", "text": pending["message"]},
                            {"role": "assistant", "text": recovered_answer},
                        ]
                    )
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

with compare_tab:
    st.write("A comparação chama somente os dois Routers. O atendimento só começa após você usar a decisão.")
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
            st.error(f"Comparação indisponível: {exc}")
    comparison = st.session_state.comparison
    if comparison and selected_mechanism != comparison["selected_mechanism"]:
        st.session_state.comparison = None
        comparison = None
        st.info("A escolha mudou; o resultado anterior foi invalidado. Compare novamente para criar uma nova decisão.")
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
                st.error(f"A decisão não foi usada: {exc}")

with inspect_tab:
    current = st.session_state.latest_run
    if current:
        st.write(f"Modo: **{current.get('execution_mode', 'unknown')}**")
        st.write(f"Perfil: `{current.get('profile_id')}` · versão `{current.get('profile_version')}`")
        st.write(f"Router: `{current.get('router_mechanism')}` · request `{current.get('request_id')}`")
        st.json(current)
        if mode == "Real (API)" and st.button("Atualizar execução", key="refresh_run"):
            try:
                st.session_state.latest_run = api.run(current["request_id"])
                st.rerun()
            except APIError as exc:
                st.error(f"Execução indisponível: {exc}")
    else:
        st.info("Envie uma mensagem para inspecionar a primeira execução.")
    if st.session_state.recovered_without_request_id:
        st.caption("Resposta recuperada pela conversa; o ID da execução não foi retornado e não foi inferido.")

if role == "admin":
    with st.expander("Administração de perfis"):
        st.write("Perfis publicados são imutáveis. Configure modelos compatíveis e limites de saída por agente.")
        try:
            catalog = api.model_catalog()["models"]
        except APIError as exc:
            st.error(f"Catálogo de modelos indisponível: {exc}")
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
            except (APIError, KeyError) as exc:
                st.error(f"Não foi possível criar o perfil: {exc}")
        drafts = [item for item in profile_items if item.get("state") == "draft"]
        if drafts:
            draft = st.selectbox("Rascunho para editar ou publicar", drafts, format_func=lambda item: item["name"])
            draft_key = f"draft_agents_{draft['profile_id']}_{draft['version']}"
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
                except (APIError, KeyError) as exc:
                    st.error(f"Não foi possível atualizar o rascunho: {exc}")
            if st.button("Publicar versão de rascunho", key="publish_profile"):
                try:
                    result = api.publish_profile(draft["profile_id"], draft["version"])
                    st.success(f"Perfil publicado · v{result['version']}.")
                    st.rerun()
                except APIError as exc:
                    st.error(f"Publicação recusada: {exc}")

if role == "admin" and mode == "Real (API)":
    with st.expander("Base de conhecimento versionada"):
        st.caption("Edite somente orientações fictícias. Cada build cria uma coleção candidata separada.")
        try:
            with st.spinner("Carregando documentos e versões..."):
                kb_documents = api.kb_documents()
                kb_state = api.kb_versions()
        except APIError as exc:
            st.error(f"Não foi possível carregar a base: {exc}")
            kb_documents, kb_state = [], {"versions": []}
        if not kb_documents:
            st.info("Nenhum documento registrado está disponível.")
        else:
            labels = {f"{item['title']} · {item['source_kind']}": item for item in kb_documents}
            selected_doc = st.selectbox("Documento", list(labels), key="kb_document_choice")
            document = labels[selected_doc]
            try:
                with st.spinner("Carregando documento..."):
                    kb_detail = api.kb_document(document["document_id"])
            except APIError as exc:
                kb_detail = None
                st.error(f"Documento indisponível: {exc}")
            if kb_detail:
                st.write(f"Origem: `{kb_detail['source_kind']}` · hash publicado: `{kb_detail['content_hash'][:16]}`")
                if document["editable"]:
                    current_draft = kb_detail.get("draft") or {}
                    draft_rev = current_draft.get("revision", 0)
                    with st.form("kb_draft_form"):
                        edited_text = st.text_area(
                            "Conteúdo fictício", value=current_draft.get("content", kb_detail["content"]), height=260
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
                        except APIError as exc:
                            st.error(f"Rascunho não salvo: {exc}")
                    if current_draft:
                        try:
                            st.code(api.kb_diff(document["document_id"])["diff"], language="diff")
                        except APIError as exc:
                            st.error(f"Diff indisponível: {exc}")
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
                    except APIError as exc:
                        st.session_state.pop("kb_estimate", None)
                        st.error(f"Estimativa indisponível: {exc}")
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
                        st.success(f"Candidata pronta para teste: {candidate['version_id']} · {candidate['state']}.")
                        st.session_state.pop("kb_estimate", None)
                        st.rerun()
                    except APIError as exc:
                        st.error(f"Build recusado ou interrompido: {exc}")
            candidates = [item for item in kb_state.get("versions", []) if item["state"] in ("testing", "ready")]
            if candidates:
                candidate = st.selectbox(
                    "Candidata para testar/ativar",
                    candidates,
                    format_func=lambda item: f"{item['version_id']} · {item['state']}",
                    key="kb_candidate_choice",
                )
                test_cap = st.number_input(
                    "Teto do teste (US$)", min_value=0.0, max_value=5.0, value=0.0, format="%.6f",
                    key="kb_test_max_cost",
                )
                reset_confirmation_context(
                    "kb_test_confirmation_context",
                    (candidate["version_id"], kb_state["active_version_id"], float(test_cap)),
                    "kb_confirm_test",
                )
                confirm_test = st.checkbox("Confirmo o teste da candidata", key="kb_confirm_test")
                test_action = st.button(
                    "Testar candidata", key="kb_test_action",
                    disabled=not confirm_test or candidate["state"] not in ("testing", "ready"),
                )
                if test_action:
                    try:
                        with st.spinner("Testando a candidata..."):
                            test_result = api.kb_test(
                                candidate["version_id"], {"confirmed": True, "max_cost_usd": test_cap}
                            )
                        st.success(f"Teste concluído · {'aprovado' if test_result['passed'] else 'sem resultado'}.")
                        st.rerun()
                    except APIError as exc:
                        st.error(f"Teste interrompido: {exc}")
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
                    except APIError as exc:
                        st.error(f"Ativação recusada: {exc}")
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
                    except APIError as exc:
                        st.error(f"Retorno recusado: {exc}")
