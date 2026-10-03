#!/bin/sh
# SPDX-License-Identifier: EUPL-1.2
# Esegue i casi di conformità FSE (conformita/casi, famiglia fse) con l'esecutore JAVA e il validatore ufficiale.
# Uso: esegui_casi_fse.sh [rapporto.json]
set -eu
QUI=$(cd "$(dirname "$0")" && pwd)
RADICE=$(cd "$QUI/../.." && pwd)
VALIDATORE="$RADICE/specifiche/fse/it-fse-gtw-validator"
: "${JAVA_HOME:?imposta JAVA_HOME su un JDK 21}"
exec "$JAVA_HOME/bin/java" -Xmx3g \
  -cp "$QUI/classi:$VALIDATORE/target/classes:$(cat "$VALIDATORE/target/cp.txt")" \
  EseguiCasiFse "$RADICE/specifiche/fse/mongo-dump" "$RADICE/conformita" "$@"
