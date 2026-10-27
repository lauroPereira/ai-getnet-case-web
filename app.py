from __future__ import annotations

import os

import streamlit as st

from getnet_web.api import APIClient, APIError, close_session_client, get_session_client
from getnet_web.simulated import SimulatedAPI


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
if "comparison" not in st.session_state:
    st.session_state.comparison = None
if "latest_run" not in st.session_state:
    st.session_state.latest_run = None
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
        send = st.form_submit_button("Enviar mensagem", key="send_message")
    if send and message_text.strip():
        payload = {
            "message": message_text,
            "conversation_id": st.session_state.conversation_id,
            "profile_id": profile_id,
            "profile_version": profile_version,
            "router_mechanism": mechanism,
        }
        try:
            result = api.send_message(payload)
            reply = result["message"]
            st.session_state.conversation_id = reply["conversation_id"]
            st.session_state.chat_history.extend(
                [{"role": "user", "text": message_text}, {"role": "assistant", "text": reply["answer"]}]
            )
            st.session_state.latest_run = result["run"] | {"request_id": reply["request_id"]}
            st.session_state.comparison = None
            st.rerun()
        except APIError as exc:
            st.error(f"Falha ao enviar: {exc}. O método selecionado não foi trocado automaticamente.")

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
        if st.button("Usar esta decisão e iniciar atendimento", key="use_comparison"):
            selected = comparison["selected_mechanism"]
            try:
                result = api.send_message(
                    {
                        "message": comparison["message"],
                        "conversation_id": st.session_state.conversation_id,
                        "profile_id": profile_id,
                        "profile_version": profile_version,
                        "router_mechanism": selected,
                        "comparison_id": comparison["comparison_id"],
                    }
                )
                reply = result["message"]
                st.session_state.conversation_id = reply["conversation_id"]
                st.session_state.chat_history.extend(
                    [{"role": "user", "text": comparison["message"]}, {"role": "assistant", "text": reply["answer"]}]
                )
                st.session_state.latest_run = result["run"] | {"request_id": reply["request_id"]}
                st.session_state.comparison = None
                st.rerun()
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
