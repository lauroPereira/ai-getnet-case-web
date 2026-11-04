# Reviewer v0.7.0 (Time B, projeto web)

Pareceres do Reviewer independente do Time B sobre o projeto web na v0.7.0. Gravado pelo Supervisor a partir do relatório do Reviewer (que não tem escrita).

## Tarefa 67, rodada 1 — apto para QA

- **Papel:** Reviewer em contexto frio, independente; leu a tarefa só até "Validação" e não leu `docs/evidence/v070-developer-web.md`. Contrato conferido em `f2a6b60:docs/specs/identidade-hitl-v070.md` do backend (Identidade, Mensagens e conversa, Serviço de ticketing, D3, D9, D10).
- **Candidato:** `C:\workspace\ai-getnet-case-web`, branch `codex/v0.7.0-identidade-hitl`, HEAD `47bc408`, base `3bbc3df` (`app.py`, `src/getnet_web/api.py`, `simulated.py`, novo `ticketing.py`, `tests/test_v070_interface.py`, `README.md`); HEAD e árvore iguais antes e depois.
- **Parecer:** apto para QA — sem P1 nem P2; 4 P3.

### Comandos e números

- Script estrito (`--basetemp .pytest_cache/v070-web-review-strict`): **59 passed**, Ruff limpo, código 0.
- Re-derivação no nível HTTP (AppTest com os clientes reais `APIClient` e `TicketingClient` sobre `httpx.MockTransport`, contando requisições):
  - login: um POST em `/v1/auth/token` com `grant_type=password`, `username`, `password`, `client_id=merchant-assistant-web`; operador vê só Ticketing;
  - senha de prova ausente de `session_state` e de qualquer elemento da tela; token só em `session_state`;
  - 4 trocas de fila, reruns, escolha de ação, texto, confirmação e "Atualizar" → **0 POST**;
  - envio com `ReadTimeout`, depois `202` ("Envio pendente", botão desabilitado), depois `200` após reconfirmar: **os 3 POSTs com a mesma `idempotency_key`** (`web-<32 hex>`, dentro de D10); clique novo após sucesso → chave nova; `409 already_resumed` com recuperação;
  - `401 invalid_token` do ticketing → volta ao login com "Sua sessão expirou…" e apaga o token; logout chama `/v1/auth/revoke` com Bearer e apaga o token.
- Mutação manual numa cópia: **10/12 mortas** (chave sempre nova, sem confirmação, sem checagem de `exp`, senha no estado, operador vendo tudo, sem revoke, sem marca de atendente, `client_id` errado, POST no rerun, falha de transporte zerando a chave).

### Critérios

Comprovados: CA-47 na tela (recepção, assumir, três ações, envio pendente, resolução, `409 claimed_by_other` e `already_resumed` com recuperação); repetição após falha com a mesma chave; áreas por papel; token expirado ou 401 volta ao login; nenhum POST em navegação ou rerun; senha nunca persistida nem exibida; marca de atendente ("Atendente humano · rota human_reply"); script estrito com código 0.

### P3

1. Falha da revogação engolida em silêncio (`app.py:845-851`); sugerido avisar que a revogação não foi confirmada.
2. Repetição após `202` sem teste (mutação que descarta a chave depois do `202` sobrevive).
3. `401` em POST do ticketing sem teste (mutação em `ticket_failure`, `app.py:~573`, sobrevive; o GET seguinte também encerra a sessão).
4. `2xx` não JSON derruba a tela (`ticketing.py:54`), borda fora do contrato.

### Depende da sincronização do OpenAPI (pendente de propósito)

Rotas `/v1/auth/token` e `/v1/auth/revoke` fixadas à mão em `api.py`; `generated_api.py:4-5` ainda lista `create_session`/`revoke_session` (não usados); `/v1/messages` sem schema de resposta e sem `via` no `openapi-v1.json` (a tela aceita `via=operator` ou `route=human_reply`); turnos de conversa com `operator_action` e `protocol` ainda não exibidos.

## Tarefa 67, rodada 2 — apto para QA (mantido)

- **Papel:** revalidação de escopo pelo mesmo Reviewer, sem a passagem do Developer.
- **Candidato:** diff `03fe6e4..0f99b2b` (`app.py`, `src/getnet_web/ticketing.py`, `tests/test_v070_interface.py` com 5 testes novos); HEAD e árvore iguais antes e depois.
- **Números:** script estrito (`--basetemp .pytest_cache/v070-web-review-r2-strict`) **64 passed** (eram 59), Ruff limpo, código 0.
- **Sonda HTTP refeita onde o diff toca:** `GET /tickets` com HTML ou `tickets` que não é lista → "Não foi possível ler o ticketing…" sem exceção; ação com `200` texto, `200` lista e `200` válido → `invalid_response` nas duas primeiras com confirmação mantida, **os 3 POSTs com a mesma `idempotency_key`**, nenhum POST extra; logout com revoke fora do ar ou `500` → token apagado e aviso de revogação não confirmada; com `401` ou `204` → token apagado sem aviso; senha fora do `session_state`.
- **Mutação:** 6/6 mortas, inclusive as duas que sobreviviam na rodada 1 (`pending-resets-key`, `no-end-on-401-ticket`), além de `revoke-notice-off`, `invalid-resp-resets-key`, `client-json-unguarded`, `ticket-shape-unguarded`.
- **P3 da rodada 1:** P3-1 (aviso `REVOKE_UNCONFIRMED`, `app.py:851-859`), P3-2, P3-3 e P3-4 (`ticketing.py:54-72`, `502 invalid_response`, chave e confirmação mantidas) atendidos. O que depende da sincronização do OpenAPI segue igual e não conta como defeito.

