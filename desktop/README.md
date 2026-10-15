# FBR-Transcript Local — Windows

Auxiliar local que recebe links individuais ou em lote, procura legendas existentes e, quando necessário, obtém áudio usando a rede e a sessão do navegador deste computador. O servidor recebe somente texto ou áudio. As chaves Groq/OpenAI continuam no servidor.

## Abrir

Use a versão portátil `FBR-Transcript-Local.exe`, mantendo a pasta `_internal` ao lado do executável. Ela inclui Python, FFmpeg, FFprobe e Node; não requer instalação desses componentes no computador de destino.

Para executar pelo código, instale Python 3.10+ com Tcl/Tk e execute em PowerShell:

```powershell
./desktop/Install-Local.ps1
./desktop/Start-Local.ps1
```

A execução pelo código precisa de FFmpeg/FFprobe e Node no PATH para obter e preparar áudio. O empacotamento portátil é feito por `desktop/Build-Windows.ps1`.

## Configurar

1. Em **Configurações**, informe a URL HTTPS do Transcript, usuário e senha do aplicativo principal. Não informe senhas de YouTube, Instagram, Facebook ou TikTok.
2. Use **Testar conexão**. O servidor deve conter as rotas `/api/local/*` desta versão. Um servidor antigo será identificado como incompatível.
3. Selecione o navegador em que você entrou nas plataformas **neste Windows**. As sessões do Firefox remoto da VPS não são as sessões do navegador local. `none` tenta acesso sem sessão.
4. O Firefox costuma permitir a leitura de um perfil local. Feche o navegador se o perfil estiver bloqueado. Chrome/Edge podem impedir a leitura por criptografia vinculada ao aplicativo; não há garantia de compatibilidade com todo perfil. Como alternativa, escolha um arquivo Netscape `cookies.txt` já disponível localmente. O arquivo original é preservado.
5. Para guardar o login do Transcript, marque **Guardar senha protegida pelo Windows**. O arquivo de configuração armazena a senha via DPAPI, vinculada ao usuário Windows. Sem essa opção, informe a senha a cada abertura.
6. Confirme se quer a alternativa por áudio e o envio ao servidor. Com envio desligado, as legendas e/ou o áudio são salvos somente no computador, sem cobrança de transcrição. O modo escolhido se aplica às tarefas ainda não concluídas.

Cole um link por linha, adicione à fila e clique **Iniciar / retomar**. Links duplicados na fila são ignorados. Use **Ver texto** ou **Abrir arquivos locais** para acessar TXT, SRT e JSON ao concluir. Se só houver áudio em modo local, o aplicativo mantém `audio.mp3` para uso posterior.

## Retomada e limites

- A fila fica em `%LOCALAPPDATA%/FBR-Transcript-Local/queue.sqlite3`; os resultados em `outputs/<identificador>/`. São dados locais do usuário, não arquivos temporários nem cookies.
- A coleta ocorre em processo separado e pode ser pausada. Uma transcrição já aceita pelo servidor continua nele; retomar consulta o mesmo identificador, sem criar automaticamente outra transcrição.
- Cada envio tem identificador estável. O servidor reserva esse identificador em transação e recusa conteúdo diferente com a mesma chave. Repetir um upload após queda de rede não cria dois trabalhos de transcrição.
- Tarefas que falham ficam paradas para revisão. **Retomar selecionada** reutiliza o identificador; uma transcrição que falhou depois de aceita não será cobrada novamente automaticamente.
- Não há reenvio automático de resultados expirados. A retenção do servidor continua valendo; os resultados baixados pelo auxiliar permanecem localmente.
- Para enviar depois uma tarefa concluída no modo somente local, use os arquivos exportados ou uma nova instância da fila; a V1 não converte automaticamente tarefas já concluídas em novos envios.
- O processamento respeita duração/tamanho do servidor. Não há garantia de acesso a publicações privadas, removidas, expiradas ou bloqueadas pela plataforma. Lives em andamento não são aceitas.
- A transcrição de áudio usa o provedor configurado no servidor. Uma falha após envio ao provedor pode ter gerado cobrança; o auxiliar não repete essa chamada por conta própria.

## Integração do servidor

- `GET /api/local/capabilities`: versão da integração, limites e disponibilidade do provedor.
- `POST /api/local/transcripts`: importa legendas sem usar o provedor de transcrição.
- `POST /api/local/audio`: recebe áudio e metadados para a fila existente.
- `GET /api/local/submissions/<id>`: recupera um envio após interrupção.
- `GET /api/jobs/<id>`: acompanha e recebe o resultado.
- `GET /api/jobs/<id>/download/json`: exporta o resultado estruturado.

Todos exigem a autenticação do servidor. Cookies não fazem parte desses contratos. Os resultados identificam a URL de origem e `source_type` (`captions_manual`, `captions_automatic` ou `speech_to_text`). Transcrições prontas podem alimentar os sistemas de análise e geração de novos roteiros; a geração de roteiros não faz parte deste auxiliar.
