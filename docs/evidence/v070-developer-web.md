# Passagem do Developer (web): v0.7.0

## Tarefa 67, rodada 1

- Papel e responsável: Developer do Time B (subagente), despachado pelo Supervisor.
- Objetivo e critérios: [tarefa 67](../tasks/67-interface-operador-web.md), cópia da tarefa do backend no commit `3ccde8d`. Contrato da spec v0.7.0 no mesmo commit: "Contrato entre os times" (Identidade, com `client_id=merchant-assistant-web` no grant password, erros RFC 6749 e `token_required`/`invalid_token`; Serviço de ticketing) e D3, D8, D9, D10.
- Escopo:
  - Novos: `src/getnet_web/ticketing.py`, `tests/test_v070_interface.py`, `docs/tasks/67-interface-operador-web.md` e esta passagem.
  - Alterados: `app.py`, `src/getnet_web/api.py`, `src/getnet_web/simulated.py` (só a assinatura de `login`) e `README.md`.
  - Backend não tocado.
- Estado: implementação concluída pelo Developer; em andamento até a revisão.
- Branch e PR: `codex/v0.7.0-identidade-hitl` em `C:\workspace\ai-getnet-case-web`, sem PR, sem push.
- Base e versão final: base `3bbc3df`; commits `47e1c6c` (código e testes), `742b8dc` (README) e o commit de documentação que traz esta passagem.
- Independência: não-independente (relato do autor).

### O que mudou

- **Login** (`APIClient.login(username, password)`):
  - Envia formulário `application/x-www-form-urlencoded` para `POST /v1/auth/token` com `grant_type=password`, `username`, `password` e `client_id` (`AUTH_CLIENT_ID`, padrão `merchant-assistant-web`).
  - Erros RFC 6749 §5.2 viram `APIError(status, error)`. A tela mostra mensagem neutra ("Usuário ou senha incorretos") sem repetir o que foi digitado. O `429` vira `too_many_attempts`, com o `Retry-After` na mensagem.
  - `logout()` chama `POST /v1/auth/revoke` com Bearer.
  - O formulário limpa os campos, e as chaves do widget saem do `session_state` no envio. O token fica só na sessão.
- **Papel, principal e validade:** lidos do próprio JWT, sem verificar a assinatura (`token_claims`), só para rotular a sessão e escolher as áreas. Quem autoriza continua sendo o backend e o ticketing.
- **Fim de sessão:**
  - `exp` vencido → volta ao login com "Sua sessão expirou ou foi encerrada. Entre novamente."
  - O mesmo vale para `401 token_required`/`invalid_token` na leitura de perfis e em qualquer chamada ao ticketing.
- **Áreas:** operador vê só a aba Ticketing (o script para antes de ler perfis); lojista e demo veem Atendimento e Resultados; admin vê as três.
- **Ticketing** (`TicketingClient` e `render_ticketing`):
  - Fila por estado, com os rótulos Recepção, Atendimento, Envio e Finalização.
  - Detalhe com caso, rascunho, conversa mascarada, histórico e entregas recebidas e enviadas (com tentativas e chave).
  - Botões:
    - Assumir em `new`.
    - Aprovar, Responder e Rejeitar, com caixa de confirmação, em `open` e `pending`. Responder e Rejeitar exigem texto; Aprovar envia o rascunho.
    - Resolver, com confirmação.
  - Cada ato termina com uma mensagem que fica até o próximo desenho e um `st.rerun()`.
  - O "envio pendente" (`202` do ticketing, ou `delivery.status` diferente de `delivered`) aparece como aviso com Atualizar.
- **Atendimento:** um turno com `via=operator` ou rota `human_reply` recebe avatar e a legenda "Atendente humano · rota human_reply", inclusive quando é recuperado pelo GET da conversa. Turnos comuns mantêm o formato anterior.

### Decisões além do texto da tarefa

1. **OpenAPI não sincronizado:**
   - As rotas `/v1/auth/token` e `/v1/auth/revoke` ficaram como constantes em `api.py`, marcadas como provisórias até a sincronização.
   - `openapi-v1.json` e `generated_api.py` não mudaram.
   - `create_session` e `revoke_session` continuam no arquivo gerado, só sem uso.
2. **`idempotency_key`:**
   - Formato `web-<uuid hex>` (36 caracteres, dentro de D10).
   - A chave é guardada junto com a assinatura do clique (ticket, ação, texto).
   - Repetir a mesma ação depois de falha de transporte ou de envio pendente reaproveita a chave.
   - Depois de uma resposta definitiva (`2xx` entregue ou `4xx`), a tentativa é descartada e o próximo clique recebe chave nova.
   - Depois de falha de transporte, a confirmação continua marcada para o reenvio.
