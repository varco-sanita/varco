# SPDX-License-Identifier: EUPL-1.2
import os
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parent.parent
KIT_DEFAULT = RADICE / "specifiche" / "kit" / "kit-ricetta-dematerializzata-datamatrix"

# Schemi HL7 (XSD del CDA R2 e schematron del PSS): non sono nel repository, li scarica
# `python strumenti/scarica_specifiche.py --gruppi cda-xsd` qui sotto. Le variabili d'ambiente,
# se impostate, vincono.
os.environ.setdefault("VARCO_CDA_XSD", str(RADICE / "specifiche" / "fse" / "cda-xsd"))
os.environ.setdefault("VARCO_SCHEMATRON", str(RADICE / "specifiche" / "fse" / "schematron"))

from varco.ambiente import leggi  # noqa: E402
from varco.fse import validazione as _validazione  # noqa: E402

# Schemi HL7 assenti (giro 3 di revisione, conformità n. 1). Un test che li usa lo DICHIARA con
# `@pytest.mark.schemi_hl7`: se gli schemi mancano PRIMA dell'esecuzione, risulta SALTATO senza
# girare. Dopo l'esecuzione nessun esito si cambia: un fallimento resta un fallimento, anche se nel
# frattempo qualcuno ha cercato gli schemi senza trovarli. Un test non dichiarato che inciampa in
# `SchemiNonTrovati` risulta ROSSO: dice che la dichiarazione manca, non nasconde niente.


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if item.get_closest_marker("schemi_hl7") and not _validazione.schemi_locali_presenti():
        pytest.skip("schemi HL7 non scaricati (python strumenti/scarica_specifiche.py --gruppi cda-xsd)")


@pytest.fixture(scope="session")
def kit() -> Path:
    p = Path(leggi("VARCO_KIT_MEF") or KIT_DEFAULT)
    if not (p / "Servizi Prescrittore" / "PosizioniTestMedico.xlsx").exists():
        pytest.skip(f"kit MEF non presente in {p}")
    return p


def pytest_collection_modifyitems(config, items):
    from varco.fse.validazione import validatore_ufficiale_pronto

    salta_rete = pytest.mark.skip(reason="test di integrazione: impostare VARCO_INTEGRAZIONE=1")
    salta_uff = pytest.mark.skip(reason="validatore ufficiale FSE non pronto: strumenti/validatore-ufficiale/prepara.sh e JAVA_HOME")
    rete = leggi("VARCO_INTEGRAZIONE") == "1"
    ufficiale = validatore_ufficiale_pronto()
    for item in items:
        if item.get_closest_marker("integrazione") and not rete:
            item.add_marker(salta_rete)
        if item.get_closest_marker("ufficiale") and not ufficiale:
            item.add_marker(salta_uff)
