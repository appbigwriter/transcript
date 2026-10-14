# FBR-Transcript

## Contas conectadas e envio de arquivos

A interface inclui sessões separadas por plataforma: importar/substituir cookies Netscape, remover e testar acesso a um link. Os cookies ficam criptografados em `/data/sessions` e não são retornados ao navegador. O gerenciamento exige senha e fica indisponível no modo público. Para login em um Firefox remoto na VPS e sincronização da sessão, veja o [guia do navegador remoto](docs/navegador-remoto.md). Esse serviço adicional ainda precisa ser instalado no Easypanel; a sincronização no aplicativo já está implementada. Isso não é OAuth oficial e não garante eliminar bloqueios das plataformas.

Também é possível selecionar **Enviar arquivo** para transcrever áudio/vídeo sem depender da extração por link. O arquivo fica na fila, respeita os limites de tamanho/duração e é removido após a tarefa.

O arquivo `app/cookies.txt` foi retirado da versão atual do Git e do contexto de build. Se os commits anteriores continham uma sessão real, revogue-a na plataforma e importe uma nova; a exclusão atual não apaga o histórico antigo.

Aplicativo para transcrever a fala de vídeos por link, preparado para instalação na VPS com Easypanel. Interface em português, fila de processamento, resultado copiável e downloads TXT/SRT/VTT. O áudio é extraído com yt-dlp/FFmpeg e enviado à API configurada no servidor.

## Instalar no Easypanel

1. Crie um serviço **App**, com nome `fbr-transcript`, no seu projeto.
2. Em **Source → Upload**, envie `fbr-transcript-easypanel.zip`.
3. Em **Build**, escolha **Dockerfile**, com caminho `Dockerfile` e raiz `/` caso o painel peça Build Path.
4. Em **Environment**, preencha as variáveis abaixo. A chave vai apenas no Easypanel; não a coloque no código, no navegador ou em mensagens.
5. Em **Storage**, crie um volume persistente montado em `/data`. O usuário do container é UID **10001**: o volume precisa permitir escrita por esse usuário. Se necessário, ajuste proprietário/permissões do volume na VPS antes do deploy.
6. Em **Domains**, configure seu domínio com destino **HTTP**, porta interna **8000**. Aponte o DNS para a VPS e habilite HTTPS no Easypanel.
7. Clique em **Deploy**. Abra o domínio e entre com usuário `fbr` e a senha configurada. O navegador apresenta a janela de autenticação.

Use **uma réplica** e **um worker**. O Dockerfile já define isso. A fila SQLite não foi projetada para vários processos/containers concorrentes. Não é preciso configurar Redis ou um banco externo.

### Opção inicial: Groq

```dotenv
FBR_PROVIDER=groq
GROQ_API_KEY=COLE_A_CHAVE_NO_EASYPANEL
FBR_USERNAME=fbr
FBR_PASSWORD=DEFINA_UMA_SENHA_FORTE_COM_12_OU_MAIS_CARACTERES
FBR_DATA_DIR=/data
```

