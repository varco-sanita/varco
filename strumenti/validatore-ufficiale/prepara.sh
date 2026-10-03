#!/bin/sh
# SPDX-License-Identifier: EUPL-1.2
# Prepara il banco del validatore UFFICIALE FSE 2.0 (senza Docker).
# Serve: git, JDK 21 (JAVA_HOME), Maven. Rete: solo GitHub e Maven Central.
set -eu
QUI=$(cd "$(dirname "$0")" && pwd)
RADICE=$(cd "$QUI/../.." && pwd)
FSE="$RADICE/specifiche/fse"
VALIDATORE="$FSE/it-fse-gtw-validator"
COMMIT_VALIDATORE=fdf3854b57a8ea45d85a08519d541698e853c845   # main del 16/03/2026
DISPATCHER="$FSE/it-fse-gtw-dispatcher"
COMMIT_DISPATCHER=9cc8aa794a170ba3133763fc94b89ba7127305bf   # main del 23/06/2026
DUMP="$FSE/mongo-dump"

: "${JAVA_HOME:?imposta JAVA_HOME su un JDK 21 (es. /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home)}"

if [ ! -d "$VALIDATORE/.git" ]; then
  git clone -q https://github.com/ministero-salute/it-fse-gtw-validator.git "$VALIDATORE"
fi
(cd "$VALIDATORE" && git fetch -q --depth 1 origin "$COMMIT_VALIDATORE" 2>/dev/null || true; git checkout -q "$COMMIT_VALIDATORE")

if [ ! -d "$DISPATCHER/.git" ]; then
  git clone -q https://github.com/ministero-salute/it-fse-gtw-dispatcher.git "$DISPATCHER"
fi
(cd "$DISPATCHER" && git fetch -q --depth 1 origin "$COMMIT_DISPATCHER" 2>/dev/null || true; git checkout -q "$COMMIT_DISPATCHER")

# dump dei dizionari: scaricati e VERIFICATI (sha256) dallo script del manifesto,
# che controlla anche il commit dei due repository qui sopra
"${PYTHON:-python3}" "$RADICE/strumenti/scarica_specifiche.py" --gruppi fse-validatore

(cd "$VALIDATORE" && mvn -q -B -DskipTests compile && mvn -q -B dependency:build-classpath -Dmdep.outputFile=target/cp.txt)
mkdir -p "$QUI/classi"
"$JAVA_HOME/bin/javac" -nowarn -d "$QUI/classi" -cp "$VALIDATORE/target/classes:$(cat "$VALIDATORE/target/cp.txt")" \
  "$QUI/src/ValidatoreUfficiale.java" "$QUI/src/EseguiCasiFse.java"
(cd "$DISPATCHER" && mvn -q -B -DskipTests compile && mvn -q -B dependency:build-classpath -Dmdep.outputFile=target/cp.txt)
"$JAVA_HOME/bin/javac" -nowarn -d "$QUI/classi" -cp "$DISPATCHER/target/classes:$(cat "$DISPATCHER/target/cp.txt")" "$QUI/src/EstraiCdaUfficiale.java"
echo "pronto: $QUI/valida.sh <file.xml>..."
