# SPDX-License-Identifier: EUPL-1.2
"""Il registratore non deve mai scrivere dati personali reali senza un flag esplicito.

CF usati qui: quelli PUBBLICI di test del kit MEF (PROVAX00X00X000Y, PNIMRA70A01H501P) e
CF sintetici costruiti per il test (RSSMRA80A01H501U, la variante omocodica
RSSMRA80A01H50MU). Nessun dato reale.
"""

import base64
import http.server
import json
import stat
import sys
import warnings
import threading
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import pytest

from varco.trasporto import RegistratoreFile, Richiesta, Risposta, TrasportoHTTP
from varco.trasporto.registro import (
    MODALITA_DATI_PERSONALI_IN_CHIARO,
    MODALITA_REDATTA,
    MODALITA_TEST_IN_CHIARO,
    codici_fiscali_presenti,
)

TEST = "https://demservicetest.sanita.finanze.it/DemRicettaPrescrittoServicesWeb/services/demInvioPrescritto"
PROD = "https://demservice.sanita.finanze.it/DemRicettaPrescrittoServicesWeb/services/demInvioPrescritto"

MEDICO_TEST = "PROVAX00X00X000Y"
ASSISTITO_TEST = "PNIMRA70A01H501P"
SINTETICO = "RSSMRA80A01H501U"
SINTETICO_OMOCODICO = "RSSMRA80A01H50MU"
IDENTITA = frozenset({MEDICO_TEST, ASSISTITO_TEST})
NRE = "1300A4019294833"
CODICE_AUT = "300920261644168700000050622782"
PIN_CIFRATO = base64.b64encode(bytes(range(128))).decode()


def _pdf_con(testo: bytes) -> bytes:
    """PDF minimo con uno stream compresso (come il promemoria del SAC)."""
    return b"%PDF-1.4\n1 0 obj<</Filter/FlateDecode>>stream\n" + zlib.compress(b"BT (" + testo + b") Tj ET") + b"\nendstream\nendobj\n%%EOF\n"


def _basic(utente: str) -> str:
    return "Basic " + base64.b64encode(f"{utente}:pw".encode()).decode()


def _richiesta(cf_assistito_in_chiaro: str = "", utente: str = MEDICO_TEST, url: str = TEST, extra: str = "") -> Richiesta:
    """Come una vera InvioPrescritto: il CF dell'assistito viaggia CIFRATO in codiceAss;
    `cf_assistito_in_chiaro` lo mette anche in testata2 per provare la ricerca nel testo."""
    corpo = (
        f"<s:Envelope xmlns:s='x' xmlns:inv='y'><s:Body><inv:InvioPrescrittoRichiesta>"
        f"<inv:pinCode>{PIN_CIFRATO}</inv:pinCode><inv:cfMedico1>{MEDICO_TEST}</inv:cfMedico1><inv:cfMedico2 />"
        f"<inv:codiceAss>{PIN_CIFRATO}</inv:codiceAss><inv:testata2>{cf_assistito_in_chiaro}</inv:testata2>"
        f"<inv:codDiagnosi>345.90</inv:codDiagnosi>{extra}<inv:codGruppoEquival>G3B</inv:codGruppoEquival>"
        f"</inv:InvioPrescrittoRichiesta></s:Body></s:Envelope>"
    ).encode()
    return Richiesta("sac.invioPrescritto", url, corpo, {"Authorization": _basic(utente), "Authorization2F": "Bearer z", "Cookie": "s=1"})


def _risposta(cf_nel_pdf: str = ASSISTITO_TEST) -> Risposta:
    pdf = base64.b64encode(_pdf_con(cf_nel_pdf.encode())).decode()
    corpo = (
        f"<s:Envelope xmlns:s='x'><s:Body><InvioPrescrittoRicevuta><nre>{NRE}</nre>"
        f"<codAutenticazione>{CODICE_AUT}</codAutenticazione><codEsitoInserimento>0000</codEsitoInserimento>"
        f"<messaggio>COGNOME_MEDICO=PRO</messaggio><pdfPromemoria>{pdf}</pdfPromemoria>"
        f"</InvioPrescrittoRicevuta></s:Body></s:Envelope>"
    ).encode()
    return Risposta(200, corpo, {"Set-Cookie": "s=2", "Content-Type": "text/xml"}, 0.2)


def _tutto(cartella: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(cartella.iterdir()))


