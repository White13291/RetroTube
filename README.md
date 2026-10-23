# RetroTube — Safari iOS 8.3

Interface simples de pesquisa e reprodução de vídeos, separada em duas partes:

- frontend/: HTML, CSS e JavaScript ES5 para publicar gratuitamente no GitHub Pages.
- backend/: Flask + yt-dlp + FFmpeg, executado em uma VPS Ubuntu. Obtém e converte vídeos em MP4 H.264 baseline + AAC.

O GitHub Pages não executa o backend. O arquivo frontend/config.js precisa apontar para um servidor próprio acessível em HTTPS.

## Preparar a VPS

É necessário Docker/Compose, um domínio (ou subdomínio) apontado para a VPS e HTTPS configurado em um proxy reverso.

1. Coloque este repositório na VPS.
2. Execute: docker compose up -d --build
3. Em seu proxy HTTPS (Caddy, Nginx etc.), encaminhe o domínio da API para http://127.0.0.1:8765.
4. Verifique https://SEU-DOMINIO-DA-API/api/health, que deve retornar ok:true.
5. Ajuste frontend/config.js com o endereço https://SEU-DOMINIO-DA-API.

Exemplo de configuração do Caddy:

    videos.seudominio.com {
        reverse_proxy 127.0.0.1:8765
    }

Não copie esse exemplo sem substituir o domínio e conferir os serviços existentes na VPS.

## Publicar no GitHub Pages

O repositório já contém `.github/workflows/pages.yml`, que publica a pasta
`frontend/` pelo GitHub Actions. Em **Settings → Pages → Build and deployment**,
escolha **GitHub Actions** e confirme que o repositório está público.
O endereço ficará no formato:

https://white13291.github.io/NOME-DO-REPOSITORIO/

O site será aberto sem login, mas a reprodução só funcionará depois que a API da VPS estiver acessível em HTTPS.

## Restrições importantes

- O YouTube pode restringir requisições de servidores de datacenter. O yt-dlp deve ser atualizado, e não há garantia de que todos os vídeos funcionarão.
- O backend limita vídeos a 30 minutos, duas conversões simultâneas e cache de 3 GB por padrão.
- O primeiro acesso a cada vídeo pode levar tempo para baixar e converter; reproduções seguintes usam cache.
- A interface usa apenas JavaScript ES5, mas a compatibilidade real com iOS 8.3 depende de teste no dispositivo e de um certificado HTTPS aceito pelo Safari.
- Respeite os direitos dos criadores, as restrições de acesso ao conteúdo e os termos dos serviços utilizados.
