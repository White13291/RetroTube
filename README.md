# RetroTube — Safari iOS 8.3 com VPS

O RetroTube agora pode ser executado na VPS Ubuntu Server. Nesse modo, a VPS serve o site, pesquisa vídeos e tenta prepará-los como **MP4 (H.264 baseline + AAC)**. A conversão ocorre **na VPS**, e o iPad assiste pelo player nativo HTML5. Nenhum arquivo precisa ser baixado manualmente no iPad.

> Solução experimental: o YouTube pode bloquear ferramentas de extração e a reprodução real precisa ser testada no dispositivo. Respeite os direitos e termos do serviço de origem.

## Instalação no Ubuntu Server 22.04 ou 24.04

Use VPS com no mínimo 2 GB de RAM (4 GB recomendado), 5 GB livres e porta TCP 8080 liberada no firewall do provedor. Em um terminal SSH **na VPS**:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/White13291/RetroTube.git
cd RetroTube
sudo bash install-vps.sh
```

Ao concluir, abra **http://IP-DA-SUA-VPS:8080/** no Safari do iPad. Substitua `IP-DA-SUA-VPS` pelo IPv4 público exibido no painel da hospedagem.

Para conferir se o Safari antigo consegue reproduzir MP4, toque em **Testar reprodução (sem YouTube)**. Isso abre um vídeo de 8 segundos gerado diretamente pela VPS, sem fazer consultas externas. O link direto de teste é `http://IP-DA-SUA-VPS:PORTA/?demo=1`.

O GitHub Pages antigo continua no endereço de sempre; **para reproduzir vídeos, abra o endereço da VPS**.

Se o repositório já estiver na VPS:

```bash
cd RetroTube
git pull --ff-only
sudo bash install-vps.sh
```

## Se não abrir no iPad

1. Na VPS, execute `curl http://127.0.0.1:8080/api/health`. A resposta deve conter `"ok":true`.
2. Libere **TCP 8080** no painel do provedor e, se UFW estiver ativo, `sudo ufw allow 8080/tcp`.
3. Confira os logs com `sudo docker compose logs --tail=80 retrotube` (ou `sudo docker-compose logs --tail=80 retrotube`).
4. Se a porta 8080 estiver ocupada, use `sudo env RETROTUBE_PORT=8081 bash install-vps.sh` e libere a porta 8081.
5. Se um vídeo falhar, veja os logs: o YouTube pode bloquear consultas provenientes de IPs de datacenters.

## Atualizar

```bash
cd RetroTube
git pull --ff-only
sudo bash install-vps.sh
```

## Segurança e limitações

- `http://IP:8080` evita incompatibilidade de certificados HTTPS com o Safari do iOS 8.3. **HTTP não é criptografado**: use em rede confiável e não digite informações privadas ou credenciais nesse endereço.
- Sua chave da YouTube Data API v3 **não está no repositório**. A busca pela VPS não exige chave. Se usar a versão GitHub Pages, a chave permanece no navegador onde foi salva.
- A API fica pública na porta 8080. Há limites básicos por IP e duração máxima de 30 minutos. O cache limita vídeos prontos a aproximadamente 3 GB, mas arquivos temporários podem consumir espaço adicional.
- A VPS normalmente obtém e converte o vídeo antes de iniciar a reprodução (o iPad não precisa baixar arquivos manualmente). A entrega aceita HTTP Range/206.
- Para HTTPS com domínio próprio, é necessário configurar separadamente um certificado compatível com o iPad.
