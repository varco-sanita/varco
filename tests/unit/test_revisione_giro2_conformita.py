# SPDX-License-Identifier: EUPL-1.2
"""Controesempi del giro 2 di revisione esterna, area conformità/test/docs
(kit-mmg-review/2026-10-02-giro2/6-conformita-test-docs.md). Non toccati qui, per decisione di
Lorenzo: licenza HL7 (giro 1, bug 5) e completamenti di publiccode.yml/SECURITY.md (giro 1, bug 8).

Ogni test riproduce il controesempio del revisore: falliva sul codice precedente, passa ora.
I test marcati `ufficiale` compilano l'esecutore Java dai sorgenti attuali (JAVA_HOME e prepara.sh).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from varco.conformita.esegui import contesto_offline, main
from varco.conformita.motore import Motore, cartella_conformita

CONF = cartella_conformita()
RADICE = CONF.parent


def _caso(id_: str) -> dict:
    return json.loads((CONF / "casi" / f"{id_}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ N1 caso senza passi


def test_g2_n1_caso_senza_passi_non_e_superato():
    """N1: OFF-001 con passi = []. Prima: «motore SUPERATO»."""
    caso = _caso("OFF-001")
    caso["passi"] = []
    esito = Motore(contesto=contesto_offline()).esegui(caso)
    assert esito.stato == "FALLITO" and "senza passi" in esito.motivo


def test_g2_n1_cli_con_caso_vuoto_non_esce_con_zero(tmp_path, capsys):
    """N1 dalla riga di comando: main(["--famiglia","offline","--solo","OFF-001"]) sul caso svuotato.
    Prima: «SUPERATO OFF-001», exit 0."""
    caso = _caso("OFF-001")
    caso["passi"] = []
    codice = main(["--famiglia", "offline", "--casi", str(_suite(tmp_path, caso)), "--solo", "OFF-001"])
    out = capsys.readouterr().out
    assert "OFF-001" in out and "SUPERATO  OFF-001" not in out and codice != 0


def test_g2_n1_gruppo_di_controllo_caso_originale(tmp_path, capsys):
    assert main(["--famiglia", "offline", "--casi", str(_suite(tmp_path, _caso("OFF-001"))), "--solo", "OFF-001"]) == 0


def _suite(tmp_path: Path, caso: dict) -> Path:
    """Una cartella conformita/ con il solo caso dato e le risposte vere."""
    conf = tmp_path / "conformita"
    (conf / "casi").mkdir(parents=True)
    (conf / "risposte").symlink_to(CONF / "risposte")
    (conf / "casi" / f"{caso['id']}.json").write_text(json.dumps(caso), encoding="utf-8")
    return conf


# ------------------------------------------------------------------ N3 segnaposto nell'NRE atteso


@pytest.mark.parametrize("nre", ["1300A4019294833", "${nre_atteso}"])
def test_g2_n3_segnaposto_nell_nre_atteso_vale_come_il_valore(nre):
    """N3: OFF-001 con atteso {'nre': '${nre_atteso}', 'nre_formato': True} e nre_atteso=1300A4019294833.
    Prima: col letterale SUPERATO, col segnaposto FALLITO («'${nre_atteso}' non rispetta nre_formato»)."""
    caso = _caso("OFF-001")
    caso["passi"][0]["atteso"] = {"nre": nre, "nre_formato": True}
    m = Motore(contesto=contesto_offline() | {"nre_atteso": "1300A4019294833"})
    assert m.esegui(caso).stato == "SUPERATO"


def test_g2_n3_gruppo_di_controllo_nre_letterale_malformato_resta_incoerente():
    caso = _caso("OFF-001")
    caso["passi"][0]["atteso"] = {"nre": "NON-UN-NRE", "nre_formato": True}
    esito = Motore(contesto=contesto_offline()).esegui(caso)
    assert esito.stato == "FALLITO" and "nre_formato" in esito.motivo


# ------------------------------------------------------------------ N4 mutazioni online davvero giudicate


def test_g2_n4_mutazioni_online_scoprono_un_confronto_sempre_verde(monkeypatch):
    """N4: con `verifica` sostituita da una funzione che dà sempre [], il controllo di mutazione
    online passava con 0 chiamate al confronto. Ora il confronto si esercita e il difetto si vede."""
    pytest.importorskip("lxml")
    from varco.conformita import motore as mod
    from test_conformita_mutazioni import _motore, controlla_mutanti

    chiamate = []
    monkeypatch.setattr(mod, "verifica", lambda atteso, oss: chiamate.append(1) or [])
    sbagliati, giudicati, _ = controlla_mutanti("online", _motore())
    assert chiamate and giudicati >= 12
    assert sbagliati, "un confronto che non confronta niente deve far passare dei mutanti"


# ------------------------------------------------------------------ N5 resoconto PDF


def test_g2_n5_resoconto_non_dice_esaurite_le_fonti_pubbliche_se_resta_un_modulo(tmp_path):
    """N5: p. 3 «Tutto quello che si poteva costruire da fonti pubbliche è costruito» e p. 2 Umbria
    «Specifiche pubbliche su GitHub: prossimo modulo». Prima: entrambe nel PDF."""
    interprete = next((x for x in (sys.executable, shutil.which("python3")) if x and subprocess.run(
        [x, "-c", "import reportlab"], capture_output=True).returncode == 0), None)
    if interprete is None:
        pytest.skip("reportlab non installato (serve solo al generatore del resoconto)")
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext non installato")
    if not Path("/System/Library/Fonts/Supplemental/Arial.ttf").exists():
        pytest.skip("il generatore usa i font di sistema di macOS")
    out = tmp_path / "resoconto.pdf"
    r = subprocess.run([interprete, str(RADICE / "strumenti" / "resoconto_pdf.py"), str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    testo = " ".join(subprocess.run(["pdftotext", "-layout", str(out), "-"], capture_output=True, text=True).stdout.split())
    assert "prossimo modulo" in testo  # la tabella resta com'è
    assert "Tutto quello che si poteva costruire da fonti pubbliche è costruito" not in testo
    assert "Da fonti pubbliche resta da costruire Umbria" in testo


# ------------------------------------------------------------------ N1 e N2 sull'esecutore Java


SONDA = """
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

