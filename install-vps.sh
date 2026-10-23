#!/usr/bin/env bash
# Ubuntu Server 22.04/24.04: sudo bash install-vps.sh
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Execute com sudo: sudo bash install-vps.sh" >&2
  exit 1
fi
cd "$(dirname "$0")"

PORT="${RETROTUBE_PORT:-8080}"
case "$PORT" in
  ''|*[!0-9]*) echo "RETROTUBE_PORT precisa ser um número." >&2; exit 1 ;;
esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  echo "Escolha uma porta entre 1024 e 65535." >&2; exit 1
fi

echo "[1/4] Verificando Docker e Git…"
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y docker.io git curl
fi
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  apt-get update
  apt-get install -y docker-compose-v2 || apt-get install -y docker-compose-plugin || apt-get install -y docker-compose
fi
systemctl enable --now docker

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
else
  COMPOSE=(docker-compose)
fi

if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :$PORT" | grep -q .; then
  if ! "${COMPOSE[@]}" ps --status running 2>/dev/null | grep -q retrotube; then
    echo "A porta $PORT parece ocupada. Execute: RETROTUBE_PORT=8081 sudo -E bash install-vps.sh" >&2
    exit 1
  fi
fi

echo "[2/4] Criando configuração…"
printf 'RETROTUBE_PORT=%s\n' "$PORT" > .env
chmod 600 .env

echo "[3/4] Instalando e iniciando os contêineres…"
"${COMPOSE[@]}" up -d --build

echo "[4/4] Verificando API local…"
if ! command -v curl >/dev/null 2>&1; then apt-get install -y curl; fi
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if curl -fsS --max-time 4 "http://127.0.0.1:$PORT/api/health"; then
    echo
    echo "Pronto! No iPad, abra: http://IP-DA-SUA-VPS:$PORT/"
    echo "Descubra o IP público no painel do provedor da VPS."
    echo "Se houver firewall, libere TCP/$PORT no Ubuntu e no painel do provedor."
    echo "Para ver os logs: sudo ${COMPOSE[*]} logs -f --tail=50"
    exit 0
  fi
  sleep 3
done
echo "O contêiner iniciou, mas a API ainda não respondeu. Veja os logs:" >&2
"${COMPOSE[@]}" logs --tail=60
exit 1
