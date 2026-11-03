# Tarefa: login por usuário e tela de ticketing no projeto web

Cópia da tarefa do backend (`ai-getnet-case`, commit `3ccde8d`). Os links relativos de especificação apontam para o repositório do backend.

## Objetivo

A interface Streamlit autentica por usuário e senha (JWT do assistente), mostra as áreas de cada papel e ganha a tela de ticketing do operador: recepção, atendimento, envio e finalização do ticket.

## Especificação relacionada

[Spec v0.7.0, versão 2](../specs/identidade-hitl-v070.md) — "Contrato entre os times" (Identidade e Serviço de ticketing), D3, D9, D10; [spec 0.2](../specs/evolucao-pos-feedback.md) RF-85; [plano](../specs/evolucao-pos-feedback-plano.md) decisão 8. Repositório: `C:\workspace\ai-getnet-case-web`, branch `codex/v0.7.0-identidade-hitl` (Time B).

## Pré-condições

- Contrato da spec versão 2. Clientes falsos do assistente e do ticketing nos testes; integração real fica para o QA.
- O OpenAPI do assistente para `/v1/auth/*`, `/v1/handoffs` e `/v1/messages` vem da branch do backend no commit que o Supervisor indicar; enquanto não houver, use o contrato da spec.
- Não editar o repositório do backend.

## Arquivos prováveis

`app.py`, `src/getnet_web/api.py`, `src/getnet_web/ticketing.py` (novo: cliente do ticketing), `src/getnet_web/generated_api.py`, `openapi-v1.json`, `src/getnet_web/simulated.py`, `tests/`, `README.md` do web.

## Passos

1. Login: usuário e senha → `POST /v1/auth/token` (`grant_type=password`); token só na sessão do Streamlit; sair chama `/v1/auth/revoke`; token expirado leva ao login com mensagem clara. Remover o login por chave.
2. Áreas por papel (lojista e demo: Atendimento e Resultados; operador: Ticketing; admin: Administração e o que já via). O backend continua sendo quem recusa.
3. Tela de ticketing (`TICKETING_URL`, padrão `http://127.0.0.1:8002`): filas por estado (`new`, `open`, `pending`, `solved`); detalhe com dados do caso, rascunho, conversa mascarada, histórico e entregas; Assumir; Aprovar, Responder (texto) e Rejeitar (instrução) com confirmação e uma `idempotency_key` por clique, reaproveitada em nova tentativa da mesma ação; Resolver. `409` e "envio pendente" viram mensagens com recuperação (atualizar).
4. Atendimento mostra o turno `via=operator` com a marca de atendente e a rota `human_reply`.
5. Navegação, troca de aba e rerun não disparam POST.

## Critérios de conclusão

- [ ] CA-47: `AppTest` com clientes falsos cobre recepção, assumir, as três ações, envio pendente e resolução, incluindo `409 claimed_by_other` e `already_resumed` com mensagem de recuperação.
- [ ] Repetir o clique após falha de transporte reenvia a mesma `idempotency_key` (teste).
- [ ] Lojista não vê Ticketing; operador não vê Atendimento; token expirado volta ao login.
- [ ] Nenhum POST em navegação e rerun (teste); senha nunca persistida nem exibida.
- [ ] Script estrito do web com código 0.

## Validação

Developer, rodada 1 (2026-11-03), branch `codex/v0.7.0-identidade-hitl` sobre `3bbc3df`. Detalhes na [passagem](../evidence/v070-developer-web.md).

- Login por usuário e senha:
  - `POST /v1/auth/token` com `grant_type=password` e `client_id=merchant-assistant-web`.
  - Erros RFC 6749 (`invalid_grant`, `invalid_client`, `429` com `Retry-After`) viram mensagens neutras.
  - O token fica só na sessão; sair chama `/v1/auth/revoke`.
  - Token expirado (`exp`), ou `401 token_required`/`invalid_token` do backend ou do ticketing, volta ao login com "Sua sessão expirou".
  - O login por chave foi removido.
- Áreas por papel: lojista e demo veem Atendimento e Resultados; operador vê só Ticketing; admin vê as três de antes.
- Tela de ticketing (`TICKETING_URL`, padrão `http://127.0.0.1:8002`):
  - Filas Recepção, Atendimento, Envio e Finalização.
  - Detalhe com caso, rascunho, conversa mascarada, histórico e entregas.
  - Assumir.
  - Aprovar, Responder e Rejeitar com confirmação e uma `idempotency_key` por clique, reaproveitada ao repetir a mesma ação depois de falha de transporte.
  - Resolver com confirmação.
  - `409 claimed_by_other`, `409 already_resumed` e "envio pendente" viram mensagens com Atualizar.
- O Atendimento marca o turno `via=operator`/`human_reply` como "Atendente humano · rota human_reply".
- Testes novos em `tests/test_v070_interface.py` (17), com assistente e ticketing falsos e `httpx.MockTransport`:
  - CA-47: recepção, assumir, as três ações, envio pendente e resolução; `claimed_by_other` e `already_resumed` com mensagem de recuperação.
  - Mesma chave depois de falha de transporte.
  - Lojista sem Ticketing e operador sem Atendimento; token expirado e `401` levam ao login.
  - Nenhum POST em navegação, troca de fila e rerun.
  - Senha fora do `session_state` e da tela.
- Script estrito: `PYTEST_ADDOPTS="-p no:cacheprovider --basetemp .pytest_cache/v070-web-strict" .venv-v040/Scripts/python.exe tools/scripts/lint_and_tests.py --strict` terminou com código 0 e 59 testes aprovados (42 + 17); ruff limpo.
- Não feito: sincronização do OpenAPI (`openapi-v1.json` e `generated_api.py`), porque falta o JSON revisado do backend.

## Status

```text
Implementada pelo Developer (rodada 1); aguarda revisão independente. A sincronização do OpenAPI fica como passo separado, quando o Supervisor indicar o commit do backend.
```
