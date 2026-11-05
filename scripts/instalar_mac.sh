#!/bin/bash
# Instala a automação de NFS-e no Mac. Uso: ./scripts/instalar_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Procurando Python 3.10 ou mais novo"
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10+ não encontrado. Instale com:"
  echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  echo "  brew install python@3.12"
  exit 1
fi
echo "    usando $PY ($("$PY" --version))"

echo "==> Criando ambiente virtual em .venv"
"$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

mkdir -p segredos dados
chmod 700 segredos

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "==> config.yaml criado (ambiente: homologacao, aprovação: manual)"
fi

if [ ! -f .env ]; then
  cat > .env <<ENV
# Credenciais — este arquivo NUNCA vai para o git.
NFSE_CERT_PFX=$(pwd)/segredos/certificado.pfx
NFSE_CERT_SENHA=
GMAIL_USUARIO=
GMAIL_SENHA_APP=
# Opcional: só usada se o PDF da Roma mudar de formato
ANTHROPIC_API_KEY=
ENV
  chmod 600 .env
  echo "==> .env criado — preencha as senhas (veja docs/INSTALACAO_MAC.md)"
fi

echo
echo "Pronto. Próximos passos:"
echo "  1. Copie o certificado para: $(pwd)/segredos/certificado.pfx"
echo "  2. Preencha o .env:   open -e .env"
echo "  3. Teste:             .venv/bin/python -m nfse_auto testar-certificado"
