# SPDX-License-Identifier: EUPL-1.2
"""Controesempi del giro 2 di revisione esterna, area SAR Puglia (kit-mmg-review/2026-10-02-giro2/3-sar-puglia.md).

Ogni test riproduce il controesempio del revisore: falliva sul codice precedente, passa ora.
Fixture e aiuti (server finto, certificati di prova, trasporto locale) vengono da test_sist.py.
"""

from __future__ import annotations

import copy
import datetime as _dt
import urllib.error
import urllib.request

import pytest

from test_sist import (  # noqa: F401 - server e p12 sono fixture
    APPLICATIVO,
    ASSISTITO,
    CODICI_REGIONALI,
    SERVER,
    TITOLARE,
    TrasportoLocale,
    _azioni,
    _ricetta_farm,
    _servizio,
    p12,
    server,
)

from varco.errori import RicettaNonValida
from varco.fse.modello import Paziente
from varco.ricetta import Assistito, CriteriNreUtilizzati, RicettaSIST
from varco.ricetta import xml_sist
from varco.ricetta.sist import FirmatarioCAdESPKCS12
from varco.trasporto.sist import CanaleSIST, OperatoreSIST
from varco.trasporto.wssecurity import ChiavePKCS12, NS_WSSE, NS_WSU, verifica_security

etree = pytest.importorskip("lxml.etree")
H = {"h": "urn:hl7-org:v3"}


# ------------------------------------------------------------------ residuo del bug 2 del giro 1


@pytest.mark.schemi_hl7
def test_g2_residuo_bug2_cns_rimossa_durante_ws_security_da_un_esito(server, p12):
    """Residuo bug 2: CNS tolta prima della firma WS-Security di setRegistraPrescrizione.
    Prima: RuntimeError, nessun esito, prescrizione già allocata al server."""
    s = _servizio(server, p12)
    originale = s.canale.chiave.firma_rsa_sha1
    chiamate = []

    def stacca(dati):
        chiamate.append(1)
        if len(chiamate) == 2:
            raise RuntimeError("CNS rimossa prima della firma WS-Security")
        return originale(dati)

    s.canale.chiave.firma_rsa_sha1 = stacca
    e = s.invia(_ricetta_farm(), oscurato=True, id_pcp="PCP-PROVA")
    assert _azioni(server) == ["chkPrescrizione"] and len(server.stato.prescrizioni) == 1
    assert e.ok and e.nre and e.registrato is False and e.da_ripetere
    assert "CNS rimossa prima della firma WS-Security" in e.errore_registrazione
    assert e.oscurato is True and e.id_pcp == "PCP-PROVA" and e.cda_firmato
    # CNS reinserita: si ripete la sola registrazione, nessuna seconda prescrizione
    e2 = s.ripeti_registrazione(e)
    assert e2.registrato is True and e2.nre == e.nre
    assert _azioni(server) == ["chkPrescrizione", "setRegistraPrescrizione"]
    assert len(server.stato.prescrizioni) == 1 and server.stato.prescrizioni[e.nre].oscurato is True


# ------------------------------------------------------------------ N1 anagrafica letta due volte


def _paziente(cf: str, nome: str, cognome: str, nascita: _dt.date) -> Paziente:
    a = Assistito(codice_fiscale=cf, provincia="BA", asl="114", codice_regione="160")
    return Paziente(a, nome, cognome, "F", nascita)


@pytest.mark.schemi_hl7
def test_g2_n1_seconda_lettura_anagrafica_non_mescola_le_identita(server, p12):
    """N1: il callback d'anagrafica dà il paziente giusto al controllo, un altro alla firma.
    Prima: CDA con CF dell'assistito e cognome SECONDO, nascita 19800202, registrato True."""
    ricetta = _ricetta_farm()
    paziente_corretto = Paziente(ricetta.assistito, "MARIA", "PROVA", "F", _dt.date(1970, 1, 1))
    altro_paziente = _paziente("PROVAX00X00X000X", "ALTRO", "SECONDO", _dt.date(1980, 2, 2))
    chiamate = []

    def anagrafica(assistito):
        chiamate.append(assistito)
        return paziente_corretto if len(chiamate) == 1 else altro_paziente

    e = _servizio(server, p12, anagrafica=anagrafica).invia(ricetta)
    assert e.ok and e.nre
    # l'identità del CDA è una sola: quella controllata prima di chkPrescrizione
    if e.cda is not None:
        doc = etree.fromstring(e.cda)
        assert doc.find(".//h:recordTarget/h:patientRole/h:id", H).get("extension") == ASSISTITO
        assert doc.findtext(".//h:recordTarget/h:patientRole/h:patient/h:name/h:family", namespaces=H) == "PROVA"
        assert doc.find(".//h:recordTarget/h:patientRole/h:patient/h:birthTime", H).get("value") == "19700101"
    assert b"SECONDO" not in (e.cda or b"")
    if e.registrato:
        assert b"SECONDO" not in server.stato.prescrizioni[e.nre].cda


