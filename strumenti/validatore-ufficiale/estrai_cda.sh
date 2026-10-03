#!/bin/sh
# SPDX-License-Identifier: EUPL-1.2
# Estrae il CDA dai PDF con il codice del dispatcher ufficiale FSE 2.0. Una riga "RISULTATO {json}" per file.
set -eu
QUI=$(cd "$(dirname "$0")" && pwd)
RADICE=$(cd "$QUI/../.." && pwd)
DISPATCHER="$RADICE/specifiche/fse/it-fse-gtw-dispatcher"
: "${JAVA_HOME:?imposta JAVA_HOME su un JDK 21}"
exec "$JAVA_HOME/bin/java" -cp "$QUI/classi:$DISPATCHER/target/classes:$(cat "$DISPATCHER/target/cp.txt")" EstraiCdaUfficiale "$@"