def _meta(cartella: Path) -> dict:
    return json.loads(next(cartella.glob("*_meta.json")).read_text(encoding="utf-8"))


SEGRETI = [MEDICO_TEST, ASSISTITO_TEST, NRE, CODICE_AUT, PIN_CIFRATO, "JVBER", "%PDF", "345.90", "COGNOME_MEDICO=PRO"]


# --- default: redatto -----------------------------------------------------------------

def test_default_redige_tutto(tmp_path):
    reg = RegistratoreFile(tmp_path)
    assert reg.modalita == MODALITA_REDATTA
    reg(_richiesta(), _risposta(), None)
    scritto = _tutto(tmp_path).decode()
    for segreto in SEGRETI:
        assert segreto not in scritto, segreto
    assert "s=1" not in scritto and "s=2" not in scritto  # cookie mascherati
    # ciò che serve al debug resta
    assert "<codEsitoInserimento>0000</codEsitoInserimento>" in scritto
    assert "G3B" in scritto
    assert _meta(tmp_path)["modalita"] == MODALITA_REDATTA


def test_redatto_stessa_impronta_dentro_la_stessa_esecuzione(tmp_path):
    reg = RegistratoreFile(tmp_path)
    vis = Richiesta("sac.visualizzaPrescritto", TEST, f"<v><nre>{NRE}</nre></v>".encode(), {})
    reg(vis, _risposta(), None)
    req = next(tmp_path.glob("*_richiesta.xml")).read_text()
    resp = next(tmp_path.glob("*_risposta.xml")).read_text()
    segnaposto = req.split("<nre>")[1].split("</nre>")[0]
    assert segnaposto.startswith("[REDATTO:nre:") and segnaposto in resp
    # un altro registratore ha un'altra chiave: l'impronta non si confronta tra esecuzioni
    altra = tmp_path / "altra"
    RegistratoreFile(altra)(vis, None, None)
    assert segnaposto not in next(altra.glob("*_richiesta.xml")).read_text()


def test_redatto_cf_omocodico_e_in_testo_libero(tmp_path):
    reg = RegistratoreFile(tmp_path)
    corpo = f"<x><messaggio>assistito {SINTETICO_OMOCODICO} e {SINTETICO}</messaggio></x>".encode()
    reg(Richiesta("sac.x", TEST + f"?cf={SINTETICO}", corpo, {}), None, RuntimeError(SINTETICO))
    scritto = _tutto(tmp_path).decode()
    assert SINTETICO not in scritto and SINTETICO_OMOCODICO not in scritto


def test_redatto_sui_file_veri_delle_prove(tmp_path):
    """Le risposte vere del SAC di test (prove/): dopo la redazione niente CF, NRE, PDF."""
    prove = Path(__file__).resolve().parents[2] / "prove" / "20260930-164416"
    req = next(prove.glob("*_01_sac-invioPrescritto_richiesta.xml"), None)
    if req is None:
        pytest.skip("prove/ non presente")
    resp = Path(str(req).replace("_richiesta.xml", "_risposta.xml"))
    assert ASSISTITO_TEST in codici_fiscali_presenti(resp.read_bytes())  # gruppo di controllo: il CF c'è (nel PDF)
    RegistratoreFile(tmp_path)(Richiesta("sac.invioPrescritto", TEST, req.read_bytes(), {}), Risposta(200, resp.read_bytes(), {}, 0.1), None)
    scritto = _tutto(tmp_path)
    assert codici_fiscali_presenti(scritto) == set()
    for segreto in SEGRETI[:6]:
        assert segreto.encode() not in scritto, segreto


def test_trasporto_vero_con_registratore_di_default(tmp_path):
    """Dal trasporto al disco: una chiamata HTTP vera (localhost) con il promemoria."""
    corpo_risposta = _risposta(SINTETICO).corpo

    class Gestore(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(corpo_risposta)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Gestore)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t = TrasportoHTTP(registratore=RegistratoreFile(tmp_path), intervallo_minimo_s=0.5)
        r = t.invia(_richiesta(cf_assistito_in_chiaro=SINTETICO, url=f"http://127.0.0.1:{srv.server_port}/x"))
        assert SINTETICO.encode() in zlib.decompress(base64.b64decode(r.corpo.split(b"<pdfPromemoria>")[1].split(b"</")[0]).split(b"stream\n")[1].split(b"\nendstream")[0])
    finally:
        srv.shutdown()
    assert codici_fiscali_presenti(_tutto(tmp_path)) == set()


