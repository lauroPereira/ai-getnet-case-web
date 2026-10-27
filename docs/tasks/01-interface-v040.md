# Tarefa: interface v0.4.0 via API

## Objetivo

Implementar a especificação da interface com cliente HTTP, fixtures e AppTest.

## Especificação relacionada

[Interface v0.4.0](../specs/interface-v040.md).

## Pré-condições

Base validada; contratos OpenAPI do backend definidos antes das telas.

## Passos

1. Gerar cliente e fixtures de contrato; criar modo simulado sem APIs pagas.
2. Implementar chat/perfis/toggle/comparação e inspeção sem credenciais de provedores.
3. Verificar estados/autorizações/reruns por AppTest e servidor HTTP simulado.
4. Fixar hashes, passar por Reviewer e QA independentes antes de integração.

## Critérios de conclusão

- [ ] Critérios da especificação demonstrados por testes e história.
- [ ] Nenhuma chamada causada só por rerun; integração real distinguida da simulada.
- [ ] Candidato identificado e pareceres independentes registrados.

## Validação

Pendente de implementação.

## Status

Pronta para o Developer após a preparação da base pelo Supervisor.
