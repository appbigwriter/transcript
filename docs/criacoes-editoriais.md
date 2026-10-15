# Biblioteca e criações editoriais

Na aplicação principal, a seção **Transforme o texto em novas ideias** permite selecionar uma transcrição concluída, inclusive as recebidas do aplicativo local, ou **Carregar uma transcrição pronta**. Cole texto ou escolha um arquivo UTF-8 TXT, SRT, VTT ou JSON exportado pelo FBR-Transcript. Informe um título e salve na biblioteca. A importação não chama a API de transcrição.

As quatro ações são: extrair estratégia (observações com evidências da fala e limites), criar roteiro original com ganchos e CTA, traduzir integralmente para português brasileiro e contextualizar no cenário obrigatório informado pelo usuário. Para roteiro e contextualização, informe público, tom e canal; o roteiro também aceita duração aproximada. A criação de roteiro aproveita a estratégia mais recente concluída para a mesma fonte, quando disponível.

O original permanece separado. O histórico permite reabrir criações, copiar e baixar TXT ou JSON. A retenção é a mesma das tarefas (`FBR_RETENTION_HOURS`, padrão 24 horas); exporte o que deseja manter. A biblioteca lista as 200 tarefas mais recentes. Limite de entrada: 60.000 caracteres, sem corte silencioso. Traduções longas são particionadas, com progresso por parte.

## Easypanel

O volume `/data` deve continuar persistente. A chave já configurada (`GROQ_API_KEY` ou `OPENAI_API_KEY`) é reutilizada por padrão. Para escolher separadamente, configure `FBR_TEXT_PROVIDER`, `FBR_TEXT_MODEL` e, opcionalmente, `FBR_TEXT_API_KEY`. Os modelos padrão são `llama-3.3-70b-versatile` para Groq e `gpt-4.1-mini` para OpenAI. O modelo precisa estar habilitado na conta do provedor. Reinicie/deploy após alterar variáveis. Nenhuma chave é enviada ao navegador.

A importação e as criações exigem autenticação da aplicação. A geração entra na fila existente e pode consumir créditos do provedor de texto. Retomar consulta ou repetir a mesma solicitação com o mesmo identificador não gera outra tarefa. Uma falha de rede com o provedor não provoca repetição automática de chamadas. Em tradução com várias partes, partes já processadas podem ter sido cobradas mesmo que uma posterior falhe.

## API autenticada

- `GET /api/library`: transcrições concluídas.
- `POST /api/library/import`: JSON com `request_id` (32 caracteres hexadecimais), `filename`, `content` e `title` opcional.
- `POST /api/creations`: `request_id`, `source_id`, `operation` (`strategy`, `script`, `translate`, `contextualize`), e briefing opcional `context`, `audience`, `tone`, `platform`, `seconds`. `context` é obrigatório para contextualizar.
- `GET /api/creations?source_id=...`: histórico.
- `GET /api/creations/submissions/{request_id}`: recuperar solicitação após conexão interrompida.
- `GET /api/jobs/{id}` e downloads TXT/JSON existentes: consultar e exportar.

Revise fatos e adequação antes de publicar; a análise da fala não comprova alcance ou causalidade de viralização.