3. **Ações também em `pending`.** Se o caso já foi retomado, o assistente responde `409 already_resumed`, e a tela mostra isso com Atualizar.
4. **Ticketing só no modo Real (API).** O modo Simulado continua com os papéis demo e admin, sem operador simulado.
5. **Leituras por GET a cada desenho.** Ao mudar de fila ou de ticket, a tela relê lista e detalhe por GET. Nada de POST fora de clique explícito (há teste para isso).

### Evidência

- **Script estrito:** `PYTEST_ADDOPTS="-p no:cacheprovider --basetemp .pytest_cache/v070-web-strict" .venv-v040/Scripts/python.exe tools/scripts/lint_and_tests.py --strict` → código 0, **59 testes aprovados** (42 da base + 17), ruff limpo, estrutura e skills válidas.
- **Falhas em execuções anteriores, corrigidas:**
  - 2 testes antigos falharam porque o turno do assistente passou a ter `via: None`. Agora `via` só entra quando o turno é de atendente.
  - Nos testes novos, a senha aparecia no `session_state` depois do login de operador. Nesse caminho o script para cedo e o Streamlit não limpava o widget. Corrigido removendo as chaves do formulário no envio, com teste.
- **Testes novos (17):**
  - Áreas por papel (4 casos).
  - Senha errada sem eco; sair revoga o token.
  - `exp` vencido e `401 invalid_token` levam ao login.
  - Operador sem Atendimento e lojista sem Ticketing.
  - Fluxo CA-47 completo, com envio pendente.
  - `claimed_by_other` e `already_resumed` com recuperação.
  - Mesma chave na nova tentativa.
  - Navegação e rerun sem POST.
  - `401` do ticketing encerra a sessão.
  - Turno de atendente marcado no Atendimento.
  - Contrato HTTP do login e da revogação.
  - Rotas, Bearer e corpo do cliente de ticketing.
- Nenhuma chamada paga e nenhum serviço real de pé: clientes falsos e `httpx.MockTransport`.

### Pendências e limitações

- **Sincronização do OpenAPI:** falta o `openapi-v1.json` revisado do backend, com `/v1/auth/*`, `/v1/handoffs` e `/v1/messages`. Quando o Supervisor indicar o commit:
  1. Copiar o JSON.
  2. Acrescentar os aliases em `tools/generate_client.py` e rodar `python tools/generate_client.py`.
  3. Trocar as constantes de `api.py` pelas operações geradas.
  4. Ajustar dois testes: `test_generated_client_operations_match_openapi_v1_contract` (hoje exige 28 operações) e `test_openapi_documents_session_bearer_for_protected_operations` (ainda cita `/v1/sessions`).
- **Formato do turno de atendente:** o contrato não fixa onde fica `via` na resposta de `/v1/messages`. A tela aceita `via=operator` no objeto da mensagem ou `route=human_reply`.
- **Sem integração real:** a tela não foi testada contra o backend nem o ticketing reais; isso fica para o QA na 68.
- **Segredo do client (fora deste repositório):** o contrato agora fixa `TICKETING_CLIENT_SECRET=ticketing-demo-secret-fictitious`. O compose da 66b usa `ticketing-client-secret-fictitious` e precisa ser alinhado na correção da 66b.

### Ação externa e próxima ação

- Ação externa: nenhuma (sem push). Commits com `lauroPereira <lauro.s.pereira@gmail.com>`, identidade conferida.
- Próxima ação: revisão independente da 67; o Supervisor indica o commit do backend para a sincronização do OpenAPI.

## Tarefa 67, rodada 2

- Papel e responsável: Developer do Time B, rodada curta de P3 pedida pelo Supervisor depois do apto para QA ([parecer](v070-reviewer-web.md), commit `03fe6e4`).
- Escopo: `app.py`, `src/getnet_web/ticketing.py`, `tests/test_v070_interface.py` e esta passagem. Base `03fe6e4`; versão final é o commit desta rodada. Não-independente (relato do autor).

### Mudanças

