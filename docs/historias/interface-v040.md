# História: chat e comparação de Router

Versão 1 — 2026-10-27. Um visitante sintético entra como demo, identifica o modo simulado,
escolhe perfil e envia uma mensagem. Continua a conversa e inspeciona fontes/modelos.
Compara LLM/JEV e usa a decisão selecionada no atendimento uma única vez. Atualiza a
tela sem reenvio. Tenta publicar como demo e recebe recusa; admin pode publicar uma
versão compatível. JEV indisponível gera erro explícito, sem fallback silencioso.

## Como terminou

Percorrida pela interface sobre HTTP real, com inferência programada: nos dois sentidos,
mudar a escolha depois da comparação invalida o resultado; uma nova comparação só ocorre
por clique explícito, e o atendimento usa a decisão uma vez, com método, ID e parâmetros
coerentes no inspetor. Modo Gravado, publicação de perfil e parâmetro por agente também
foram percorridos. O Router JEV com chave real foi percorrido pela interface na v1.1.0.
Evidência: [resultado da v0.4.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v040-resultado.md)
e [da v1.1.0](https://github.com/lauroPereira/ai-getnet-case/blob/main/docs/evidence/v110-resultado.md).