# --- in chiaro solo con dati di test ---------------------------------------------------

def test_dati_di_test_in_chiaro(tmp_path):
    reg = RegistratoreFile(tmp_path, identita_di_test=IDENTITA)
    assert reg.modalita == MODALITA_TEST_IN_CHIARO
    rq, rs = _richiesta(), _risposta()
    reg(rq, rs, None)
    # in chiaro tutto, tranne le credenziali: il pincode (anche cifrato) è mascherato in ogni modalità
    scritta = next(tmp_path.glob("*_richiesta.xml")).read_bytes()
    assert b"<inv:pinCode>[REDATTO:credenziale:" in scritta
    atteso = ET.fromstring(rq.corpo)
    pin = next(e for e in atteso.iter() if e.tag.endswith("pinCode"))
    pin.text = next(e for e in ET.fromstring(scritta).iter() if e.tag.endswith("pinCode")).text
    assert ET.canonicalize(ET.tostring(atteso), rewrite_prefixes=True) == ET.canonicalize(scritta, rewrite_prefixes=True)
    assert next(tmp_path.glob("*_risposta.xml")).read_bytes() == rs.corpo  # niente credenziali: byte identici
    meta = _meta(tmp_path)
    assert meta["modalita"] == MODALITA_TEST_IN_CHIARO and "in_chiaro_negato" not in meta
    assert meta["intestazioni_richiesta"]["Authorization"] == "***"


@pytest.mark.parametrize(
    "richiesta, risposta, motivo",
    [
        (_richiesta(cf_assistito_in_chiaro=SINTETICO), _risposta(), "codici fiscali non di test"),
        (_richiesta(), _risposta(cf_nel_pdf=SINTETICO), "codici fiscali non di test"),  # solo dentro il PDF compresso
        (_richiesta(cf_assistito_in_chiaro=SINTETICO_OMOCODICO), _risposta(), "codici fiscali non di test"),
        (_richiesta(utente=SINTETICO), _risposta(), "utente non tra le identità di test"),
        (_richiesta(url=PROD), _risposta(), "host non di test"),
        (_richiesta(url="https://example.org/x"), _risposta(), "host non di test"),
        (Richiesta("sac.x", TEST, b"<a/>", {}), None, "utente non verificabile"),
    ],
)
def test_dati_non_di_test_si_redigono_comunque(tmp_path, richiesta, risposta, motivo):
    reg = RegistratoreFile(tmp_path, identita_di_test=IDENTITA)
    reg(richiesta, risposta, None)
    meta = _meta(tmp_path)
    assert meta["modalita"] == MODALITA_REDATTA
    assert motivo in meta["in_chiaro_negato"]
    scritto = _tutto(tmp_path)
    assert codici_fiscali_presenti(scritto) == set()
    assert SINTETICO.encode() not in scritto and NRE.encode() not in scritto


def test_identita_vuote_rifiutate(tmp_path):
    with pytest.raises(ValueError):
        RegistratoreFile(tmp_path, identita_di_test=[])


# --- tutto in chiaro: solo col flag esplicito -----------------------------------------

@pytest.mark.parametrize("valore", [1, "si", "True", object()])
def test_flag_in_chiaro_deve_essere_proprio_true(tmp_path, valore):
    with pytest.raises(TypeError):
        RegistratoreFile(tmp_path, registra_dati_personali_in_chiaro=valore)


def test_flag_in_chiaro_esplicito(tmp_path):
    with pytest.warns(RuntimeWarning, match="INTERE"):
        reg = RegistratoreFile(tmp_path, registra_dati_personali_in_chiaro=True)
    rq, rs = _richiesta(cf_assistito_in_chiaro=SINTETICO), _risposta(cf_nel_pdf=SINTETICO)
    reg(rq, rs, None)
    assert next(tmp_path.glob("*_risposta.xml")).read_bytes() == rs.corpo
    meta = _meta(tmp_path)
    assert meta["modalita"] == MODALITA_DATI_PERSONALI_IN_CHIARO
    assert meta["intestazioni_richiesta"]["Authorization"] == "***"  # le credenziali no, mai


