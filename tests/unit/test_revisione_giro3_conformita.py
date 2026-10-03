# SPDX-License-Identifier: EUPL-1.2
"""Controesempi del giro 3 di revisione esterna (03/10/2026), area 6 (conformità e test) e
Piemonte N2 / giro 2 N3 (aspettative sulle intestazioni).

Rapporti: kit-mmg-review/2026-10-02-giro3/6-conformita-test-docs.md e 5-sar-piemonte.md. Ogni test
riproduce il controesempio del revisore (prima della correzione falliva); i test «famiglia» coprono
le varianti dello stesso principio.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
from pathlib import Path

import pytest

from varco.conformita.esegui import contesto_offline, main
from varco.conformita.motore import Motore, cartella_conformita

pytest_plugins = ["pytester"]

CONF = cartella_conformita()
RADICE = CONF.parent
CONFTEST = Path(__file__).resolve().parent.parent / "conftest.py"


def _caso(id_: str) -> dict:
    return json.loads((CONF / "casi" / f"{id_}.json").read_text(encoding="utf-8"))


# ====================================================================== n. 1: hook di conftest


def _esegui_sonda(pytester, corpo: str, env: dict[str, str] | None = None):
    """Esegue `corpo` come modulo di test con il VERO tests/conftest.py, in un processo a parte."""
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    pytester.makepyfile(test_sonda=corpo)
    pytester.makeini("[pytest]\nmarkers =\n    schemi_hl7: x\n    senza_schemi_hl7: x\n")
    for k, v in (env or {}).items():
        pytester._monkeypatch.setenv(k, v)
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "-q", "-rs")


SONDA_REVISORE = '''
import os
from varco.fse.validazione import schemi_locali_presenti

def test_probe():
    os.environ["VARCO_CDA_XSD"] = "/schemi-assenti-review"
    assert schemi_locali_presenti() is False
    assert 2 + 2 == 5, "REGRESSIONE INDIPENDENTE DAGLI SCHEMI"
'''


def test_g3_c1_fallimento_dopo_la_ricerca_degli_schemi_resta_rosso(pytester):
    """Giro 3, conformità n. 1: «SKIPPED … schemi HL7 non scaricati … 1 skipped, exit 0» per `assert 2 + 2 == 5`."""
    r = _esegui_sonda(pytester, SONDA_REVISORE)
    r.assert_outcomes(failed=1)
    assert r.ret == 1
    assert "REGRESSIONE INDIPENDENTE DAGLI SCHEMI" in r.stdout.str()


SONDA_FAMIGLIA = '''
import os
import pytest
from varco.fse.validazione import SchemiNonTrovati, ValidatoreLocale, schemi_locali_presenti, _schema_cda

ESEGUITI = []

def test_eccezione_intercettata_poi_errore():
    try:
        _schema_cda()
    except SchemiNonTrovati:
        pass
    raise ValueError("REGRESSIONE dopo un SchemiNonTrovati intercettato")

def test_schemi_non_trovati_senza_dichiarazione_e_rosso():
    raise SchemiNonTrovati("test che usa gli schemi senza dichiararlo")

def test_assert_su_un_esito_che_dipende_dagli_schemi():
    assert schemi_locali_presenti(), "il test non ha dichiarato schemi_hl7"

@pytest.mark.schemi_hl7
def test_dichiarato_non_gira_senza_schemi():
    ESEGUITI.append(1)
    raise AssertionError("NON DOVEVA GIRARE: gli schemi mancavano prima")

@pytest.mark.schemi_hl7
@pytest.mark.parametrize("x", [1, 2])
def test_dichiarato_parametrizzato(x):
    raise AssertionError("NON DOVEVA GIRARE")

def test_verde_resta_verde():
    assert not schemi_locali_presenti()
'''


def test_g3_c1_famiglia_nessun_esito_cambiato_dopo_l_esecuzione(pytester):
    """Famiglia n. 1: senza schemi, SALTATO solo chi lo dichiara (prima di girare); ogni altro fallimento,
    anche un SchemiNonTrovati non dichiarato, resta ROSSO; un verde resta verde."""
    r = _esegui_sonda(pytester, SONDA_FAMIGLIA,
                      {"VARCO_CDA_XSD": "/schemi-assenti-review", "VARCO_SCHEMATRON": "/schemi-assenti-review"})
    r.assert_outcomes(failed=3, skipped=3, passed=1)
    assert "NON DOVEVA GIRARE" not in r.stdout.str()


def test_g3_c1_con_gli_schemi_il_test_dichiarato_gira(pytester):
    """Gruppo di controllo: con gli schemi presenti un test `schemi_hl7` GIRA (e qui fallisce, come deve)."""
    from varco.fse.validazione import schemi_locali_presenti

    if not schemi_locali_presenti():
        pytest.skip("schemi HL7 non scaricati: il gruppo di controllo li richiede")
    corpo = "import pytest\n@pytest.mark.schemi_hl7\ndef test_x():\n    assert 2 + 2 == 5\n"
    r = _esegui_sonda(pytester, corpo, {k: os.environ[k] for k in ("VARCO_CDA_XSD", "VARCO_SCHEMATRON")})
    r.assert_outcomes(failed=1)


# ====================================================================== n. 2: caso senza atteso


def test_g3_c2_caso_senza_atteso_fallito():
    """Giro 3, conformità n. 2: «atteso assente: schema_errori 1, motore SUPERATO»."""
    caso = _caso("OFF-001")
    del caso["passi"][0]["atteso"]
    esito = Motore(contesto=contesto_offline()).esegui(caso)
    assert esito.stato == "FALLITO", esito
    assert "atteso" in esito.motivo


def test_g3_c2_cli_senza_atteso_non_esce_con_zero(tmp_path, capsys):
    """Giro 3, conformità n. 2 dalla CLI: «SUPERATO OFF-001 … CLI SENZA ATTESO exit 0»."""
    caso = _caso("OFF-001")
    del caso["passi"][0]["atteso"]
    conf = tmp_path / "conformita"
    (conf / "casi").mkdir(parents=True)
    (conf / "risposte").symlink_to(CONF / "risposte")
    (conf / "schema").symlink_to(CONF / "schema")
    (conf / "casi" / "OFF-001.json").write_text(json.dumps(caso), encoding="utf-8")
    codice = main(["--famiglia", "offline", "--casi", str(conf), "--solo", "OFF-001"])
    out = capsys.readouterr().out
    assert codice != 0 and "SUPERATO  OFF-001" not in out


def test_g3_c2_atteso_vuoto_fallito():
    """Giro 3, conformità n. 2, variante del revisore: `atteso: {}` passava anche lo schema."""
    caso = _caso("OFF-001")
    caso["passi"][0]["atteso"] = {}
    assert Motore(contesto=contesto_offline()).esegui(caso).stato == "FALLITO"


def _tutti_i_casi():
    return sorted(Path(p).stem for p in glob.glob(str(CONF / "casi" / "*.json")))


@pytest.mark.parametrize("variante", ["assente", "vuoto", "null", "lista"])
@pytest.mark.parametrize("id_", _tutti_i_casi())
def test_g3_c2_famiglia_ogni_caso_senza_aspettative_e_fallito(id_, variante):
    """Famiglia n. 2: per OGNI caso della suite (tutte le famiglie, online compresi) togliere o svuotare
    le aspettative di un passo dà FALLITO, prima di ogni SALTATO per credenziali o schemi mancanti."""
    caso = _caso(id_)
    passo = caso["passi"][-1]
    if variante == "assente":
        del passo["atteso"]
    else:
        passo["atteso"] = {"vuoto": {}, "null": None, "lista": []}[variante]
    esito = Motore(contesto=contesto_offline()).esegui(caso)
    assert esito.stato == "FALLITO", (id_, esito.stato, esito.motivo)


@pytest.mark.parametrize("id_", _tutti_i_casi())
def test_g3_c2_gruppo_di_controllo_i_casi_veri_rispettano_il_formato(id_):
    """Gruppo di controllo: nessun caso della suite viene rifiutato dal controllo di formato."""
    assert Motore()._difetti_di_formato(_caso(id_)) == []


SONDA_JAVA = """
import com.fasterxml.jackson.databind.ObjectMapper;

