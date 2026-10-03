# SPDX-License-Identifier: EUPL-1.2
"""Il validatore UFFICIALE del gateway FSE 2.0 (codice it-fse-gtw-validator) sui documenti del kit.

Nessuna rete: il validatore gira in locale (strumenti/validatore-ufficiale). Si attivano da soli
se il banco è pronto (prepara.sh eseguito e JAVA_HOME impostato su un JDK 21).
"""

import json
import subprocess

import pytest

from varco.conformita.motore import cartella_conformita
from varco.fse import cda_pss, esempi
from varco.fse.validazione import ValidatoreUfficiale, cartella_validatore_ufficiale

pytestmark = pytest.mark.ufficiale


def test_documenti_generati_ora_validi_per_il_gateway(tmp_path):
    percorsi = []
    for nome, pss in (("completo.xml", esempi.pss_completo()), ("assenze.xml", esempi.pss_assenze())):
        p = tmp_path / nome
        p.write_bytes(cda_pss.genera_xml(pss))
        percorsi.append(p)
    esiti = ValidatoreUfficiale().valida_file(percorsi)
    assert [e.esito for e in esiti] == ["OK", "OK"], [e.errori for e in esiti]
    assert all(e.vocabolario_verificato for e in esiti)


def test_esempio_ufficiale_del_ministero_valido():
    """Gruppo di controllo positivo: un PSS pubblicato dal Ministero come valido deve passare."""
    esempio = cartella_conformita().parent / "specifiche" / "fse" / "it-fse-support" / "doc" / "esempi" / "CDA" / "PSS.xml"
    if not esempio.exists():
        pytest.skip("esempio ufficiale non scaricato")
    assert ValidatoreUfficiale().valida_file([esempio])[0].esito == "OK"


def test_esecutore_java_dei_casi_fse(tmp_path):
    """I casi JSON eseguiti da un altro linguaggio (Java) con il validatore ufficiale."""
    rapporto = tmp_path / "rapporto.json"
    r = subprocess.run([str(cartella_validatore_ufficiale() / "esegui_casi_fse.sh"), str(rapporto)],
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    dati = json.loads(rapporto.read_text())
    assert dati["falliti"] == 0 and dati["errori"] == 0 and dati["superati"] >= 7