Crie a chave em [console.groq.com/keys](https://console.groq.com/keys). O modelo padrão é `whisper-large-v3-turbo`, com texto e tempos para legendas. Configure créditos/limites conforme sua conta. Não existe chave incluída no pacote.

### Alternativa: OpenAI

```dotenv
FBR_PROVIDER=openai
OPENAI_API_KEY=COLE_A_CHAVE_NO_EASYPANEL
FBR_USERNAME=fbr
FBR_PASSWORD=DEFINA_UMA_SENHA_FORTE_COM_12_OU_MAIS_CARACTERES
FBR_DATA_DIR=/data
```

Crie a chave em [platform.openai.com/api-keys](https://platform.openai.com/api-keys). O modelo padrão é `gpt-4o-mini-transcribe`, para **texto sem tempos**. A assinatura do ChatGPT não configura créditos da API neste serviço. Para gerar legendas pela OpenAI, adicione `FBR_MODEL=whisper-1`. Também é possível usar `FBR_MODEL=gpt-4o-transcribe` para texto. O aplicativo não inventa tempos quando o modelo não os fornece.

Somente a chave do provedor selecionado é usada. Não há mudança automática de provedor nem novas tentativas automáticas de transcrição, para evitar cobranças duplicadas após falhas de conexão.

## Plataformas e limites reais

São aceitos links de publicações com vídeo do **YouTube, Instagram, TikTok, Facebook e X**. Isso não garante que qualquer publicação dessas plataformas possa ser extraída:

- YouTube: vídeos, shorts e lives já publicadas. Algumas publicações/redes de VPS exigem cookies, token de origem ou configuração adicional do extrator.
- Instagram: posts, reels e links de stories individuais. Login pode ser necessário mesmo para conteúdo público.
- TikTok: vídeos e links curtos reconhecidos. Bloqueios geográficos e antiautomação podem impedir acesso.
- Facebook: vídeos, reels, links compartilhados e determinados links de stories. Variações de URL, sessões e bloqueios podem impedir a extração.
- X: publicações com vídeo. Conteúdo restrito pode exigir uma sessão autorizada.
- Stories expirados não são recuperados. Não há extração de publicações sem vídeo, lives em andamento, playlists inteiras ou múltiplos vídeos por postagem. Em uma postagem com vários vídeos, tenta-se apenas o primeiro.
- O programa usa os extratores específicos dessas plataformas. Links arbitrários, endereços locais, arquivos e páginas de perfil são recusados.

Quando houver exigência de sessão, o administrador pode montar um arquivo de cookies Netscape autorizado em `/secrets/cookies.txt`, somente para leitura, e configurar `FBR_COOKIES_FILE=/secrets/cookies.txt`. O arquivo é copiado para a pasta temporária da tarefa porque o downloader atualiza cookies; a cópia é removida ao terminar. Trate o arquivo como credencial de conta, use somente uma sessão autorizada e renove-o quando expirar. A autenticação não garante contornar bloqueios técnicos ou permitir acesso que a conta não possui.

O downloader precisa acompanhar as mudanças das plataformas. A versão inicial está fixada em `requirements.txt`; atualize-a e reconstrua o container se um extrator deixar de funcionar. Não há promessa de compatibilidade permanente ou recuperação de conteúdo indisponível.

## Processamento e privacidade

- Uma tarefa de cada vez; até 10 na fila.
- Limite inicial de 60 minutos de áudio e 150 MB de download. Valores configuráveis no `.env.example`.
- Limite de 30 novas tarefas por hora na instância, além dos limites do provedor. Reiniciar o serviço reinicia esse contador; configure orçamento na conta da API para limitar gastos.
- Áudio convertido para MP3 mono, 16 kHz, 48 kbps e partes de aproximadamente 10 minutos, abaixo de 25 MB cada. Divisões podem cortar uma palavra/frase e causar diferenças próximas às junções; revise transcrições extensas.
- O áudio é enviado ao provedor escolhido; aplicam-se também as políticas de retenção dele. Não ocorre transcrição local.
- Áudio e cookies temporários são removidos após a tarefa; sobras de uma interrupção são removidas na próxima inicialização. Resultados e URLs ficam na VPS por 24 horas, por padrão, e depois são excluídos. O tempo é contado a partir da última atualização da tarefa.
- Transcrições são guardadas em SQLite no volume `/data`. O acesso é compartilhado pelo usuário/senha do aplicativo; esta versão não tem contas individuais nem separação por usuário.
- Em caso de reinício, tarefas em execução ficam como interrompidas e não são refeitas automaticamente. A fila ainda não iniciada pode continuar.
- Limite de 30 minutos de execução por tarefa. Falhas em partes posteriores não devolvem cobranças já realizadas por partes anteriores.
- O processamento começa mesmo que o navegador seja fechado. Na mesma sessão do navegador, o último resultado é recuperado ao recarregar a página.

## Custos de referência

Consultados em **13/10/2026**, em dólares americanos, sem impostos, câmbio, hospedagem ou serviços de extração adicionais:

| Provedor/modelo | Referência publicada | 10 horas de áudio |
| --- | --- | --- |
| Groq Whisper Large V3 Turbo | US$ 0,04/hora | US$ 0,40 |
| Groq Whisper Large V3 | US$ 0,111/hora | US$ 1,11 |
| OpenAI GPT-4o Mini Transcribe | estimativa de US$ 0,003/minuto | US$ 1,80 |
| OpenAI Whisper-1 | US$ 0,006/minuto | US$ 3,60 |

Groq aplica mínimo de 10 segundos faturados por requisição. A estimativa de OpenAI Mini é baseada na referência por minuto; a conta efetiva depende da medição publicada para o modelo. Preços, modelos, cotas e acesso podem mudar. A qualidade precisa ser avaliada com o idioma, sotaque, ruído e vocabulário dos seus próprios vídeos; menor preço não implica qualidade idêntica.

Fontes: [Groq modelos](https://console.groq.com/docs/models), [Groq áudio](https://console.groq.com/docs/speech-to-text), [OpenAI preços](https://developers.openai.com/api/docs/pricing), [OpenAI transcrição](https://developers.openai.com/api/docs/guides/speech-to-text), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [Easypanel App](https://easypanel.io/docs/services/app).

## Executar localmente

Python 3.12+, Node.js 24, FFmpeg e FFprobe devem estar instalados. O container já inclui tudo isso.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export FBR_PROVIDER=groq
export GROQ_API_KEY='sua-chave'
export FBR_PASSWORD='sua-senha-forte'
export FBR_DATA_DIR='./data'
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

O programa prioriza as variáveis do processo e usa `.env.local` e depois `.env` da raiz do projeto como valores de fallback. Esses arquivos não são enviados ao GitHub nem incluídos na imagem Docker. No Easypanel use o editor Environment; com Docker use `--env-file`. Para uma instância deliberadamente pública é possível definir `FBR_ALLOW_PUBLIC=1`, mas isso dispensa a senha e permite que terceiros gerem despesas na sua chave. O padrão exige senha.

## Verificação desta entrega

- 11 testes automatizados de links permitidos/recusados, autenticação, exportação TXT/SRT/VTT, fila, recuperação após reinício e chamadas de API com transporte simulado.
- Separação real, com FFmpeg, de um áudio sintético de 601 segundos em dois arquivos e verificação do limite de duração.
- Consulta de metadados e download real de um vídeo público de 19 segundos do YouTube, no ambiente de desenvolvimento.
- Dependências resolvidas para Linux x86_64 e Python 3.12.
- Interface servida com HTTP 200 e módulos JavaScript verificados.

**Ainda não verificado:** transcrição real em Groq/OpenAI (não foi fornecida uma chave), extração real em Instagram/TikTok/Facebook/X, comportamento a partir do IP da sua VPS e execução do container (o serviço Docker local não estava ativo). Os testes simulados não comprovam qualidade de reconhecimento de fala. Esses pontos devem ser verificados após a instalação, com links autorizados de cada plataforma e chave configurada.

