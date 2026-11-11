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
4. Fixar o candidato por hashes e submetê-lo a revisão independente e validação funcional antes da integração.

## Critérios de conclusão

- [x] Critérios da especificação demonstrados por testes e história.
- [x] Nenhuma chamada causada só por rerun; integração real distinguida da simulada.
- [x] Candidato identificado, com revisão independente e validação funcional registradas.

## Resultado

Interface v0.4.0 pela API `/v1`: chat contínuo, perfis versionados, Router LLM ou JEV por requisição, comparação só dos Routers, inspeção de resultados e modos Simulado e Gravado, sem chave de provedor.
14 testes da interface e Ruff limpos; transporte HTTP real com inferência programada, sem chamada paga. O Router JEV com chave real foi percorrido pela interface na v1.1.0.
Evidência: [resultado da v0.4.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v040-resultado.md) e [da v1.1.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v110-resultado.md).

## Status

```text
concluída (v0.4.0)
```
