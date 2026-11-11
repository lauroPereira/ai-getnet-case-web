# Especificação: interface inicial v0.4.0

Versão 1 — 2026-10-27 — entregue na v0.4.0.

## Objetivo

Permitir que um lojista sintético, ou quem demonstra o produto, converse e compare
Routers pela API /v1, e que um administrador publique perfis compatíveis, sem expor
segredos.

## Escopo

Streamlit e cliente HTTP gerado dos contratos do backend; chat contínuo; seleção de
perfil/versionamento; toggle LLM/JEV; comparação somente dos Routers; inspeção de
resultados; modo simulado; estados de erro/vazio/loading. Reruns não reenviam ações.

## Critérios

- Interface acessa somente API, sem arquivos/bancos do backend ou chaves de provedores.
- Papéis e dono são verificados pelo backend; demo não publica perfis.
- Métodos real, simulado e gravado são identificados; versão efetiva aparece.
- Comparação não executa atendimento; envio usa a decisão selecionada uma vez.
- Testes AppTest e HTTP sem custo cobrem caminho normal, erro e repetição da tela.
- Integração real JEV não é dada como concluída apenas por testes programados.

## Custo

A v0.4.0 não faz chamada paga: a validação usa transporte HTTP real com modelos e
embeddings programados. A validação com modelos reais começa na v0.5.0, com estimativa
e teto registrados antes de cada campanha.