- **P3-1, revogação não confirmada:** se `/v1/auth/revoke` falhar (erro do backend ou de transporte), a sessão termina na interface do mesmo jeito e a barra lateral avisa que o backend não confirmou a revogação e que o token pode continuar válido até expirar. Teste: `test_unconfirmed_revocation_is_announced`.
- **P3-2, repetição após `202`:** clicar de novo depois de um envio pendente reenvia a mesma `idempotency_key`. Teste: `test_repeating_an_action_after_a_pending_send_reuses_the_key`.
- **P3-3, `401` em POST do ticketing:** `401 invalid_token` numa ação encerra a sessão e volta ao login com a mensagem de expiração. As leituras do falso nunca respondem `401`, então só o POST pode encerrar. Teste: `test_401_on_a_ticketing_post_ends_the_session`.
- **P3-4, `2xx` fora do contrato:** o `TicketingClient` transforma em `502 invalid_response` três casos: corpo que não é JSON, JSON que não é objeto, e lista ou detalhe sem os campos esperados.
  - A tela mostra erro com recuperação: "Use Atualizar para conferir o estado antes de repetir".
  - Numa ação, o resultado é desconhecido, então a tentativa e a confirmação são mantidas e um novo clique reenvia a mesma chave.
  - Testes: `test_non_json_2xx_from_the_ticketing_does_not_break_the_screen`, na leitura e na ação; `test_ticketing_client_rejects_bodies_outside_the_contract`.

### Evidência

- Script estrito (`--basetemp .pytest_cache/v070-web-strict`) → código 0, **64 testes aprovados** (59 + 5), ruff limpo.
- Nenhuma chamada paga, nenhum serviço real; sem push.

### Pendente de propósito

Sincronização do OpenAPI (`/v1/auth/*`, `/v1/handoffs`, `via` em `/v1/messages`), quando o Supervisor indicar o commit do backend.

## Tarefa 67, rodada 3

- Papel e responsável: Developer do Time B; sincronização do OpenAPI pedida pelo Supervisor.
- Escopo:
  - `openapi-v1.json`, `tools/generate_client.py` (aliases), `src/getnet_web/generated_api.py` (regenerado), `src/getnet_web/api.py`, `app.py`.
  - `tests/test_interface.py`, `tests/test_v070_interface.py`, `README.md`, a tarefa 67 e esta passagem.
  - Nada lido ou executado no backend.
- Base e versão final: base `7e317f0`; versão final é o commit desta rodada.
- Independência: não-independente (relato do autor).

### Sincronização

- **Fonte:** o OpenAPI exportado pelo Supervisor do backend no commit `7079920`, conferido por sha256 `d5aad2944c7638439d16456f5e6c57ebd383576e9d3aae6d52b36cf73ebbfd8e`.
  - 36 caminhos, entre eles `/v1/auth/token`, `/v1/auth/revoke`, `/v1/handoffs*`, `/v1/integrations/ticketing/events`, `/v1/messages` e `/v1/conversations/{id}`.
  - Copiado sem alteração para `openapi-v1.json`, que tem o mesmo sha256.
- **Processo do projeto:**
  - Em `tools/generate_client.py`, os aliases `create_session` e `revoke_session` (rotas que saíram, D3) deram lugar a `auth_token` e `auth_revoke`.
  - `python tools/generate_client.py` gerou 28 operações em `generated_api.py`.
  - Não acrescentei `/v1/handoffs`: a tela de ticketing fala com o serviço de ticketing, não com essas rotas.
- **`api.py`:** sem as constantes provisórias.
  - `login` usa `OPERATIONS["auth_token"]`, com formulário RFC 6749, como declara o `requestBody` `application/x-www-form-urlencoded`.
  - `logout` usa `OPERATIONS["auth_revoke"]`, com Bearer, como declara a `security` `SessionBearer`.

### `via`, `operator_action` e `protocol`

- **O que o OpenAPI diz:**
  - `via` aparece declarado no `MessageResponse` (`agent`|`operator`, "operator: the reply was written or approved by a human support agent"), junto da rota `human_reply`.
  - A resposta 200 de `/v1/messages` e a de `/v1/conversations/{id}` não têm schema no arquivo (`{}`), e `operator_action` não aparece no OpenAPI.
- **O que a web faz:**
  - Lê `via` no topo da resposta de `/v1/messages` (pedido do Supervisor) e também no objeto `message`, além de aceitar `route=human_reply`.
  - Nos turnos de operador, guarda `operator_action` (no turno ou no topo da resposta) e `protocol` (no turno ou em `handoff.protocol`).
  - A legenda mostra, por exemplo, "Atendente humano · rota human_reply · ação resposta do atendente · protocolo HO-20261103-ABC123". Isso vale tanto para a resposta do envio quanto para o turno recuperado pelo GET da conversa.
- **Testes novos:**
  - `test_openapi_snapshot_declares_the_attendant_turn_and_the_form_token_endpoint`.
  - `test_via_at_the_top_of_the_v1_response_marks_the_attendant_turn`.
  - `test_conversation_operator_turn_shows_action_and_protocol`.