class CnsAssente:
    def firma_cades(self, dati: bytes) -> bytes:
        raise RuntimeError("CNS rimossa")


@pytest.mark.schemi_hl7
def test_g2_n1_al_recupero_vale_l_anagrafica_controllata(server, p12):
    """N1, percorso di recupero: la firma CAdES era fallita, poi il callback d'anagrafica cambia
    risposta. Prima: `ripeti_registrazione` rileggeva e registrava il CDA con l'altro paziente."""
    import dataclasses

    ricetta = _ricetta_farm()
    paziente_corretto = Paziente(ricetta.assistito, "MARIA", "PROVA", "F", _dt.date(1970, 1, 1))
    altro_paziente = _paziente("PROVAX00X00X000X", "ALTRO", "SECONDO", _dt.date(1980, 2, 2))
    risposte = [paziente_corretto]
    s = _servizio(server, p12, anagrafica=lambda a: risposte[0])
    vero = s.firmatario
    s.firmatario = CnsAssente()
    e = s.invia(ricetta)
    assert e.da_ripetere and e.cda_firmato is None
    s.firmatario = vero
    risposte[0] = altro_paziente
    # senza l'anagrafica controllata nell'esito, la nuova lettura passa il controllo d'identità
    senza = s.ripeti_registrazione(dataclasses.replace(e, paziente=None))
    assert senza.registrato is False and "non contiene l'Assistito" in senza.errore_registrazione
    assert server.stato.prescrizioni[e.nre].cda is None and _azioni(server) == ["chkPrescrizione"]
    # con l'esito di invia: CDA con il paziente controllato
    r = s.ripeti_registrazione(e)
    assert r.registrato is True and b"SECONDO" not in r.cda and b"PROVA" in r.cda
    assert server.stato.prescrizioni[e.nre].cda == r.cda


@pytest.mark.schemi_hl7
def test_g2_n1_gruppo_di_controllo_prima_lettura_sbagliata(server, p12):
    """Gruppo di controllo del revisore: lo stesso paziente sbagliato alla PRIMA lettura è rifiutato."""
    altro = _paziente("PROVAX00X00X000X", "ALTRO", "SECONDO", _dt.date(1980, 2, 2))
    with pytest.raises(RicettaNonValida, match="non contiene l'Assistito"):
        _servizio(server, p12, anagrafica=lambda a: altro).invia(_ricetta_farm())
    assert server.stato.richieste == []


# ------------------------------------------------------------------ N2 Timestamp fresco non firmato


