# Firefox remoto no Easypanel

Este diretório é um serviço **Compose separado**. Não substitui o App `transcript` nem seu volume `/data`. O compose fixa a imagem Firefox pelo digest consultado em 14/10/2026. A sintaxe foi validada sem iniciar o container; ainda é necessário testar no seu servidor.

## 1. Criar o serviço

No projeto `sistemas`, crie um serviço Compose chamado `transcript-browser`.

Configure a origem Git:

- Repository URL: `https://github.com/appbigwriter/transcript.git`
- Branch: `main`
- Build Path: `/deploy/firefox`
- Docker Compose File: `compose.yaml`

Em repositório privado, adicione a deploy key indicada por esse serviço no GitHub. Não reutilize chaves de escrita quando bastar leitura.

Em Environment, defina os nomes abaixo com uma senha forte exclusiva. Habilite a criação de `.env` se a versão do Easypanel exigir isso para interpolar o Compose:

```dotenv
FBR_BROWSER_USER=fbr-browser
FBR_BROWSER_PASSWORD=SENHA_EXCLUSIVA_COM_PELO_MENOS_16_CARACTERES
```

O compose aborta se a senha estiver vazia. Não compartilhe essa senha no chat ou em commits.

## 2. Persistir o perfil

O bind mount usa `/srv/fbr-transcript/firefox` no host e `/config` no Firefox. O container usa PUID/PGID **10001**, o mesmo UID do Transcript. A imagem LinuxServer normalmente inicializa e ajusta seu diretório `/config`; confirme isso nos logs.

Se o diretório precisar ser preparado manualmente, execute no terminal administrativo **da VPS**, após conferir esse caminho exato:

```bash
sudo install -d -m 700 -o 10001 -g 10001 /srv/fbr-transcript/firefox
```

Não execute esse comando no seu computador Windows. Não use chmod 777 e não substitua outros volumes existentes. O limite inicial é 2 GB de RAM para o serviço, com 1 GB de memória compartilhada; confirme capacidade disponível na VPS e ajuste após medir.

## 3. Proteger o acesso antes de publicar

O Compose não publica portas no host. O navegador remoto permite controlar sessões e tem um terminal com privilégios dentro do container: sua interface precisa de VPN ou gateway de identidade com MFA. Só a senha básica da imagem não é a proteção recomendada para exposição pública.

Depois de proteger o acesso, configure Domains no Easypanel para encaminhar ao serviço interno **firefox**, protocolo **HTTP**, porta **3000**, com HTTPS externo e suporte a WebSocket. Escolha um domínio que você controla. Se ainda não há gateway/VPN disponível, mantenha o serviço sem domínio público até configurar essa proteção.

Não abra portas de depuração e não adicione `privileged`, `network_mode: host`, montagens do Docker socket ou acesso de escrita ao sistema da VPS.

## 4. Conectar ao App existente

No serviço App `transcript`, adicione um bind mount **somente de leitura**:

| Campo | Valor |
|---|---|
| Host path | `/srv/fbr-transcript/firefox` |
| Container path | `/browser-profile` |
| Read only | habilitado |

Preserve o volume `/data` e todas as variáveis existentes. No Environment do **Transcript**, adicione:

```dotenv
FBR_BROWSER_PROFILE=/browser-profile
FBR_BROWSER_URL=https://DOMINIO_PROTEGIDO_DO_FIREFOX
```

Mantenha `FBR_ALLOW_PUBLIC=0` e a senha de acesso do aplicativo. Preserve a `FBR_SESSION_SECRET` existente; não a altere sem necessidade. Se ainda não estiver definida, o aplicativo deriva a proteção da senha atual, portanto não é obrigatório defini-la para este teste.

Faça Deploy dos dois serviços. Se não encontrar no painel a opção de bind mount somente de leitura, não troque por um volume de escrita: a montagem deve ser configurada corretamente antes da sincronização.

## 5. Login e validação

1. Entre no gateway protegido e abra o Firefox remoto.
2. Acesse o YouTube diretamente, faça seu login e conclua os desafios/MFA manualmente.
3. Abra o vídeo `https://www.youtube.com/watch?v=ZTSI3DDP_4A` no Firefox e confirme que reproduz nessa VPS.
4. No Transcript, abra **Contas conectadas → YouTube → Sincronizar do navegador**.
5. Teste a sessão. A sincronização importa apenas os cookies do YouTube/Google, filtrados e criptografados no Transcript.
6. Execute primeiro o diagnóstico de extração, sem transcrição. Somente se ele baixar e validar o áudio, teste a transcrição com a chave do provedor configurada.

Os endpoints `/api/diagnostics/runtime` e `/api/diagnostics/extraction` exigem a autenticação do Transcript. O segundo cria uma tarefa que só baixa/valida mídia; não chama Groq/OpenAI. O diagnóstico de runtime agora inclui o estado da montagem do Firefox sem expor caminhos internos ou cookies.

Não é uma garantia de desbloqueio: se o vídeo reproduzir no Firefox mas o extrator continuar recebendo `youtube_bot_challenge`, registre o resultado. O navegador e o extrator não são clientes idênticos. Não repita o teste em sequência, não tente resolver CAPTCHA automaticamente e não prometa acesso a publicações sem permissão.

Fontes: [LinuxServer Firefox](https://docs.linuxserver.io/images/docker-firefox/), [Easypanel Compose](https://easypanel.io/docs/services/compose).