- **Testes antigos ajustados:**
  - `test_openapi_documents_session_bearer_for_protected_operations` agora confere `bearerFormat: JWT`; `/v1/auth/token` sem `security`; `/v1/auth/revoke` com `SessionBearer`; ausência de `/v1/sessions`; e as operações geradas `auth_token`/`auth_revoke`.
  - A contagem de 28 operações continua valendo.

### Evidência

- Script estrito (`--basetemp .pytest_cache/v070-web-strict`) → código 0, **67 testes aprovados** (64 + 3), ruff limpo.
- Na primeira execução, 1 teste antigo falhou porque comparava o esquema de segurança exatamente, e o novo declara `bearerFormat` e descrição. O teste foi ajustado.
- Nenhuma chamada paga, nenhum serviço real; sem push.

### Limitações

- O contrato pode ter ajuste fino depois das revisões de 63 a 66; isso pede nova cópia do OpenAPI e nova geração.
- Sem schema de resposta para `/v1/messages` e `/v1/conversations/{id}`, o lugar de `operator_action` vem do pedido do Supervisor, não do OpenAPI. A tela aceita os dois lugares.

### Próxima ação

Revisão da rodada 3.

## Tarefa 67, rodada 4

- Papel e responsável: Developer do Time B, correções do [parecer da rodada 3](v070-reviewer-web.md) (commit `40b3797`; o candidato revisado foi `733cc37`).
- Escopo: `app.py`, `tests/test_v070_interface.py`, `README.md`, a tarefa 67 e esta passagem. Backend não tocado e nada executado nele; li só a tarefa e a spec do repositório do backend e usei as formas capturadas pelo Reviewer.
- Base e versão final: base `40b3797`; versão final é o commit desta rodada. Independência: não-independente (relato do autor). Identidade do Git conferida antes do commit (`lauroPereira <lauro.s.pereira@gmail.com>`).

### P2-1: troca de usuário não herda a conversa

- **Mudança:** novas funções em `app.py`.
  - `conversation_defaults()` é a fonte única dos valores iniciais de `chat_history`, `conversation_id`, `backend_message_count`, `pending_message`, `recovered_without_request_id`, `latest_run`, `runs`, `case_view`, `comparison`, `evaluation_estimate`, `comparison_estimate`, `profile_comparison_id`, `profile_comparison` e `pending_run_selection`. O laço de inicialização passou a usá-la.
  - `forget_conversation()` repõe esses valores e apaga o que o usuário digitou, escolheu ou estimou (`USER_SCRATCH_KEYS`: `message_input`, `compare_input`, `results_run_choice`, os contextos de confirmação de avaliação e comparação, `kb_estimate`, `admin_notice` e os contextos de confirmação da base de conhecimento).
  - `end_session(notice, forget=False)`: **Encerrar sessão** passa `forget=True`, com ou sem revogação confirmada.
- **Quem é o dono da conversa:** `conversation_owner` guarda o `sub` do token que a criou. Não é apagado na expiração nem no `401`, para que o mesmo usuário volte a ela.
  - No login, se o `sub` for ausente ou diferente de `conversation_owner`, `forget_conversation()` roda antes de gravar o token.
  - Mesmo `sub` depois de expirar mantém a conversa.
  - Token sem `sub` nunca reaproveita, porque não dá para provar que é o mesmo usuário.
- **Decisão além do texto do parecer:** o primeiro login da sessão do navegador (`conversation_owner` vazio) também limpa. Efeito visível: uma conversa começada no modo Simulado ou Gravado some ao entrar no modo Real. Escolhi a regra conservadora (só reaproveita quando o `sub` é conhecido e igual); trocar para "limpa só se havia dono anterior" é uma linha.
- **Não é limpo:** `used_comparisons` (conjunto do modo simulado), as seleções da tela (perfil, cenário, filtros) e o estado do ticketing, que o `end_session` já zerava.

### P2-2: recuperação da resposta com a ordem real da conversa

- **Mudança em `find_pending_reply`:** devolve `(turno, contagem consumida)` ou `None`.
  1. Primeiro `assistant` depois da pergunta pendente, como antes.
  2. Sem ele, o último turno de operador (`via=operator` ou rota `human_reply`) entre o baseline e a pergunta, que é a ordem que a entrega D8 grava: `[merchant Q1, assistant(agent), assistant(operator), merchant pendente]`.
  3. Uma mensagem de lojista seguinte não impede mais o item 2; só encerra a busca do item 1.
