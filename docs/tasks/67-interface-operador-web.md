# Tarefa: login por usuário e tela de ticketing no projeto web

Tarefa da v0.7.0 do backend (`ai-getnet-case`) executada neste repositório; a especificação e o plano estão no backend.

## Objetivo

A interface Streamlit autentica por usuário e senha (JWT do assistente), mostra as áreas de cada papel e ganha a tela de ticketing do operador: recepção, atendimento, envio e finalização do ticket.

## Especificação relacionada

[Spec v0.7.0, versão 2](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/specs/identidade-hitl-v070.md) — "Contrato entre os times" (Identidade e Serviço de ticketing), D3, D9, D10; [spec da evolução v0.7.0 a v0.10.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/specs/evolucao-producao-v070-v0100.md) RF-85; [plano](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/specs/evolucao-producao-v070-v0100-plano.md) decisão 8.

## Pré-condições

- Contrato da spec versão 2. Clientes falsos do assistente e do ticketing nos testes.
- O OpenAPI do assistente para `/v1/auth/*`, `/v1/handoffs` e `/v1/messages` vem do backend; enquanto não houver export, vale o contrato da spec.
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

- [x] CA-47: `AppTest` com clientes falsos cobre recepção, assumir, as três ações, envio pendente e resolução, incluindo `409 claimed_by_other` e `already_resumed` com mensagem de recuperação.
- [x] Repetir o clique após falha de transporte reenvia a mesma `idempotency_key` (teste).
- [x] Lojista não vê Ticketing; operador não vê Atendimento; token expirado volta ao login.
- [x] Nenhum POST em navegação e rerun (teste); senha nunca persistida nem exibida.
- [x] Script estrito do web com código 0.

## Resultado

Login por usuário e senha, áreas por papel e tela de Ticketing com Assumir, Aprovar, Responder, Rejeitar e Resolver, uma `idempotency_key` por clique e nenhum POST em navegação ou rerun; o estado de um usuário não passa ao seguinte no mesmo navegador.
Script estrito do web com 92 testes aprovados; CA-47 aprovado com o serviço de ticketing real. Desde a v0.13.0 o login é por redirecionamento (authorization code com PKCE).
Evidência: [resultado da v0.7.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v070-resultado.md) e [revisão independente](../evidence/v070-reviewer-web.md).

## Status

```text
concluída (v0.7.0)
```
