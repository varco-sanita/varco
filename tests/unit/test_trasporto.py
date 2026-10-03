# SPDX-License-Identifier: EUPL-1.2
import base64
import json
import time
import urllib.request

import pytest

from varco import AmbienteBloccato, ConfigurazioneNonValida, Credenziali, ErroreSOAP, ErroreTrasporto
from varco.ambienti import Ambiente, e_produzione, verifica_url_consentito
from varco.trasporto import CanaleSAC, RegistratoreFile, Richiesta, Risposta, TrasportoHTTP, sessione_2f_test
from varco.trasporto.http import LimitatoreFrequenza
from varco.trasporto.soap import sbusta

PROD = "https://demservice.sanita.finanze.it/DemRicettaPrescrittoServicesWeb/services/demInvioPrescritto"
TEST = "https://demservicetest.sanita.finanze.it/DemRicettaPrescrittoServicesWeb/services/demInvioPrescritto"


@pytest.fixture
def rete_vietata(monkeypatch):
    def vietato(*a, **k):
        raise AssertionError("nessuna chiamata di rete doveva partire")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", vietato)


@pytest.mark.parametrize(
    "url,atteso",
    [
        (PROD, True),
        ("https://DEMSERVICE.sanita.finanze.it/x", True),
        ("https://altroservizio.sanita.finanze.it/x", True),
        ("https://demservice.sanita.finanze.it./x", True),  # FQDN con punto finale
        ("https://demservice.sanita.finanze.it.:443/x", True),  # prudenza: senza "test" = produzione
        (TEST, False),
        ("https://localhost:8443/x", False),
    ],
)
def test_riconoscimento_produzione(url, atteso):
    assert e_produzione(url) is atteso


def test_guardia_produzione_blocca_prima_della_rete(rete_vietata):
    t = TrasportoHTTP()
    with pytest.raises(AmbienteBloccato):
        t.invia(Richiesta("x", PROD, b"<x/>"))
    with pytest.raises(AmbienteBloccato):
        verifica_url_consentito(PROD, consenti_produzione="si")  # solo True esplicito sblocca


def test_guardia_produzione_si_sblocca_solo_col_flag(monkeypatch):
    chiamate = []

    class FintaRisposta:
        status = 200
        headers = {}

        def read(self):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def finto_open(self, req, timeout):
        chiamate.append(req.full_url)
        return FintaRisposta()

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", finto_open)
    TrasportoHTTP(consenti_produzione=True).invia(Richiesta("x", PROD, b"<x/>"))
    assert chiamate == [PROD]


def test_solo_https(rete_vietata):
    with pytest.raises(ErroreTrasporto):
        TrasportoHTTP().invia(Richiesta("x", "http://demservicetest.sanita.finanze.it/x", b""))


def test_frequenza_massima_2_al_secondo():
    with pytest.raises(ValueError):
        TrasportoHTTP(intervallo_minimo_s=0.1)


def test_limitatore_distanzia_le_chiamate():
    lim = LimitatoreFrequenza(0.5)
    host = f"host-{time.time()}"
    t0 = time.monotonic()
    lim.attendi(host)
    lim.attendi(host)
    lim.attendi(host)
    assert time.monotonic() - t0 >= 0.95


def test_canale_produzione_richiede_sessione_2f_reale():
    cred = Credenziali("U", "P", "1234")
    with pytest.raises(ConfigurazioneNonValida):
        CanaleSAC(cred, ambiente=Ambiente.PRODUZIONE)


def test_sessione_2f_formato_test():
    import datetime as dt

    assert (
        sessione_2f_test("PROVAX00X00X000Y", adesso=dt.datetime(2026, 9, 30))
        == "PROVAX00X00X000Y-2026-09-RICETTA-DEM-PRESCRITTORE"
    )


class TrasportoRegistrato:
    def __init__(self, corpo: bytes, stato: int = 200):
        self.corpo, self.stato, self.richieste = corpo, stato, []

    def invia(self, r: Richiesta) -> Risposta:
        self.richieste.append(r)
        return Risposta(self.stato, self.corpo, {}, 0.01)