- **Sem turno repetido:** o item 2 olha só depois do baseline. A contagem `backend_message_count` passa a ser a posição consumida (turno da resposta, ou a pergunta no item 2, mais um) em vez de `+= 2`. Nos formatos `[pergunta, resposta]` e `[operador, pergunta]` dá o mesmo `+2`; difere só quando havia mensagens não vistas entre o baseline e a pergunta, e aí evita exibi-las depois.
- **Limite do dado:** a conversa não expõe se o turno do operador já foi entregue. Com `[operador, pergunta]` sem outra pista, a tela o trata como a resposta, como o parecer pediu. O baseline é o que o separa de um turno já mostrado.
- **Teste corrigido:** `test_conversation_operator_turn_shows_action_and_protocol` agora usa a ordem capturada, com o envio de Q2 perdido por timeout, e confere a legenda "Atendente humano · rota human_reply · ação resposta do atendente · protocolo HO-20261026-0F1286".

### P3

- **P3-1:** o ramo de `via`/`operator_action` no topo do envelope saiu de `assistant_turn(text, reply)`, que não recebe mais o envelope, e o teste `TopLevelVia` foi removido. Nenhum caminho real dependia dele: o envelope real é `{"message": {..., "via": ...}, "run": ...}`, e `operator_action` e `protocol` só vêm nos turnos do GET da conversa. Um auxiliar `is_operator_turn` serve ao turno e à recuperação. O formato real do envio fica coberto por `test_attendance_marks_the_attendant_turn` e por `test_recovery_does_not_show_a_delivered_operator_turn_twice`, que usa a mensagem de operador capturada.
- **P3-2:** `README.md:31` agora fala do backend em que a interface entra com usuário e senha (v0.7.0), sem as chaves de acesso.
- **P3-3:** o sha256 do blob de `openapi-v1.json` em `733cc37` é `8cace9073e7cfae27f79722a0237bdd4c7648f01643bb30ad8294ed092931fc9`. O `d5aad2944c76…` citado na rodada 3 é o do arquivo de trabalho com CRLF (`core.autocrlf=true`); a rodada 3 fica como histórico e vale este. A tarefa 67 foi corrigida.

### Testes novos e alterados

Os testes novos estão em `tests/test_v070_interface.py`, com um assistente falso (`ScriptedChat`) que grava as mensagens na ordem do backend real, formas capturadas pelo Reviewer e nenhuma rede.

- `test_conversation_operator_turn_shows_action_and_protocol` (corrigido): ordem real, resposta perdida por timeout, recuperação mostra o turno do operador uma vez, contagem 4, rerun sem GET nem POST.
- `test_recovery_does_not_show_a_delivered_operator_turn_twice`: o turno entregue pelo envio (D8) não volta numa recuperação posterior; a pergunta sem resposta fica pendente; depois da resposta automática a contagem vai a 6.
- `test_recovery_prefers_the_assistant_answer_that_follows_the_question` e `test_recovery_without_an_operator_turn_or_an_answer_keeps_the_question_pending`.
- `test_logout_then_another_principal_does_not_inherit_the_conversation`: A conversa (com execução, comparação e pergunta pendente), sai, B entra e não vê nada de A; a mensagem de B não leva o `conversation_id` de A.
- `test_expiry_then_another_principal_does_not_inherit_the_conversation` (2 casos: outro `sub`; token sem `sub`) e `test_expiry_then_the_same_principal_keeps_the_conversation`.
- Removido: `test_via_at_the_top_of_the_v1_response_marks_the_attendant_turn`.

### Evidência

- **Comando:** `PYTHONPATH=src PYTEST_ADDOPTS="-p no:cacheprovider --basetemp .pytest_cache/t67-r4-dev-20261103-204018" .venv-v040/Scripts/python.exe tools/scripts/lint_and_tests.py --strict` → código 0, **73 testes aprovados** em 68,23 s (67 − 1 removido + 7 novos), Ruff "All checks passed!", estrutura e skills válidas. Uma execução anterior da mesma árvore de código (`...-20261103-203756`) também terminou com 73 aprovados, antes de eu só mexer na documentação.
- **Mutação em `app.py`** (restaurado byte a byte do backup depois de cada rodada), subconjunto de testes do arquivo `test_v070_interface.py`: 10 mutantes, 10 mortos.
  - Logout sem `forget`; login que nunca esquece; login que aceita `sub` ausente; expiração que esquece; `USER_SCRATCH_KEYS` sem efeito; `comparison` fora dos valores iniciais.
  - Recuperação sem o ramo do operador; ramo do operador sem respeitar o baseline; contagem `+= 2` no lugar da consumida.
  - `runs` fora dos valores iniciais morreu por exceção (chave ausente), não por asserção.
- Nenhuma chamada paga, nenhum serviço real, sem push.

### Limitações

