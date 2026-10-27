# Especificação: interface inicial v0.4.0

## Objetivo

Permitir que um avaliador/lojista sintético converse e compare Routers pela API /v1,
e que um administrador publique perfis compatíveis, sem expor segredos.

## Escopo aprovado

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

## Limites

Sem chamadas pagas, publicação, alteração de roteador, commit/push ou criação de remoto
nesta fase. A autorização humana da v0.4.0 abrange preparação desta base e implementação
local; qualquer gasto posterior precisa de estimativa e autorização próprias.