def test_intestazioni_sac():
    import xml.etree.ElementTree as ET

    t = TrasportoRegistrato(b"<e:Envelope xmlns:e='http://schemas.xmlsoap.org/soap/envelope/'><e:Body><ok/></e:Body></e:Envelope>")
    canale = CanaleSAC(Credenziali("MEDICO", "segreta", "123"), trasporto=t)
    el, grezza = canale.chiama("s", "/percorso", "urn:azione", ET.Element("x"))
    r = t.richieste[0]
    assert r.url == "https://demservicetest.sanita.finanze.it/percorso"
    assert r.intestazioni["Authorization"] == "Basic " + base64.b64encode(b"MEDICO:segreta").decode()
    assert r.intestazioni["Authorization2F"].startswith("Bearer MEDICO-")
    assert r.intestazioni["Authorization2F"].endswith("-RICETTA-DEM-PRESCRITTORE")
    assert r.intestazioni["SOAPAction"] == '"urn:azione"'
    assert el.tag == "ok" and grezza.stato_http == 200


def test_fault_soap():
    xml = (b"<?xml version='1.0' ?><env:Envelope xmlns:env='http://schemas.xmlsoap.org/soap/envelope/'>"
           b"<env:Body><env:Fault><faultcode>env:Client</faultcode><faultstring>Credenziali invalide</faultstring>"
           b"</env:Fault></env:Body></env:Envelope>")
    with pytest.raises(ErroreSOAP) as e:
        sbusta(xml, 500)
    assert e.value.credenziali_rifiutate and e.value.stato_http == 500


@pytest.mark.parametrize("xml", [b"<html>errore</html>", b"non xml", b"<e:Envelope xmlns:e='http://schemas.xmlsoap.org/soap/envelope/'/>"])
def test_risposte_non_soap(xml):
    with pytest.raises(ErroreTrasporto):
        sbusta(xml, 502)


def test_registratore_maschera_credenziali(tmp_path):
    reg = RegistratoreFile(tmp_path)
    richiesta = Richiesta("sac.prova", TEST, b"<a/>", {"Authorization": "Basic segreto", "Authorization2F": "Bearer x", "X": "y"})
    reg(richiesta, Risposta(200, b"<b/>", {}, 0.1), None)
    meta = json.loads(next(tmp_path.glob("*_meta.json")).read_text())
    assert meta["intestazioni_richiesta"] == {"Authorization": "***", "Authorization2F": "***", "X": "y"}
    assert "segreto" not in "".join(p.read_text() for p in tmp_path.iterdir())
    assert len(list(tmp_path.glob("*_risposta.xml"))) == 1


def test_credenziali_da_env_e_file(tmp_path):
    c = Credenziali.da_env(ambiente={"VARCO_UTENTE": "U", "VARCO_PASSWORD": "P", "VARCO_PINCODE": "1"})
    assert (c.utente, c.cf) == ("U", "U")
    assert "P" not in repr(c) and "pincode" not in repr(c)
    f = tmp_path / "c.toml"
    f.write_text('[credenziali]\nutente = "A"\npassword = "B"\npincode = "C"\ncf_medico = "CF"\n')
    c = Credenziali.da_file(f)
    assert (c.utente, c.password, c.pincode, c.cf) == ("A", "B", "C", "CF")
    with pytest.raises(ConfigurazioneNonValida):
        Credenziali.da_env(ambiente={})


def test_redirect_rifiutato():
    """Un 302 (anche verso la produzione) non viene seguito: aggirerebbe la guardia."""
    import http.server
    import threading

    class Gestore(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", PROD)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Gestore)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        with pytest.raises(ErroreTrasporto, match="Redirect 302"):
            TrasportoHTTP().invia(Richiesta("x", f"http://127.0.0.1:{srv.server_port}/x", b"<x/>"))
    finally:
        srv.server_close()
