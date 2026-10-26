# RetroTube — Safari iOS 8.3 com VPS

O RetroTube pode ser executado na VPS Ubuntu Server. A VPS serve o site, pesquisa vídeos e tenta prepará-los como **MP4 (H.264 baseline + AAC)**. A conversão ocorre **na VPS**, e o iPad assiste pelo player nativo HTML5. Nenhum arquivo precisa ser baixado manualmente no iPad.

**Fonte padrão: PeerTube.** A busca usa os índices públicos `search.joinpeertube.org` e `peertube.tv`, testados na Oracle, sem chave de API, login ou necessidade de manter um computador ligado. Ao escolher um resultado, a VPS consulta o servidor PeerTube de origem e tenta obter seu arquivo público MP4/WebM, convertendo-o para o player do iPad. Nem todos os vídeos indexados têm arquivos públicos em resoluções compatíveis ou servidores de origem acessíveis.

Para reduzir os resultados que falham ao reproduzir, a busca verifica um pequeno trecho dos arquivos de mídia antes de exibi-los e experimenta `fileDownloadUrl` quando `fileUrl` não funciona. Isso pode tornar a pesquisa mais lenta e ocultar vídeos de servidores instáveis, mas evita oferecer arquivos que já respondem HTTP 404. A verificação não garante que a transferência completa funcionará.

**Dailymotion (experimental):** o extrator de pesquisa do `yt-dlp` foi testado na Oracle e retornou zero vídeos; por isso não é mais a fonte padrão. **YouTube (experimental):** pode solicitar confirmação de robô à Oracle. Nesses casos o backend tenta Invidious e Piped, que também vêm apresentando erros HTTP. PeerTube tem um catálogo independente e não oferece todo o catálogo do YouTube.

> Solução experimental: o YouTube pode bloquear ferramentas de extração e a reprodução real precisa ser testada no dispositivo. Respeite os direitos e termos do serviço de origem.

## Instalação no Ubuntu Server 22.04 ou 24.04

Use VPS com no mínimo 2 GB de RAM (4 GB recomendado), 5 GB livres e porta TCP 8081 liberada no firewall do provedor. Em um terminal SSH **na VPS**:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/White13291/RetroTube.git
cd RetroTube
sudo env RETROTUBE_PORT=8081 bash install-vps.sh
```

Ao concluir, abra **http://IP-DA-SUA-VPS:8081/** no Safari do iPad. Substitua `IP-DA-SUA-VPS` pelo IPv4 público exibido no painel da hospedagem.

Para conferir se o Safari antigo consegue reproduzir MP4, toque em **Testar reprodução (sem YouTube)**. Isso abre um vídeo de 8 segundos gerado diretamente pela VPS, sem fazer consultas externas. O link direto de teste é `http://IP-DA-SUA-VPS:PORTA/?demo=1`.

O GitHub Pages antigo continua no endereço de sempre; **para reproduzir vídeos, abra o endereço da VPS**.

Se o repositório já estiver na VPS:

```bash
cd ~/RetroTube
git pull --ff-only
sudo env RETROTUBE_PORT=8081 bash install-vps.sh
curl -G --max-time 40 --data-urlencode 'q=brasil' --data-urlencode 'source=peertube' http://127.0.0.1:8081/api/search
```

## Se não abrir no iPad

1. Na VPS, execute `curl http://127.0.0.1:8081/api/health`. A resposta deve conter `"ok":true`.
2. Libere **TCP 8081** no painel do provedor e, se UFW estiver ativo, `sudo ufw allow 8081/tcp`.
3. Confira os logs com `sudo docker compose logs --tail=80 retrotube` (ou `sudo docker-compose logs --tail=80 retrotube`).
4. Caso precise alterar a porta, defina `RETROTUBE_PORT` com um número livre e libere a mesma porta na Oracle.
5. Se um vídeo falhar, veja os logs: um servidor PeerTube de origem pode não oferecer arquivos MP4/WebM, estar fora do ar ou bloquear acessos de datacenters.
6. Teste a busca do PeerTube sem chave de API: `curl -G --data-urlencode 'q=noticias brasil' --data-urlencode 'source=peertube' http://127.0.0.1:8081/api/search`. Uma resposta com `"items"` confirma que a busca funciona, mas a reprodução ainda precisa ser testada separadamente.
7. Se a mensagem disser que as fontes alternativas do YouTube estão indisponíveis, teste o Invidious na **própria VPS**:

   ```bash
   curl -i --max-time 20 'https://inv.nadeko.net/api/v1/videos/eWNhOwEIdRk?region=BR'
   ```

   Uma resposta JSON com `formatStreams` indica que o endpoint de metadados funciona; HTTP 403, 429, 500, 502 ou 503 indica bloqueio ou indisponibilidade, e o RetroTube tentará outras instâncias. Este teste **não garante** que o proxy de MP4 também esteja acessível.
8. Para verificar a conversão de um resultado PeerTube, pesquise antes no site e copie seu identificador `peertube_UUID`. Consulte `http://127.0.0.1:8081/api/prepare/peertube_UUID` e depois `/api/status/peertube_UUID`. Para YouTube, use o identificador simples de 11 caracteres; para Dailymotion, `dailymotion_` seguido do ID.

## Atualizar

```bash
cd RetroTube
git pull --ff-only
sudo env RETROTUBE_PORT=8081 bash install-vps.sh
```

## Segurança e limitações

- `http://IP:8081` evita incompatibilidade de certificados HTTPS com o Safari do iOS 8.3. **HTTP não é criptografado**: use em rede confiável e não digite informações privadas ou credenciais nesse endereço.
- Sua chave da YouTube Data API v3 **não está no repositório**. A busca pela VPS não exige chave. Se usar a versão GitHub Pages, a chave permanece no navegador onde foi salva.
- A API fica pública na porta 8081. Há limites básicos por IP e duração máxima de 30 minutos. O cache limita vídeos prontos a aproximadamente 3 GB, mas arquivos temporários podem consumir espaço adicional.
- A VPS normalmente obtém e converte o vídeo antes de iniciar a reprodução (o iPad não precisa baixar arquivos manualmente). A entrega aceita HTTP Range/206.
- O Invidious fornece uma API pública de busca e metadados, mas o site `inv.nadeko.net` funcionar no PC **não prova** que a Oracle conseguirá fazer a mesma requisição. Valide na VPS após atualizar.
- Para HTTPS com domínio próprio, é necessário configurar separadamente um certificado compatível com o iPad.