public class SondaEseguiCasi {
    public static void main(String[] a) throws Exception {
        ObjectMapper m = new ObjectMapper();
        ObjectNode oss = (ObjectNode) m.readTree("{\\"esito\\":\\"OK\\",\\"errori\\":[],\\"avvisi\\":[]}");
        System.out.println("SYNTAX=" + EseguiCasiFse.verifica(m.readTree("{\\"esito\\":\\"SYNTAX_ERROR\\"}"), oss));
        System.out.println("CHIAVE=" + EseguiCasiFse.verifica(m.readTree("{\\"esito\\":\\"OK\\",\\"errore_inesistente\\":\\"IMPOSSIBILE\\"}"), oss));
        System.out.println("BUONO=" + EseguiCasiFse.verifica(m.readTree("{\\"esito\\":\\"OK\\"}"), oss));
        System.out.println("VUOTO=" + EseguiCasiFse.difettiCaso(m.readTree("{\\"id\\":\\"FSE-999\\",\\"passi\\":[]}")));
    }
}
"""


@pytest.mark.ufficiale
def test_g2_n2_esecutore_java_non_ignora_le_aspettative_sconosciute(tmp_path):
    """N2 (e N1 lato Java): EseguiCasiFse.verifica con osservato {"esito":"OK"} e atteso
    {"esito":"OK","errore_inesistente":"IMPOSSIBILE"}. Prima: CHIAVE_IGNORATA=[]. Compilato dai sorgenti attuali."""
    from varco.fse.validazione import cartella_validatore_ufficiale

    qui = cartella_validatore_ufficiale()
    validatore = RADICE / "specifiche" / "fse" / "it-fse-gtw-validator"
    cp_txt = validatore / "target" / "cp.txt"
    if not cp_txt.exists():
        pytest.skip("validatore ufficiale non preparato (strumenti/validatore-ufficiale/prepara.sh)")
    java = Path(os.environ["JAVA_HOME"]) / "bin"
    classi = tmp_path / "classi"
    (tmp_path / "SondaEseguiCasi.java").write_text(SONDA, encoding="utf-8")
    cp = f"{validatore / 'target' / 'classes'}:{cp_txt.read_text().strip()}"
    r = subprocess.run([str(java / "javac"), "-nowarn", "-d", str(classi), "-cp", cp,
                        str(qui / "src" / "ValidatoreUfficiale.java"), str(qui / "src" / "EseguiCasiFse.java"),
                        str(tmp_path / "SondaEseguiCasi.java")], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    r = subprocess.run([str(java / "java"), "-cp", f"{classi}:{cp}", "SondaEseguiCasi"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    righe = dict(x.split("=", 1) for x in r.stdout.strip().splitlines())
    assert righe["SYNTAX"] != "[]"  # gruppo di controllo: già prima falliva
    assert "errore_inesistente" in righe["CHIAVE"]
    assert righe["BUONO"] == "[]"
    assert "senza passi" in righe["VUOTO"]