def _busta_scaduta_con_timestamp_fresco(s: RicettaSIST, nre: str) -> tuple[bytes, bytes]:
    """La richiesta setAnnullaPrescrizione firmata due giorni fa, più una copia del Timestamp con
    ID diverso e date attuali inserita DAVANTI all'originale, senza rifirmare."""
    due_giorni_fa = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=2)
    vecchio = CanaleSIST(s.canale.operatore, s.canale.chiave, base_url=s.canale.base_url,
                         applicativo_di_prova=APPLICATIVO, trasporto=TrasportoLocale(),
                         orologio=lambda: due_giorni_fa)
    originale = vecchio.busta(xml_sist.richiesta_annulla(vecchio.dati_chiamata(), nre))
    doc = etree.fromstring(originale)
    ns = {"wsse": NS_WSSE, "wsu": NS_WSU}
    security = doc.find(".//wsse:Security", ns)
    ts = security.find("wsu:Timestamp", ns)
    fresco = copy.deepcopy(ts)
    fresco.set(f"{{{NS_WSU}}}Id", "TS-NON-FIRMATO")
    adesso = _dt.datetime.now(_dt.timezone.utc)
    fresco.find("wsu:Created", ns).text = adesso.strftime("%Y-%m-%dT%H:%M:%SZ")
    fresco.find("wsu:Expires", ns).text = (adesso + _dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    security.insert(0, fresco)
    return originale, etree.tostring(doc)


@pytest.mark.schemi_hl7
def test_g2_n2_timestamp_fresco_non_firmato_non_salva_una_firma_scaduta(server, p12):
    """N2: richiesta firmata due giorni prima + Timestamp fresco NON firmato davanti all'originale.
    Prima: verifica_security True e il server finto annullava la prescrizione (HTTP 200, esito TRUE)."""
    s = _servizio(server, p12)
    e = s.invia(_ricetta_farm())
    assert e.registrato is True
    originale, modificata = _busta_scaduta_con_timestamp_fresco(s, e.nre)
    adesso = _dt.datetime.now(_dt.timezone.utc)
    assert verifica_security(originale, adesso=adesso).valida is False  # gruppo di controllo
    esito = verifica_security(modificata, adesso=adesso)
    assert esito.valida is False, esito
    req = urllib.request.Request(f"{s.canale.base_url}/CVPService", data=modificata, method="POST", headers={
        "Content-Type": "text/xml;charset=UTF-8",
        "SOAPAction": f'"{SERVER.AZIONE}setAnnullaPrescrizione"'})
    with pytest.raises(urllib.error.HTTPError) as http:
        urllib.request.urlopen(req, timeout=10)  # noqa: S310 - solo 127.0.0.1
    assert http.value.code == 500 and b"000265" in http.value.read()
    assert server.stato.prescrizioni[e.nre].stato_sist == "1"  # non annullata


def test_g2_n2_gruppo_di_controllo_busta_fresca_valida():
    """Controllo: una busta firmata adesso, senza manomissioni, resta valida."""
    from varco.trasporto.wssecurity import intestazione_security

    # la chiave serve solo a firmare: un p12 al volo
    import tempfile
    from pathlib import Path

    from test_sist import _p12

    with tempfile.TemporaryDirectory() as d:
        chiave = ChiavePKCS12(str(_p12(Path(d), TITOLARE)), b"pw")
        sec = intestazione_security(chiave, prefisso_soap="soapenv")
        busta = (f'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
                 f"<soapenv:Header>{sec}</soapenv:Header><soapenv:Body/></soapenv:Envelope>").encode()
        assert verifica_security(busta, adesso=_dt.datetime.now(_dt.timezone.utc)).valida is True


# ------------------------------------------------------------------ N3 periodo di erogazione


@pytest.mark.schemi_hl7
def test_g2_n3_ricerca_per_erogazione_futura_non_trova_ricette_mai_erogate(server, p12):
    """N3 (= residuo bug 7): prescrizione registrata, stato 1, ricerca con soli limiti di erogazione
    al 01/01/2099. Prima: 1 risultato."""
    s = _servizio(server, p12)
    e = s.invia(_ricetta_farm())
    assert e.registrato is True and server.stato.prescrizioni[e.nre].stato_sist == "1"
    richiesta = xml_sist.richiesta_ricerca(s.canale.dati_chiamata(), CriteriNreUtilizzati(
        "160", dal=_dt.datetime(2099, 1, 1), al=_dt.datetime(2099, 1, 1)), CODICI_REGIONALI[TITOLARE])
    for nome in ("dataEmissioneDal", "dataEmissioneAl"):
        el = richiesta.find(f"{{{xml_sist.NS}}}{nome}")
        el.tag = f"{{{xml_sist.NS}}}" + nome.replace("Emissione", "Erogazione")
    # l'ordine dello schema: le date di erogazione vengono dopo quelle di emissione, qui assenti
    risposta = s.canale.chiama("getPrescrizioniIdentificate", richiesta)[0]
    esito = xml_sist.leggi_ricerca(risposta)
    assert esito.ricette == ()


@pytest.mark.schemi_hl7
def test_g2_n3_gruppo_di_controllo_erogazione_nel_periodo(server, p12):
    """Controllo: una prescrizione con data di erogazione nel periodo si trova; senza erogazione no."""
    s = _servizio(server, p12)
    e = s.invia(_ricetta_farm())
    server.stato.prescrizioni[e.nre].data_erogazione = _dt.date(2099, 1, 1)
    richiesta = xml_sist.richiesta_ricerca(s.canale.dati_chiamata(), CriteriNreUtilizzati(
        "160", dal=_dt.datetime(2099, 1, 1), al=_dt.datetime(2099, 1, 1)), CODICI_REGIONALI[TITOLARE])
    for nome in ("dataEmissioneDal", "dataEmissioneAl"):
        el = richiesta.find(f"{{{xml_sist.NS}}}{nome}")
        el.tag = f"{{{xml_sist.NS}}}" + nome.replace("Emissione", "Erogazione")
    esito = xml_sist.leggi_ricerca(s.canale.chiama("getPrescrizioniIdentificate", richiesta)[0])
    assert [r.nre for r in esito.ricette] == [e.nre]
