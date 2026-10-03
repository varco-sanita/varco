#!/bin/sh
# SPDX-License-Identifier: EUPL-1.2
# Valida uno o più CDA con il codice del validatore ufficiale FSE 2.0. Una riga JSON per file.
set -eu
QUI=$(cd "$(dirname "$0")" && pwd)
RADICE=$(cd "$QUI/../.." && pwd)
VALIDATORE="$RADICE/specifiche/fse/it-fse-gtw-validator"
: "${JAVA_HOME:?imposta JAVA_HOME su un JDK 21}"
exec "$JAVA_HOME/bin/java" -Xmx3g -Dlogging.level.root=WARN \
  -cp "$QUI/classi:$VALIDATORE/target/classes:$(cat "$VALIDATORE/target/cp.txt")" \
  ValidatoreUfficiale "$RADICE/specifiche/fse/mongo-dump" "$@"
