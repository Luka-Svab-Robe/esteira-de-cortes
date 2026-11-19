#!/usr/bin/env bash
# Instalação no Linux/macOS: ./instalar.sh  (repassa as opções para instalar.py)
set -e
cd "$(dirname "$0")"
for py in python3.12 python3.11 python3.13 python3.10 python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    exec "$py" instalar.py "$@"
  fi
done
echo "Python 3.10 ou superior não encontrado. Instale em https://www.python.org/downloads/"
exit 1