def test_modalita_esclusive(tmp_path):
    with pytest.raises(ValueError):
        RegistratoreFile(tmp_path, identita_di_test=IDENTITA, registra_dati_personali_in_chiaro=True)


def test_cli_flag_in_chiaro_richiede_registra():
    from varco.conformita.esegui import main

    with pytest.raises(SystemExit):
        main(["--famiglia", "offline", "--registra-dati-personali-in-chiaro"])


# --- casi trovati dalla revisione esterna ----------------------------------------------

@pytest.mark.parametrize(
    "richiesta, risposta, motivo",
    [
        # assistito estero/SASN: nessun CF, ma nome e tessera reali
        (_richiesta(extra="<inv:cognNome>MARIO ROSSI</inv:cognNome><inv:numTessSasn>12345</inv:numTessSasn>"),
         _risposta(), "senza CF verificabile"),
        # CF cifrato e nessuna risposta (errore di rete): non si può verificare l'assistito
        (_richiesta(), None, "cifrato e non verificabile"),
        # CF cifrato e risposta senza promemoria (rifiuto 9999)
        (_richiesta(), Risposta(200, b"<r><codEsitoInserimento>9999</codEsitoInserimento></r>", {}, 0.1),
         "cifrato e non verificabile"),
    ],
)
def test_dati_reali_senza_cf_in_chiaro_si_redigono(tmp_path, richiesta, risposta, motivo):
    reg = RegistratoreFile(tmp_path, identita_di_test=IDENTITA)
    reg(richiesta, risposta, None)
    meta = _meta(tmp_path)
    assert meta["modalita"] == MODALITA_REDATTA
    assert motivo in meta["in_chiaro_negato"]
    scritto = _tutto(tmp_path)
    assert b"MARIO ROSSI" not in scritto and PIN_CIFRATO.encode() not in scritto


def test_redatti_testi_liberi_nre_e_codici_fuori_dai_tag(tmp_path):
    corpo = (
        f"<x><motivazNote>paziente Mario, epilessia</motivazNote>"
        f"<messaggio>ricetta {NRE} codice {CODICE_AUT} assistito {SINTETICO.lower()}</messaggio></x>"
    ).encode()
    RegistratoreFile(tmp_path)(Richiesta("sac.x", TEST, corpo, {}), None, RuntimeError(f"NRE {NRE} {CODICE_AUT}"))
    scritto = _tutto(tmp_path).decode()
    for segreto in ("epilessia", NRE, CODICE_AUT, SINTETICO.lower(), SINTETICO):
        assert segreto not in scritto, segreto
    assert SINTETICO in codici_fiscali_presenti(SINTETICO.lower().encode())  # anche minuscolo si riconosce


@pytest.mark.parametrize("modo", [{}, {"identita_di_test": IDENTITA}, {"registra_dati_personali_in_chiaro": True}])
def test_credenziali_nell_url_mai_su_disco(tmp_path, modo):
    url = "https://utente:segretissima@demservicetest.sanita.finanze.it/x"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        reg = RegistratoreFile(tmp_path, **modo)
    reg(Richiesta("sac.x", url, b"<a/>", {}), None, None)
    assert b"segretissima" not in _tutto(tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="permessi POSIX")
def test_file_e_cartella_ad_accesso_ristretto(tmp_path):
    cartella = tmp_path / "reg"
    RegistratoreFile(cartella)(_richiesta(), _risposta(), None)
    assert stat.S_IMODE(cartella.stat().st_mode) & 0o077 == 0
    for f in cartella.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f


def test_due_registratori_nello_stesso_secondo_non_si_sovrascrivono(tmp_path):
    a, b = RegistratoreFile(tmp_path), RegistratoreFile(tmp_path)
    a(_richiesta(), _risposta(), None)
    b(_richiesta(), _risposta(), None)
    assert len(list(tmp_path.iterdir())) == 6


def test_cli_registra_rifiutato_senza_chiamate():
    from varco.conformita.esegui import main

    with pytest.raises(SystemExit):
        main(["--famiglia", "offline", "--registra", "x"])


def test_credenziali_nell_url_dell_errore_mascherate_anche_in_chiaro(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        reg = RegistratoreFile(tmp_path, registra_dati_personali_in_chiaro=True)
    reg(Richiesta("sac.x", TEST, b"<a/>", {}), None, RuntimeError("https://u:segretissima@host/x"))
    assert b"segretissima" not in _tutto(tmp_path)
