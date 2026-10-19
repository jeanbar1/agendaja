#!/usr/bin/env bash
#
# Roda a fila de notificacoes. E esse script que o cron chama.
#
# Duas coisas que quebram se eu nao cuidar aqui:
#
# 1. O cron nao carrega o meu .bashrc, entao o "python" dele nao e o do
#    venv - por isso eu aponto o caminho completo do interpretador.
# 2. O python-decouple procura o .env na pasta atual, entao o cd
#    tem que vir antes do manage.py.

set -euo pipefail

PROJETO="$HOME/Documentos/TCC/agendaja"

# O venv pode estar dentro do projeto ou na pasta de cima. Procuro nos
# dois em vez de chutar.
if [ -x "$PROJETO/.venv/bin/python" ]; then
    PYTHON="$PROJETO/.venv/bin/python"
elif [ -x "$PROJETO/../.venv/bin/python" ]; then
    PYTHON="$PROJETO/../.venv/bin/python"
else
    echo "Nao encontrei o venv a partir de $PROJETO" >&2
    exit 1
fi

mkdir -p "$PROJETO/logs"
cd "$PROJETO"

exec "$PYTHON" manage.py enviar_lembretes >> "$PROJETO/logs/lembretes.log" 2>&1
