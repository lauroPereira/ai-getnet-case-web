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