## Tarefa 67, rodada 3 — correções necessárias

- **Papel:** Reviewer em contexto frio, independente (subagente `reviewer`, sonnet/xhigh, despacho de 2026-11-03 20h00). Leu a tarefa só até "Validação"; não abriu as passagens do Developer. Um `grep` por resquícios de `/v1/sessions` imprimiu incidentalmente a linha 65 da tarefa 67 do web (relato da rodada 3); o Reviewer declara não ter usado essa linha, e as conclusões foram re-derivadas.
- **Candidato:** diff `7e317f0..733cc37`, conferido no worktree destacado `C:\workspace\ai-getnet-case-wt\review-web`; HEAD e árvore iguais antes e depois. O worktree do backend `review-a2`, usado só para o export em `7079920`, foi reposicionado pelo Supervisor para outra revisão durante esta; o Reviewer refez os exports de `5ebbfa6` e `386025a` por `git archive`.
- **Números:** script estrito do web (`--basetemp .pytest_cache/t67-r3-rev-20261103-200309`) **67 passed**, Ruff limpo, skills ok, código 0. Mutação em cópias: 20 mutantes, 19 mortos; sobrevivente M11 (desvio por `route == "human_reply"` sem `via`), equivalente no contrato.
- **Sincronização comprovada:**
  - `openapi-v1.json` equivale ao export de `create_app().openapi()` do backend em `7079920`, `5ebbfa6` e `386025a` (exports idênticos byte a byte). O sha256 do blob é `8cace907…31fc9`; `d5aad294…` é o do arquivo de trabalho com CRLF.
  - `generated_api.py` igual ao regenerado por `tools/generate_client.py` (28 operações); só os dois aliases mudaram no gerador.
  - Login por formulário RFC 6749 e logout com Bearer, conferidos contra o `create_app()` real do backend em processo, com modelo programado e sem rede. Nenhuma rota `sessions`. Turno de atendente marcado no caminho principal. Navegação e rerun sem POST; mesma chave de idempotência na nova tentativa.
- **P2-1 — troca de usuário herda o estado do anterior.** `app.py:579-589` (`SESSION_KEYS`, `end_session`) e logout em `app.py:871-880` limpam só token, papel, principal e ticketing; ficam `chat_history`, `conversation_id`, `backend_message_count`, `pending_message`, `case_view`, `runs` e `latest_run`. O lojista B vê a conversa de A, e a próxima mensagem de B sai com o `conversation_id` de A (`404` no backend real). Isolamento do backend intacto. Corrigir limpando o estado de conversa no logout e quando o `sub` do novo token diferir do anterior.
- **P2-2 — recuperação da resposta humana entregue por D8 falha com a ordem real da conversa.** `app.py:238-257` (`find_pending_reply`) e `app.py:1004-1016`. A conversa real é `[merchant, assistant(agent), assistant(operator), merchant]`, e a função só procura `assistant` depois do `merchant` pendente. O teste `tests/test_v070_interface.py:610-638` usa uma ordem que o backend não grava. Com a resposta perdida por timeout, "Atualizar conversa" nunca mostra a resposta humana, contra o propósito de D8.
- **P3-1:** ramo e teste de `via`/`operator_action` no topo do envelope (`app.py:211-226`, `tests/test_v070_interface.py:591-607`) descrevem formato que o backend não envia; o envelope real é `{"message": {..., "via": ...}, "run": ...}`.
- **P3-2:** `README.md:31` ainda cita as chaves de acesso `/v1`.
- **P3-3:** hash do snapshot depende de `core.autocrlf`; passagens devem citar o do blob.
- **P3-4:** desvio por rota sem teste isolado (M11); só registro.
- **Não comprovado:** navegador real e serviço de ticketing real (ficam para o QA); demais rotas `/v1` sob D20/D21; texto do `Retry-After` no `429`.

## Tarefa 67, rodada 4 — correções necessárias (um P2 residual)