- Navegador real, backend real e ticketing real não foram exercitados (ficam para o QA). Os testes usam `AppTest` e clientes falsos com as formas capturadas pelo Reviewer.
- No `AppTest`, as chaves dos campos de texto sobrevivem a logout e login. Por isso `message_input` e `compare_input` são apagados à mão; num navegador o Streamlit provavelmente já descarta widgets não desenhados, mas não verifiquei.
- A recuperação não distingue um turno de operador entregue antes por outro caminho (por exemplo outro cliente lendo a conversa) de um entregue com a pergunta pendente, porque o GET da conversa não expõe esse estado.

### Próxima ação

Nova revisão independente da rodada 4; QA depois, com navegador, backend e ticketing reais.

## Tarefa 67, rodada 5

- Papel e responsável: Developer do Time B, resíduo do [parecer da rodada 4](v070-reviewer-web.md) (commit `f134102`; o candidato revisado foi `9f6a894`).
- Escopo: `app.py`, `tests/test_v070_interface.py`, a tarefa 67 e esta passagem. Backend não tocado.
- Base e versão final: base `f134102`; versão final é o commit desta rodada. Independência: não-independente (relato do autor). Identidade do Git conferida antes do commit (`lauroPereira <lauro.s.pereira@gmail.com>`).

### P2-A: a tela de ticketing não passa de um operador para outro

- **Causa:** na rodada 4 eu supus que o Streamlit descartava os widgets que não são desenhados (a tela de login termina em `st.stop()`/`st.rerun()`). O parecer mostrou que não: o estado dos widgets com chave sobrevive, e eu só tinha coberto os campos do chat. O rascunho de uma operadora aparecia para o próximo operador no mesmo navegador.
- **Mudança em `app.py`:**
  - `USER_SCRATCH_KEYS` passou a listar todo widget de valor com chave constante: as sete chaves do ticketing citadas no parecer (`ticket_status`, `ticket_choice`, `ticket_action`, `ticket_confirm`, `ticket_solve_confirm`, e `ticket_text_reply`/`ticket_text_reject`, que são montadas por `ticket_text_{ação}`) e as que também tinham escapado.
  - Escaparam: `profile`, `scenario_choice`, `comparison_selection`, `eval_confirm`, `eval_repeat`, `cmp_profiles`, `cmp_scenarios`, `cmp_repetitions`, `cmp_mechanism`, `cmp_confirm`, `cmp_bar_choice`, `cmp_point_choice`, `cmp_cell_choice`, `kb_document_choice`, `kb_candidate_choice`, `kb_build_max_cost`, `kb_test_max_cost` e as quatro caixas `kb_confirm_*`.
  - `is_user_scratch(key)` cobre também as chaves montadas em tempo de execução: `ticket_text_*` e os formulários de perfil do administrador (`create_*`/`update_*` terminando em `_model`, `_output_enabled` ou `_output_tokens`).
  - `forget_conversation()` varre `st.session_state` e apaga as chaves que `is_user_scratch` reconhece. Roda no logout e no login de outro `sub` (ou sem `sub`), como na rodada 4.
- **O que continua de propósito:** `mode`, `sim_role` e a URL do backend (escolhas do navegador, não do usuário); botões não guardam estado.
- **Expiração e mesmo operador:** a expiração não apaga nada, então fila, ticket, ação e rascunho voltam para o mesmo `sub`. A caixa de confirmação volta desmarcada, porque `end_session` zera `ticket_confirm_context` e `ticket_attempt` (a chave de idempotência); uma sessão nova pede a confirmação de novo.

### P3-A: testes para os mutantes que sobreviveram

Em `tests/test_v070_interface.py`:

- R05: `test_tokens_without_sub_never_share_a_conversation` (sem `sub` nos dois tokens).
- R13: `merchant_a_conversation` confere que `results_run_choice` existe para o A, e a conferência de que B não o recebe ficou no teste do logout e no auxiliar comum.
- R18: `test_recovery_takes_the_last_operator_turn_before_the_question`.
- R19: `test_recovery_stops_at_the_next_merchant_message` (a resposta a uma mensagem posterior não é a resposta à pendente; depois, com o turno do operador gravado antes da pergunta, ele passa a valer).
- R21: `test_recovery_ignores_assistant_turns_that_are_not_the_operators` (contagem atrasada, só uma resposta automática antes da pergunta).
- R26: `test_recovery_checks_the_text_of_the_question` (o assistente falso ganhou o desfecho `lost`, em que o pedido nunca chegou).
- P2-A: `test_another_operator_does_not_inherit_the_previous_operators_screen` (2 casos, logout e expiração: chaves ausentes da sessão, fila padrão, primeiro ticket, ação `approve`, texto vazio, caixas desmarcadas, envio desabilitado), `test_expiry_then_the_same_operator_keeps_the_draft` e `test_admin_form_choices_do_not_reach_the_next_admin`.
- Auditoria: `test_every_keyed_value_widget_is_dropped_with_the_conversation` lê `app.py` por AST e exige que todo `text_input`, `text_area`, `number_input`, `selectbox`, `radio`, `checkbox` e `multiselect` com chave constante esteja em `USER_SCRATCH_KEYS` (ou seja da barra lateral). Um widget novo sem entrada na lista derruba o teste.

