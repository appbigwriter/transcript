# Configuração aplicada na VPS

Em 14/10/2026, as sessões autorizadas de YouTube, Facebook e TikTok foram importadas para o armazenamento criptografado do Transcript. Instagram e X continuam sem sessão configurada.

## Serviços e persistência

- Projeto Easypanel: `sistemas`.
- Firefox: serviço Compose `transcript-browser`, perfil persistido por `/srv/fbr-transcript/firefox` → `/config`.
- Interface: `https://sistemas-transcript-browser.pojxaz.easypanel.host`, encaminhada ao serviço `firefox`, HTTP interno, porta `3000`. Exige usuário `fbr-browser` e a senha cadastrada no Environment. Não publicar outras portas do container.
- Transcript: serviço App `transcript`, com bind `/srv/fbr-transcript/firefox/transcript-data` → `/data`.
- O diretório persistente de dados pertence ao UID/GID `10001`, com diretórios `0700` e arquivos `0600`. Contém o banco de tarefas e as sessões criptografadas; preserve esse caminho ao recriar qualquer serviço.

Antes de aplicar o bind do Transcript, foram preservados o banco de tarefas por backup SQLite consistente e os três arquivos de sessão criptografados. A transferência interna usou um envelope RSA-OAEP/SHA-256 e AES-GCM, com chave e servidor temporários de uso único; os cookies não foram expostos em logs ou armazenados neste repositório.

O App não recebeu uma montagem do perfil Firefox. O painel não disponibilizou a opção de montagem somente de leitura. A importação foi pontual: entrar novamente no navegador não atualiza automaticamente as sessões já importadas. O botão de sincronização automática ainda exige a configuração de um perfil legível pelo App conforme o guia geral.

O diretório de dados do Transcript está dentro do bind do Firefox; administradores do container Firefox têm acesso a esses arquivos. Para ampliar o isolamento no futuro, migrar os dados para um diretório independente da VPS, preservando o banco e as sessões antes de alterar os binds. Não apagar o diretório do Firefox para recriar o navegador sem preservar `transcript-data`.

## Validação realizada

Com a sessão importada, o diagnóstico de extração na VPS concluiu o download e a validação do vídeo YouTube `ZTSI3DDP_4A`: duração de 961,921 segundos e arquivo de 13.200.316 bytes. Nenhuma API de transcrição foi chamada nesse teste.

Após recriar o container do Transcript, `/data` foi confirmado como ponto de montagem, as três sessões permaneceram legíveis e a tarefa de diagnóstico concluída continuou disponível. O servidor temporário de transferência foi encerrado e seus arquivos foram removidos.

Facebook e TikTok tiveram sessões importadas, mas a extração ainda precisa ser validada com links autorizados dessas plataformas. Sessão salva não garante que qualquer publicação estará acessível.

Sessões podem expirar ou ser revogadas pelas plataformas. Preserve a senha do Transcript e `FBR_SESSION_SECRET`, quando configurada: mudanças podem impedir a leitura das sessões criptografadas existentes. Credenciais e cookies não devem entrar em commits.
