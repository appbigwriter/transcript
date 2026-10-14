# Navegador remoto e sessões de extração

## O que foi implementado

A área **Contas conectadas** gerencia uma sessão por plataforma: YouTube, Instagram, Facebook, TikTok e X. É possível importar cookies Netscape, substituir, remover, testar um link e sincronizar um perfil Firefox montado na VPS.

Isso é uma sessão do navegador usada pelo extrator, **não OAuth oficial**. Não foram cadastrados aplicativos no Google, Meta ou TikTok nem implementados consentimentos OAuth. Essas integrações dependem de cadastro, credenciais e permissões específicas e não dão acesso geral ao áudio de qualquer publicação.

Cada importação retém somente cookies dos domínios da plataforma escolhida. Cookies expirados são descartados. As sessões são criptografadas com AES-GCM em `/data/sessions`, e as APIs retornam somente metadados, nunca os valores dos cookies. A chave é derivada de `FBR_SESSION_SECRET` ou, se não informado, da senha de acesso. Defina uma chave aleatória estável com pelo menos 32 caracteres para separar a proteção das sessões da senha de login. Alterar a chave ou a senha usada na derivação exige reimportar as sessões.

O modo público (`FBR_ALLOW_PUBLIC=1`) não oferece gerenciamento de sessões nem sincronização. O acesso desta versão continua sendo por uma única credencial compartilhada: todos que conhecem a senha do aplicativo podem administrar as sessões. Para vários usuários independentes, é necessária separação de perfis e autorização por usuário.

## Como usar agora

1. Faça login na plataforma em um navegador de sua confiança e exporte os cookies em formato Netscape. Não envie arquivos contendo sessões a terceiros ou ao GitHub.
2. Abra **Contas conectadas**, escolha a plataforma e use **Importar sessão**.
3. Cole um link autorizado daquela plataforma e clique em **Testar link com a sessão**. Esse teste consulta metadados, sem transcrever nem gastar créditos da API. Ele não comprova que a sessão está autenticada nem garante download: um vídeo público pode ser acessível sem login.
4. Transcreva o link normalmente. O extrator usa somente a sessão da plataforma selecionada, em uma cópia temporária removida ao fim da tarefa.
5. Se o acesso falhar, renove a sessão ou use **Enviar arquivo** para transcrever um vídeo/áudio que você já possui.

**Remover** exclui a sessão do aplicativo e impede o uso do arquivo legado para essa plataforma. Não revoga a sessão no Google/Meta/TikTok/X: para isso, encerre-a nas configurações da própria conta. Uma tarefa já em andamento pode continuar usando sua cópia temporária.

## Firefox remoto na VPS: configuração proposta

O código já consegue ler um perfil Firefox, mas **o serviço de navegador não foi instalado nem validado na sua VPS**. Ele precisa de um container separado do FBR-Transcript. Uma opção é a imagem [LinuxServer Firefox](https://docs.linuxserver.io/images/docker-firefox/), cujo perfil fica no volume `/config`. Confirme a documentação e fixe uma versão/digest antes de uso em produção.

Estrutura:

```text
Usuário → gateway HTTPS com controle de acesso → Firefox remoto
                                                   │
                                        volume de perfil persistente
                                                   │ leitura apenas
                                  FBR-Transcript → sessão criptografada
                                                   │
                                               extrator
```

No Easypanel:

1. Crie um serviço separado para Firefox, com volume persistente em `/config`. O navegador precisa de memória e CPU adicionais; dimensione após observar o consumo.
2. Proteja a interface com VPN ou gateway de identidade com MFA. A documentação LinuxServer indica que somente `CUSTOM_USER`/`PASSWORD` é adequado a uma rede confiável; não exponha o desktop diretamente à internet contando apenas com isso.
3. O Firefox oferece interface HTTPS na porta 3001 e HTTP na 3000 para uso atrás de proxy. Configure o proxy do Easypanel conforme a imagem e mantenha WebSockets habilitados. Não publique portas de depuração do navegador e não monte o socket Docker.
4. Monte o mesmo volume de perfil no container Transcript, **somente para leitura**, por exemplo em `/browser-profile`. Isso exige um volume compartilhado/bind mount na VPS; não é uma cópia via rede. O usuário UID 10001 do Transcript precisa de leitura dos arquivos do perfil, sem torná-los públicos nem liberar escrita. Não use permissões globais como `chmod 777`.
5. No Environment do Transcript, configure:

```dotenv
FBR_BROWSER_PROFILE=/browser-profile
FBR_BROWSER_URL=https://SEU_DOMINIO_PROTEGIDO_DO_NAVEGADOR
FBR_SESSION_SECRET=SUA_CHAVE_ALEATORIA_ESTAVEL
```

6. Faça o deploy. Abra o navegador remoto pelo link da área Contas conectadas. Faça login diretamente nos sites oficiais, inclusive MFA quando solicitado.
7. Volte ao Transcript e clique em **Sincronizar do navegador** na plataforma desejada. O sistema copia apenas os cookies daquela plataforma para o armazenamento criptografado. Valide um link e depois uma transcrição.

A sincronização é **manual**. Um novo login no Firefox não atualiza automaticamente uma sessão já importada. Nenhuma senha da rede social é recebida pelo formulário do Transcript. O perfil Firefox contém sessões sensíveis: proteja volume, backups e gateway.

## Por que não garante ausência de bloqueios

Login na mesma VPS mantém a origem de rede consistente, mas IP de datacenter, MFA, expiração, restrições regionais, desafios e mudanças do extrator ainda podem impedir acesso. O downloader faz suas próprias requisições HTTP e não se torna idêntico ao Firefox só por usar os mesmos cookies.

Não são implementados recursos de disfarce de automação, rotação de proxy, solução automática de CAPTCHA ou recuperação de stories expirados. Quando a plataforma pedir nova autorização, ela deve ser feita pelo usuário. A documentação do [yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ) e a [página de extratores](https://github.com/yt-dlp/yt-dlp/wiki/Extractors) detalham as limitações de sessões e clientes.

## Arquivo legado e Git

`FBR_COOKIES_FILE` continua como alternativa para arquivos externos ao código, filtrados por plataforma. Uma sessão gerenciada tem prioridade. Ao remover a sessão gerenciada, um marcador persistente evita que o arquivo legado a reative automaticamente.

O arquivo `app/cookies.txt` estava versionado antes desta alteração. Foi retirado da versão atual e excluído do contexto de build, preservando a cópia local. **Isso não apaga commits antigos.** Se o arquivo tinha cookies reais, encerre/revogue essas sessões nas plataformas e depois importe novas sessões. Não reescrevemos o histórico do repositório.

## Upload alternativo

Em **Enviar arquivo**, o usuário envia vídeo/áudio diretamente ao servidor. O limite é o mesmo de `FBR_MAX_DOWNLOAD_MB`; a duração é validada antes de chamar a API. O arquivo usa nome interno aleatório, fica na fila e é removido após o processamento. O início da tarefa não garante que o formato seja válido: arquivos ilegíveis recebem erro sem transcrição. Arquivos órfãos de tarefas interrompidas são removidos na próxima inicialização.

## Verificação

Há testes para criptografia e adulteração, persistência, isolamento por domínio, expiração, permissões da interface, origem de requisições, uploads e leitura real pelo yt-dlp de um perfil SQLite Firefox com cookies fictícios. Esses testes não equivalem a um login real em cada plataforma nem à implantação do serviço Firefox no Easypanel.