### Evidência

- **Comando:** `PYTHONPATH=src PYTEST_ADDOPTS="-p no:cacheprovider --basetemp .pytest_cache/t67-r5-dev-20261103-212040" .venv-v040/Scripts/python.exe tools/scripts/lint_and_tests.py --strict` → código 0, **83 testes aprovados** em 89,69 s (73 + 10), Ruff "All checks passed!", estrutura e skills válidas.
- **Mutação em `app.py`** (restaurado byte a byte depois): 15 mutantes, 15 mortos, cada um com `-x` no subconjunto de testes que cita a correção.
  - Limpeza: prefixo `ticket_text_` desligado; chaves `create_`/`update_` desligadas; cada uma das cinco chaves fixas do ticketing retirada da lista; `results_run_choice` retirada (R13); `cmp_profiles` e `eval_repeat` retiradas (pegos pela auditoria).
  - Sessão: `sub` ausente reaproveita a conversa (R05).
  - Recuperação: primeiro turno de operador no lugar do último (R18); sem parada no próximo lojista (R19); qualquer `assistant` conta no ramo do operador (R21); sem a guarda do texto da pergunta (R26).
- Nenhuma chamada paga, nenhum serviço real, sem push.

### Limitações

- Navegador real, backend real e ticketing real continuam sem exercício (QA). A limpeza foi demonstrada com `AppTest`. A suposição da rodada 4 sobre o descarte automático de widgets não valeu; a correção não depende dela.
- A auditoria por AST só vê chaves constantes. Um widget novo com chave montada em tempo de execução precisa entrar em `is_user_scratch`; hoje isso cobre `ticket_text_*` e os formulários de perfil do administrador.
- Fica para o fechamento (P3-B, T68): o README do backend ainda fala de chave de acesso na seção "Streamlit interface".

### Próxima ação

Nova revisão independente da rodada 5; QA depois, com navegador, backend e ticketing reais.

## Tarefa 67, rodada 6

- Papel e responsável: Developer do Time B, resíduo do [parecer da rodada 5](v070-reviewer-web.md) (commit `b162d73`; o candidato revisado foi `0bc8d86`).
- Escopo: `app.py`, `tests/test_v070_interface.py`, a tarefa 67 e esta passagem. Backend não tocado.
- Base e versão final: base `b162d73`; versão final é o commit desta rodada. Independência: não-independente (relato do autor). Identidade do Git conferida antes do commit (`lauroPereira <lauro.s.pereira@gmail.com>`).

### P2-B: widgets sem `key` passavam ao próximo principal

- **Causa:** um widget sem `key` tem id automático (rótulo e parâmetros) e o estado dele também sobrevive à tela de login, que termina em `st.stop()`. A limpeza da rodada 5 só alcançava chaves.
- **Widgets que ganharam `key`** (levantados por AST em `app.py`; sobrou sem chave só a URL do backend):
  - Rádio "Router do atendimento": `router_choice_{profile_id}`. A chave é por perfil de propósito: o padrão do rádio depende do perfil (JEV no híbrido, LLM nos demais), e uma chave única o fixaria na primeira escolha. Com uma chave por perfil, o comportamento anterior continua: cada perfil começa no seu padrão, inclusive ao voltar a ele (teste `test_the_router_default_still_follows_the_profile`).
  - "Nome do novo perfil" (`profile_draft_name`), "Permitir seleção pela sessão demo" (`profile_allow_demo`) e "Rascunho para editar ou publicar" (`profile_draft_choice`).
  - "Conteúdo fictício" da base: `kb_draft_text_{documento}_{revisão do rascunho}`. Uma chave fixa faz o editor mostrar o texto do documento anterior ao trocar de documento (o teste `test_another_document_shows_its_own_text_in_the_editor` falha com ela); um rascunho salvo, de revisão nova, é um editor novo com o texto salvo.
  - "Fonte oficial · somente leitura" (desabilitado, sem entrada do usuário): `kb_source_text_{documento}`, só para a auditoria poder exigir chave em tudo.