public class SondaAtteso {
    public static void main(String[] a) throws Exception {
        ObjectMapper m = new ObjectMapper();
        String doc = "\\"operazione\\":\\"valida_documento\\",\\"documento\\":\\"documenti/x.xml\\"";
        System.out.println("ASSENTE=" + EseguiCasiFse.difettiCaso(m.readTree("{\\"passi\\":[{" + doc + "}]}")));
        System.out.println("VUOTO=" + EseguiCasiFse.difettiCaso(m.readTree("{\\"passi\\":[{" + doc + ",\\"atteso\\":{}}]}")));
        System.out.println("BUONO=" + EseguiCasiFse.difettiCaso(m.readTree("{\\"passi\\":[{" + doc + ",\\"atteso\\":{\\"esito\\":\\"OK\\"}}]}")));
    }
}
"""


@pytest.mark.ufficiale
def test_g3_c2_esecutore_java_senza_atteso_fallito(tmp_path):
    """Famiglia n. 2, secondo esecutore: anche EseguiCasiFse (Java) rifiuta un passo senza aspettative.
    Compilato dai sorgenti attuali."""
    from varco.fse.validazione import cartella_validatore_ufficiale

    qui = cartella_validatore_ufficiale()
    validatore = RADICE / "specifiche" / "fse" / "it-fse-gtw-validator"
    cp_txt = validatore / "target" / "cp.txt"
    if not cp_txt.exists():
        pytest.skip("validatore ufficiale non preparato (strumenti/validatore-ufficiale/prepara.sh)")
    java = Path(os.environ["JAVA_HOME"]) / "bin"
    classi = tmp_path / "classi"
    (tmp_path / "SondaAtteso.java").write_text(SONDA_JAVA, encoding="utf-8")
    cp = f"{validatore / 'target' / 'classes'}:{cp_txt.read_text().strip()}"
    r = subprocess.run([str(java / "javac"), "-nowarn", "-d", str(classi), "-cp", cp,
                        str(qui / "src" / "ValidatoreUfficiale.java"), str(qui / "src" / "EseguiCasiFse.java"),
                        str(tmp_path / "SondaAtteso.java")], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    r = subprocess.run([str(java / "java"), "-cp", f"{classi}:{cp}", "SondaAtteso"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    righe = dict(x.split("=", 1) for x in r.stdout.strip().splitlines())
    assert "manca 'atteso'" in righe["ASSENTE"]
    assert "vuoto" in righe["VUOTO"]
    assert righe["BUONO"] == "[]"


# ====================================================================== n. 3: ValidatoreLocale esplicito


def test_g3_c3_validatore_locale_esplicito_senza_schemi_saltato(monkeypatch):
    """Giro 3, conformità n. 3: «ERRORE SchemiNonTrovati('Schemi HL7 mancanti in /schemi-assenti-review …')»."""
    from varco.fse.validazione import ValidatoreLocale

    monkeypatch.setenv("VARCO_CDA_XSD", "/schemi-assenti-review")
    r = Motore(validatore_fse=ValidatoreLocale()).esegui(_caso("FSE-003"))
    assert r.stato == "SALTATO", (r.stato, r.motivo)
    assert "schemi" in r.motivo.lower()


# Verdetti che non hanno bisogno dello schema mancante: FSE-103 rifiuta la generazione prima di validare;
# FSE-007 è un errore XSD, trovato prima dello schematron.
_VERDETTO_SENZA_QUELLO_SCHEMA = {("FSE-103", "VARCO_CDA_XSD"), ("FSE-103", "VARCO_SCHEMATRON"),
                                 ("FSE-007", "VARCO_SCHEMATRON")}


@pytest.mark.parametrize("variabile", ["VARCO_CDA_XSD", "VARCO_SCHEMATRON"])
@pytest.mark.parametrize("id_", [i for i in _tutti_i_casi() if i.startswith("FSE-")])
def test_g3_c3_famiglia_nessun_caso_fse_in_errore_senza_schemi(monkeypatch, id_, variabile):
    """Famiglia n. 3: ogni caso FSE, senza XSD o senza schematron, con il validatore esplicito: mai ERRORE,
    mai un verde che avrebbe avuto bisogno dello schema mancante; altrimenti SALTATO."""
    from varco.fse.validazione import ValidatoreLocale

    monkeypatch.setenv(variabile, "/schemi-assenti-review")
    r = Motore(validatore_fse=ValidatoreLocale()).esegui(_caso(id_))
    if (id_, variabile) in _VERDETTO_SENZA_QUELLO_SCHEMA:
        assert r.stato in ("SUPERATO", "SALTATO"), (r.stato, r.motivo)
    else:
        assert r.stato == "SALTATO", (r.stato, r.motivo)


@pytest.mark.schemi_hl7
def test_g3_c3_famiglia_schemi_tolti_dopo_la_scelta_del_validatore(monkeypatch):
    """Famiglia n. 3: il validatore si sceglie con gli schemi presenti, poi gli schemi spariscono: SALTATO."""
    from varco.conformita.motore import validatore_fse_predefinito

    v = validatore_fse_predefinito("locale")
    assert v is not None
    monkeypatch.setenv("VARCO_CDA_XSD", "/schemi-assenti-review")
    r = Motore(validatore_fse=v).esegui(_caso("FSE-003"))
    assert r.stato == "SALTATO", (r.stato, r.motivo)


# ====================================================================== Piemonte N2 / giro 2 N3: intestazioni


def test_g3_pie_n2_prefisso_vuoto_e_assente_insieme_fallito():
    """Giro 3, Piemonte N2: PIE-201 con intestazioni_prefisso {"X-inesistente": ""} e la stessa tra gli assenti:
    «0 / SUPERATO»."""
    caso = _caso("PIE-201")
    atteso = caso["passi"][0]["atteso"]
    atteso["intestazioni_prefisso"] = {"X-inesistente": ""}
    atteso["assenti"].append("X-inesistente")
    assert Motore().esegui(caso).stato == "FALLITO"


@pytest.mark.parametrize("modifica", [
    # prefisso vuoto su un header che non c'è (senza contraddizione dichiarata): assente ≠ vuoto
    lambda a: a.setdefault("intestazioni_prefisso", {}).update({"X-inesistente": ""}),
    # prefisso non vuoto su un header che non c'è
    lambda a: a.setdefault("intestazioni_prefisso", {}).update({"X-inesistente": "Bearer "}),
    # contraddizione con maiuscole diverse (i nomi degli header non le distinguono)
    lambda a: (a.setdefault("intestazioni_prefisso", {}).update({"x-idsessione": "Bearer"}),
               a["assenti"].append("X-IDSESSIONE")),
    lambda a: (a["intestazioni"].update({"x-gestionale": "VARCO_301"}), a["assenti"].append("X-Gestionale")),
    # header presente dichiarato assente
    lambda a: a["assenti"].append("X-Gestionale"),
], ids=["prefisso_vuoto_assente", "prefisso_assente", "contraddizione_prefisso_maiuscole",
        "contraddizione_valore_maiuscole", "presente_dichiarato_assente"])
def test_g3_pie_n2_famiglia_intestazioni(modifica):
    """Famiglia Piemonte N2 (giro 2 N3 chiuso a metà): nessuna aspettativa sulle intestazioni passa senza
    l'header o contraddicendosi."""
    caso = _caso("PIE-201")
    modifica(caso["passi"][0]["atteso"])
    assert Motore().esegui(caso).stato == "FALLITO"


def test_g3_pie_n2_gruppo_di_controllo():
    """Gruppo di controllo: PIE-201 com'è, e con un prefisso vero su un header presente, è SUPERATO."""
    assert Motore().esegui(_caso("PIE-201")).stato == "SUPERATO"
    caso = _caso("PIE-201")
    caso["passi"][0]["atteso"]["intestazioni_prefisso"] = {"X-idSessione": "Bearer "}
    assert Motore().esegui(caso).stato == "SUPERATO"