- **Papel:** Reviewer novo em contexto frio (subagente `reviewer`, sonnet/xhigh); leu a tarefa até "Validação", o contrato e o parecer da rodada 3; não leu as passagens do Developer.
- **Candidato:** `9f6a894`, diff `733cc37..9f6a894` em `app.py`, `tests/test_v070_interface.py` e `README.md`, no worktree `review-web`; HEAD e árvore iguais antes e depois.
- **Números:** script estrito **73 passed**, Ruff limpo, código 0. Mutação: 26 mutantes nas correções, 16 mortos.
- **Rodada 3:** P2-1 corrigido para conversa e campos do lojista (22/22 nas sondas de logout, revoke falho, expiração com outro `sub` ou sem `sub`, mesmo principal mantém); P2-2 corrigido (ordem real com timeout recupera a resposta humana uma vez, sem repetir em reruns; replay dos payloads reais passa; as mesmas sondas falham em `733cc37`); P3-1 e P3-2 corrigidos. Sem regressão em navegação sem POST, chave de idempotência e login/logout do contrato.
- **P2-A — rascunho do operador vaza para outro operador no mesmo navegador.** `USER_SCRATCH_KEYS` (`app.py:597-606`) e `forget_conversation` (`app.py:628`) não cobrem `ticket_text_reply`, `ticket_text_reject`, `ticket_action`, `ticket_confirm`, `ticket_solve_confirm`, `ticket_status` e `ticket_choice` (`app.py:760-819`). Como a tela de login termina em `st.stop()`/`st.rerun()`, o Streamlit não descarta os widgets. Sonda: A digita um rascunho, sai (ou expira) e B entra → texto visível em 6/6 cenários; se B confirmar, o texto de A sai em nome de B com chave nova. Já existia em `733cc37`.
- **P3-A:** mutantes sobreviventes por falta de teste: R05 (`sub` ausente nos dois tokens), R13 (`results_run_choice` fora da limpeza), R18 (vários turnos de operador antes da pergunta), R19, R21 e R26 (fronteiras do fallback); R14, R16, R23 e R24 equivalentes.
- **P3-B:** o README do web remete à seção "Streamlit interface" do README do backend, que em `386025a` ainda fala de chave de acesso; atualizar no fechamento (T68).
- **Não comprovado:** navegador e ticketing reais (QA); P2-A demonstrado com `AppTest` e pela fonte do Streamlit, sem browser.

## Tarefa 67, rodada 5 — correções necessárias (um P2 residual)

- **Papel:** Reviewer novo em contexto frio (subagente `reviewer`, sonnet/xhigh); leu a tarefa até "Validação", o contrato e o parecer da rodada 4; não leu as passagens do Developer.
- **Candidato:** `0bc8d86`, diff `9f6a894..0bc8d86` em `app.py` e `tests/test_v070_interface.py`, no worktree `review-web`; HEAD, status e hashes iguais antes e depois.
- **Números:** script estrito **83 passed**, Ruff limpo, código 0. Mutação: P3-A 6/6 mortos (R05, R13, R18, R19, R21, R26, cada um por teste nomeado); limpeza 9/10 (C8 sobrevive); auditoria AST 6/6 nos casos que cobre.
- **P2-A corrigido para widgets com `key`:** sonda do operador 113/113 (fila, ticket, ação, rascunhos e confirmações de A não chegam a B após logout, revoke falho, expiração ou `401` do ticketing; mesmo operador após expirar mantém o rascunho com a confirmação desmarcada). Modo e URL do backend preservados; navegação sem POST e chave de idempotência sem regressão.
- **P2-B — widgets sem `key` passam ao próximo principal.** A limpeza (`app.py:641-672`) só alcança chaves; widgets com id automático sobrevivem porque a tela de login termina em `st.stop()` (`app.py:970`). Afetados: rádio "Router do atendimento" (`app.py:1010`), "Nome do novo perfil" (`app.py:1383`), "Permitir seleção pela sessão demo" (`app.py:1385`), "Conteúdo fictício" da KB (`app.py:1462`) e, por inspeção, "Rascunho para editar ou publicar" (`app.py:1397`). Sonda: 0/10 — o admin B vê o nome e o texto da KB digitados pelo admin A; a mensagem do lojista B sai com o Router escolhido por A. Backend continua autoritativo.
- **P3-B:** a auditoria por AST só vê `key=` constante em sete tipos de widget; não vê `slider`, `toggle`, `date_input`, `select_slider`, `segmented_control`, `pills`, chave f-string, widget sem `key`, chave em variável nem `**{"key": …}`.
- **P3-C:** `ticket_solve_confirm` (`app.py:828`) continua marcado na expiração do mesmo operador e na troca de ticket; "Resolver" fica habilitado. Anterior a esta rodada.
- **P3-D:** nenhum teste fixa a limpeza de `ticket_attempt` (C8 sobrevive), embora o código a faça.
- **Não comprovado:** navegador e servidor Streamlit reais (só `AppTest`); ticketing real; alguns widgets de comparação, avaliação e KB só pela lista e pela auditoria.