- **Limpeza:** `USER_SCRATCH_KEYS` ganhou as três chaves fixas e `is_user_scratch` ganhou os prefixos `router_choice_`, `kb_draft_text_` e `kb_source_text_`.
- **Fica de fora, de propósito:** a URL do backend (escolha do navegador), `mode` e `sim_role`.
- **Testes:**
  - `test_another_merchant_does_not_inherit_the_router_choice` (logout e expiração): A escolhe JEV e envia; B entra e envia com o padrão LLM.
  - `test_another_admin_does_not_inherit_the_profile_and_knowledge_base_forms` (logout e expiração): A preenche e envia os dois formulários, escolhe o segundo rascunho e sai; B vê nome "Ajustado", permissão marcada, primeiro rascunho e o texto da base do servidor, e nenhum "secreto" no `session_state`.

### P3-B: auditoria por AST

`test_every_input_widget_has_a_key_that_is_dropped_with_the_conversation` substitui a da rodada 5. Ele lê `app.py` e compila a `is_user_scratch` real a partir dele, de modo que a auditoria acompanha o app.

- Exige `key` em toda chamada de widget de entrada: `text_input`, `text_area`, `number_input`, `selectbox`, `radio`, `checkbox`, `multiselect`, `slider`, `select_slider`, `toggle`, `date_input`, `time_input`, `color_picker`, `file_uploader`, `camera_input`, `audio_input`, `chat_input`, `segmented_control`, `pills`, `feedback` e `data_editor`. Botões não entram: são momentâneos e não guardam estado.
- Aceita chave constante coberta por `is_user_scratch` ou pela lista de exceções por desenho (`mode`, `sim_role`, `login_username`, `login_password`), e f-string pelo prefixo constante, que precisa estar coberto. As chaves de `render_agent_configuration` (`{key_prefix}_{agente}_...`) são avaliadas como `create_...`.
- A única chamada sem `key` aceita é a da URL do backend, identificada pelo rótulo. `**kwargs` e chave em variável reprovam, embora o app não tenha nenhum caso.
- Conferido o app inteiro: mais de 40 chamadas vistas, nenhum problema.

### P3-C: confirmação de "Resolver"

`ticket_solve_confirm` agora tem um contexto, como as outras confirmações: `reset_confirmation_context("ticket_solve_confirm_context", ticket_id, "ticket_solve_confirm")`. Trocar de ticket desmarca a caixa, e `ticket_solve_confirm_context` entrou em `TICKET_STATE_KEYS`, que `end_session` zera, então uma sessão nova do mesmo operador também a desmarca. "Resolver" volta desabilitado nos dois casos. Teste: `test_solve_confirmation_is_renewed_on_another_ticket_and_in_a_new_session` (sem nenhum POST).

### P3-D: tentativa pendente

`test_a_pending_attempt_does_not_reach_another_principal` (logout e expiração): a falha de transporte deixa `ticket_attempt` com o texto e a chave; no logout ou na expiração ele some, no login do outro operador continua vazio, e a mesma ação (mesmo ticket, ação e texto) sai com chave nova.

### Evidência

- **Comando:** `PYTHONPATH=src PYTEST_ADDOPTS="-p no:cacheprovider --basetemp .pytest_cache/t67-r6-dev-20261103-220821" .venv-v040/Scripts/python.exe tools/scripts/lint_and_tests.py --strict` → código 0, **92 testes aprovados** em 101,52 s (83 − 1 auditoria substituída + 10 novos), Ruff "All checks passed!", estrutura e skills válidas.
- **Mutação em `app.py`** (restaurado byte a byte depois): 11 mutantes, 11 mortos, cada um por teste nomeado.
  - Limpeza: prefixo `router_choice_` fora; `profile_draft_name`, `profile_allow_demo` e `profile_draft_choice` fora da lista; prefixo `kb_draft_text_` fora; chave constante no editor da base (pego também pelo teste do documento); rádio sem `key`.
  - Auditoria: prefixo `kb_source_text_` fora.
  - Confirmação de "Resolver": sem o reinício por ticket; contexto fora de `TICKET_STATE_KEYS` (expiração).
  - P3-D: `ticket_attempt` fora de `TICKET_STATE_KEYS`.
- Nenhuma chamada paga, nenhum serviço real, sem push.

### Limitações

- Navegador real, backend real e ticketing real continuam sem exercício (QA); a limpeza foi demonstrada com `AppTest`.
- A auditoria não vê widgets criados por outro meio que uma chamada `st.<widget>(...)` (por exemplo uma função auxiliar que receba o objeto `st`), nem chaves em variável; ela reprova as que vê, mas o app não tem nenhuma.
- O texto somente leitura da base ganhou chave só para a auditoria; não guarda entrada do usuário.

### Próxima ação

Revisão estreita da rodada 6, só nos pontos P2-B e P3-B a P3-D; QA depois, com navegador, backend e ticketing reais.
