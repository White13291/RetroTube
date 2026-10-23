# RetroTube — somente GitHub Pages

Esta é a versão sem VPS do RetroTube. A página é feita em HTML, CSS e
JavaScript ES5 e recebe links de vídeos do YouTube, carregando o player oficial
diretamente no navegador. **Nenhum vídeo é baixado ou convertido por nós.**

Site público: https://white13291.github.io/RetroTube/

O GitHub Pages é um servidor estático. Para mostrar resultados **dentro do
RetroTube**, a página aceita uma chave pessoal da **YouTube Data API v3**.
A chave é salva no localStorage do navegador (ou apenas na memória durante
a página atual quando o armazenamento está bloqueado), **nunca no código
ou no GitHub**. As consultas
são feitas diretamente ao Google, e os cartões apontam para o player do
próprio RetroTube. Não é preciso VPS ou backend próprio.

## Habilitar a busca oficial

1. Entre no Google Cloud Console com sua conta Google e crie ou selecione um projeto.
2. Em **APIs e serviços → Biblioteca**, ative **YouTube Data API v3**.
3. Em **APIs e serviços → Credenciais**, crie uma chave de API.
4. Restrinja a chave à **YouTube Data API v3** e, em restrições de aplicativo
   (sites HTTP), ao domínio e caminho do RetroTube
   (\`https://white13291.github.io/RetroTube/*\`).
5. No RetroTube, toque em **Configurar busca do YouTube**, cole a chave
   e salve. Faça uma pesquisa; os resultados devem aparecer dentro do site.

Não envie a chave a terceiros nem a coloque no repositório. Para um segundo
dispositivo, configure-a novamente no próprio navegador desse dispositivo.
Consulte as cotas vigentes no Google Cloud. A documentação do YouTube de
outubro de 2026 indica uma alocação padrão de 100 buscas \`search.list\` por dia.

Como alternativa experimental, o usuário pode clicar em **Tentar busca
pública**, que usa instâncias Piped/Invidious sujeitas a falhas e bloqueios.

**Atenção:** o código do RetroTube funciona em JavaScript antigo, mas o player
que vem do YouTube pode não funcionar no Safari do iOS 8.3. Teste no dispositivo.

## Arquivos antigos de servidor (não utilizados)

Os arquivos da pasta backend/, compose.yaml e frontend/config.js foram
preservados como referência para uma possível versão futura com servidor.
Eles **não são utilizados** pelo site publicado atualmente.

### Caso se queira usar a versão com VPS no futuro

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
O endereço do site é:

https://white13291.github.io/RetroTube/

O acesso ao RetroTube não exige conta. A reprodução depende do player do
YouTube e das permissões de incorporação do vídeo, não de uma API própria.

## Restrições importantes

- O player do YouTube pode recusar a incorporação de alguns vídeos.
- A busca oficial requer uma chave da YouTube Data API v3 configurada pelo
  próprio usuário; a compatibilidade com Safari antigo ainda depende da
  conexão HTTPS, CORS e recursos do navegador.
- A busca experimental sem chave usa serviços públicos de terceiros, que
  podem ficar fora do ar.
- Não há conversão nem download de arquivos MP4 pelo nosso servidor.
- A interface usa apenas JavaScript ES5, mas a compatibilidade real com iOS 8.3 depende de teste no dispositivo e de um certificado HTTPS aceito pelo Safari.
- Respeite os direitos dos criadores, as restrições de acesso ao conteúdo e os termos dos serviços utilizados.
