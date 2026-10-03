# SPDX-License-Identifier: EUPL-1.2
"""Controesempi della revisione esterna del 02/10/2026 (guardia anti-produzione e registro).

Ogni test riproduce il controesempio del revisore così come l'ha eseguito: prima della correzione
falliva, dopo deve passare. Nessuna rete: le connessioni si fermano prima del socket, le scritture
del registratore si catturano in memoria. Dati sintetici (RSSMRA80A01H501U non è una persona).
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest

from varco import AmbienteBloccato, Credenziali
from varco.ambienti import e_produzione, verifica_url_consentito
from varco.ricetta import xml_sac
from varco.ricetta.modello import Assistito, Prescrittore, Ricetta, Riga, TipoPrescrizione
from varco.trasporto import (
    AdesioneFVG,
    AdesionePiemonte,
    ApplicativoFVG,
    CanaleFVG,
    CanalePiemonte,
    CanaleSAC,
    ClientOAuth2Piemonte,
    GestionalePiemonte,
    ModalitaFVG,
    ModalitaPiemonte,
    PostazioneFVG,
    RegistratoreFile,
    Richiesta,
    Risposta,
    ServizioFVG,
    ServizioPiemonte,
    TrasportoHTTP,
)
from varco.trasporto.soap import imbusta

SINTETICO = "RSSMRA80A01H501U"


# ====================================================================== guardia


class Arresto(Exception):
    pass


def _ferma(conn):
    raise Arresto(conn.host)


def _prova_trasporto_http(url: str, **flag):
    """Il comando del revisore: connessione fermata prima del socket, proxy spenti."""
    with patch("urllib.request.getproxies", return_value={}), \
         patch("http.client.HTTPSConnection.connect", _ferma), \
         patch("http.client.HTTPConnection.connect", _ferma):
        TrasportoHTTP(**flag).invia(Richiesta("prova", url, b"<x/>"))


# Controesempio 1 (report 1): dominio percent-encoded. Prima: «GUARDIA SUPERATA; host prima del socket: ...»
@pytest.mark.parametrize("url", [
    "https://demservice.sanita.finanze.%69t/x",
    "https://modipa.fse.salute.gov.%69t/x",
    "https://pdd-virtasl.rmmg.rsr.rupar.puglia.%69t/x",
    "https://dem.sanita.fvg.%69t/x",
    "https://servizio.csi.%69t/x",
    # varianti della stessa idea: punto codificato, doppia codifica, lettere a larghezza piena (IDNA)
    "https://demservice%2esanita%2efinanze%2eit/x",
    "https://demservice.sanita.finanze.%2569t/x",
    "https://demservice.sanita.finanze.ｉｔ/x",
    "https://DEMSERVICE.sanita.finanze.%49%54./x",
])
def test_percent_encoding_non_aggira_la_guardia(url):
    assert e_produzione(url)
    with pytest.raises(AmbienteBloccato):
        _prova_trasporto_http(url)


@pytest.mark.parametrize("url", [
    "https://pddasl-preprod.sanita.regione.rsr.rupar.puglia.%69t/x",  # collaudo SIST codificato
    "https://demtest.sanita.fvg.%69t/x",  # collaudo FVG codificato
])
def test_collaudi_regionali_codificati_vogliono_il_flag(url):
    with pytest.raises(AmbienteBloccato):
        _prova_trasporto_http(url)


def test_host_normalizzato_come_lo_vede_urllib():
    from varco.ambienti import host_normalizzati

    assert "demservice.sanita.finanze.it" in host_normalizzati("https://demservice.sanita.finanze.%69t/x")
    assert "demservice.sanita.finanze.it" in host_normalizzati("https://u:p@DemService.Sanita.Finanze.IT.:443/x")


# Controesempio 1, coda: «La connessione a un IP letterale 192.0.2.1 raggiunge ugualmente il punto intercettato»
@pytest.mark.parametrize("url", [
    "https://192.0.2.1/x",
    "https://[2001:db8::1]/x",
    "https://3221225985/x",  # 192.0.2.1 in decimale: getaddrinfo lo accetta
    "https://0xc0.0.2.1/x",
    "https://[::ffff:192.0.2.1]/x",
    "https://0.0.0.0/x",
])
def test_ip_letterali_bloccati_salvo_flag(url):
    assert e_produzione(url)
    with pytest.raises(AmbienteBloccato):
        _prova_trasporto_http(url)
    # con il flag esplicito la guardia lascia passare e la connessione arriva al punto intercettato
    with pytest.raises(Arresto):
        _prova_trasporto_http(url, consenti_produzione=True)


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8443/x", "https://127.1/x", "https://[::1]:8443/x", "https://localhost/x",
    "https://[::ffff:127.0.0.1]/x",
])
def test_loopback_resta_consentito(url):
    assert not e_produzione(url)
    verifica_url_consentito(url)


class Finto:
    """Trasporto proprio di un integratore: registra la richiesta, non apre socket."""

    def __init__(self, risposta: bytes = b""):
        self.richieste: list[Richiesta] = []
        self.risposta = risposta

    def invia(self, richiesta: Richiesta) -> Risposta:
        self.richieste.append(richiesta)
        return Risposta(200, self.risposta, {}, 0.0)


# Controesempio 2 (report 1). Prima: «CUSTOM INVOCATO https://demservice.sanita.finanze.it/x Authorization presente= True»
def test_trasporto_custom_non_aggira_la_guardia_del_sac():
    finto = Finto(imbusta(ET.Element("ok")))
    canale = CanaleSAC(
        Credenziali("utente-sintetico", "password-sintetica", "1234"),
        base_url="https://demservice.sanita.finanze.it",
        trasporto=finto,
    )
    with pytest.raises(AmbienteBloccato):
        canale.chiama("sac.prova", "/x", "azione", ET.Element("x"))
    assert finto.richieste == []


def test_trasporto_custom_col_flag_esplicito_passa():
    """Il flag resta l'unico modo: un trasporto proprio lo dichiara come TrasportoHTTP."""
    finto = Finto(imbusta(ET.Element("ok")))
    finto.consenti_produzione = True
    canale = CanaleSAC(Credenziali("u", "p", "1234"), base_url="https://demservice.sanita.finanze.it", trasporto=finto)
    canale.chiama("sac.prova", "/x", "azione", ET.Element("x"))
    assert len(finto.richieste) == 1
    finto.consenti_produzione = "si"  # solo True vale
    with pytest.raises(AmbienteBloccato):
        canale.chiama("sac.prova", "/x", "azione", ET.Element("x"))


def test_consegna_normalizza_anche_per_un_trasporto_custom():
    from varco.trasporto.http import consegna

    finto = Finto()
    with pytest.raises(AmbienteBloccato):
        consegna(finto, Richiesta("x", "https://demservice.sanita.finanze.%69t/x", b""))
    assert finto.richieste == []


# Report 4, punto 1. Prima: «INOLTRATA_SENZA_FLAG https://dem.sanita.fvg.it/SARWs/annullaPrescrittoSecure / ok= True»
def test_trasporto_custom_non_aggira_la_guardia_fvg():
    class _Token:
        def access_token(self):
            return "access-sintetico"

        def id_token(self):
            return "id-sintetico"

    titolare = "PROVAX00X00X000Y"
    applicativo = ApplicativoFVG("VARCO-PROVA", "0.1", "1.4.4")
    finto = Finto()
    c = CanaleFVG(titolare, PostazioneFVG("MACOS", "15.0", titolare, "LICENZA-0001"),
                  adesione=AdesioneFVG("PROVA", applicativo), modalita=ModalitaFVG.FEDERATA, token=_Token(),
                  trasporto=finto, url={ServizioFVG.ANNULLA: "https://dem.sanita.fvg.it/SARWs/annullaPrescrittoSecure"})
    with pytest.raises(AmbienteBloccato):
        c.chiama(ServizioFVG.ANNULLA, ET.Element("x"))
    assert finto.richieste == []


# Report 5, punto 1. Prima: «B1 invio trasporto_raggiunto= 1 flag_presenti= False» (e lo stesso per id_sessione)
@pytest.mark.parametrize("servizio", [ServizioPiemonte.INVIO, ServizioPiemonte.ID_SESSIONE])
def test_trasporto_custom_non_aggira_la_guardia_piemonte(servizio):
    gestionale = GestionalePiemonte("VARCO", "301")
    spia = Finto()
    c = CanalePiemonte(
        ModalitaPiemonte.MAIL,
        credenziali=Credenziali("rupar-sint", "password-sintetica", "1234567890", "PROVAX00X00X000Y"),
        id_sessione=lambda: "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
        adesione=AdesionePiemonte("R", gestionale),
        url={servizio: "https://produzione.csi.it/soap"},
        trasporto=spia,
    )
    with pytest.raises(AmbienteBloccato):
        c.chiama(servizio, ET.Element("x"), "azione")
    assert spia.richieste == []


def test_client_oauth2_passa_dallo_stesso_punto_di_consegna():
    spia = Finto(b"{}")
    gestionale = GestionalePiemonte("VARCO", "301")
    client = ClientOAuth2Piemonte("https://servizio.csi.%69t/reloauthserver", gestionale, "http://localhost:8081/cb",
                                  trasporto=spia, adesione=AdesionePiemonte("R", gestionale))
    with pytest.raises(AmbienteBloccato):
        client._chiama("token", "POST", "https://servizio.csi.%69t/reloauthserver/oauth2/token")
    assert spia.richieste == []


# ====================================================================== registro


@pytest.fixture
def registro():
    """RegistratoreFile con le scritture catturate in memoria (come il comando del revisore)."""
    scritti: dict[str, bytes] = {}

    def cattura(self, p, b):
        scritti[p.name.rsplit("_", 1)[-1]] = b
        return p

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(Path, "mkdir"))
        stack.enter_context(patch.object(Path, "glob", lambda self, pattern: iter(())))
        stack.enter_context(patch.object(RegistratoreFile, "_scrivi", cattura))
        yield lambda **kw: (RegistratoreFile("/NON_SCRIVERE", **kw), scritti)


# Controesempio 3 (report 1). Prima: url con password=segreto-sintetico, X-Api-Key in chiaro, errore con password=
@pytest.mark.parametrize("in_chiaro", [False, True])
def test_credenziali_in_url_header_generici_ed_errori(registro, in_chiaro):
    kw = {"registra_dati_personali_in_chiaro": True} if in_chiaro else {}
    with pytest.warns(RuntimeWarning) if in_chiaro else contextlib.nullcontext():
        r, scritti = registro(**kw)
    r(Richiesta("sac.prova", "https://localhost/x?password=segreto-sintetico&pincode=1234567890&ok=1",
                b"<x><prescrizione1>Mario Sintetico - diagnosi riservata</prescrizione1></x>",
                {"X-Api-Key": "chiave-sintetica", "X-Custom": "Bearer bearer-sintetico", "Content-Type": "text/xml"}),
      None, RuntimeError("password=segreto-sintetico token: token-sintetico Basic YmFzaWMtc2ludGV0aWNv"))
    tutto = b"".join(scritti.values()).decode()
    for segreto in ("segreto-sintetico", "chiave-sintetica", "bearer-sintetico", "1234567890", "token-sintetico",
                    "YmFzaWMtc2ludGV0aWNv"):
        assert segreto not in tutto, segreto
    meta = json.loads(scritti["meta.json"])
    assert meta["intestazioni_richiesta"]["X-Api-Key"] == "***"
    assert meta["intestazioni_richiesta"]["Content-Type"] == "text/xml"
    assert "ok=1" in meta["url"]  # il resto dell'URL resta leggibile


# Controesempio 4, primo scenario. Prima: «<x><prescrizione1>Mario Sintetico - diagnosi riservata</prescrizione1></x>»
def test_prescrizione1_redatta(registro):
    r, scritti = registro()
    r(Richiesta("sac.prova", "https://localhost/x",
                b"<x><prescrizione1>Mario Sintetico - diagnosi riservata</prescrizione1>"
                b"<prescrizione2>Altro testo riservato</prescrizione2></x>"), None, None)
    corpo = scritti["richiesta.xml"].decode()
    assert "Mario Sintetico" not in corpo and "riservat" not in corpo
    assert "[REDATTO:testo_libero:" in corpo


def test_prescrizione1_redatta_in_una_vera_ricetta(registro):
    """Il secondo comando del revisore: una Ricetta vera, valida sugli XSD, passata al registratore."""
    from varco.schemi import errori_xsd

    pr = Prescrittore("PROVAX00X00X000Y", "130", "201", "F")
    ass = Assistito(codice_fiscale="PNIMRA70A01H501P", provincia="AQ", asl="201")
    riga = Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="LEVETIRACETAM",
                prescrizione1="Mario Sintetico - diagnosi riservata")
    ric = Ricetta(pr, ass, TipoPrescrizione.FARMACEUTICA, [riga], data_compilazione=dt.datetime(2026, 9, 30, 16, 5, 7))
    ric.valida()
    el = xml_sac.richiesta_invio(ric, "1234567890", lambda s: f"CIFRATO{len(s)}")
    try:
        assert errori_xsd(el) == []
    except RuntimeError:  # lxml assente: la validazione XSD è facoltativa
        pass
    r, scritti = registro()
    r(Richiesta("sac.invioPrescritto", "https://localhost/x", imbusta(el)), None, None)
    assert b"Mario Sintetico" not in scritti["richiesta.xml"]
    assert b"riservata" not in scritti["richiesta.xml"]


# Controesempio 4, secondo scenario. Prima: «CF RICOSTRUITO DAL LOG= CF RSSMRA80A01H501U»
@pytest.mark.parametrize("forma", ["decimale", "esadecimale", "mista"])
def test_entita_numeriche_decodificate_prima_della_redazione(registro, forma):
    if forma == "decimale":
        cf = "".join(f"&#{ord(c)};" for c in SINTETICO)
    elif forma == "esadecimale":
        cf = "".join(f"&#x{ord(c):x};" for c in SINTETICO)
    else:
        cf = "".join(c if i % 2 else f"&#{ord(c)};" for i, c in enumerate(SINTETICO))
    r, scritti = registro()
    corpo = f"<r><messaggio>CF {cf}</messaggio><a v='{cf}'/></r>".encode()
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), Risposta(200, corpo, {}, 0.0), None)
    riletto = ET.fromstring(scritti["risposta.xml"])
    testo = " ".join(riletto.itertext()) + " " + " ".join(v for e in riletto.iter() for v in e.attrib.values())
    assert SINTETICO not in testo.upper()
    assert SINTETICO not in scritti["risposta.xml"].decode()


def test_xml_annidato_come_testo_e_cdata(registro):
    r, scritti = registro()
    corpo = (f"<r><dati>&lt;cognNome&gt;Mario Sintetico&lt;/cognNome&gt;</dati>"
             f"<altroTesto><![CDATA[<prescrizione1>Diagnosi riservata</prescrizione1> {SINTETICO}]]></altroTesto></r>").encode()
    r(Richiesta("sac.prova", "https://localhost/x", corpo), None, None)
    out = scritti["richiesta.xml"].decode()
    assert "Mario Sintetico" not in out and "riservata" not in out and SINTETICO not in out


# Controesempio 5. Prima: «CORPO <x><password>password-corpo-sintetica</password><token>token-sintetico</token></x>»
def test_in_chiaro_le_credenziali_restano_mascherate(registro):
    with pytest.warns(RuntimeWarning):
        r, scritti = registro(registra_dati_personali_in_chiaro=True)
    r(Richiesta("sac.prova", "https://localhost/x",
                b"<x><password>password-corpo-sintetica</password><token>token-sintetico</token>"
                b"<pinCode>pin-sintetico</pinCode><idSessione>3f2504e0-4f89-41d3-9a0c-0305e82c3301</idSessione>"
                b"<cfAssistito>RSSMRA80A01H501U</cfAssistito></x>"),
      Risposta(200, b'{"access_token": "at-sintetico", "id_token": "it-sintetico", "esito": "ok"}', {}, 0.0), None)
    tutto = b"".join(scritti.values()).decode()
    for segreto in ("password-corpo-sintetica", "token-sintetico", "pin-sintetico", "3f2504e0-4f89", "at-sintetico",
                    "it-sintetico"):
        assert segreto not in tutto, segreto
    assert json.loads(scritti["meta.json"])["modalita"] == "IN_CHIARO_DATI_PERSONALI"
    assert "RSSMRA80A01H501U" in tutto  # i dati personali sì: è la modalità in chiaro
    assert '"esito": "ok"' in scritti["risposta.xml"].decode()


def test_in_chiaro_form_oauth2_mascherato(registro):
    with pytest.warns(RuntimeWarning):
        r, scritti = registro(registra_dati_personali_in_chiaro=True)
    r(Richiesta("piemonte.oauth2.token", "https://localhost/oauth2/token",
                b"grant_type=authorization_code&code=codice-sintetico&code_verifier=verifier-sintetico&client_secret=s3greto"),
      None, None)
    corpo = scritti["richiesta.xml"].decode()
    assert "grant_type=authorization_code" in corpo
    for segreto in ("codice-sintetico", "verifier-sintetico", "s3greto"):
        assert segreto not in corpo


# Campi a testo libero del modello e dei moduli regionali (ricerca sistematica)
@pytest.mark.parametrize("tag", [
    "prescrizione1", "prescrizione2",  # SAC/FVG/Piemonte: Riga.prescrizione1/2
    "descrProdPrest",  # Riga.descrizione: testo libero quando manca il codice
    "nota",  # SIST: Riga.note e Riga.note_prestazione
    "statoEstero",  # Assistito.stato_estero
    "residenza", "sesso",  # SIST, anagrafica
    "city", "postalCode", "county", "name", "text", "originalText",  # CDA
])
def test_campi_liberi_redatti(registro, tag):
    r, scritti = registro()
    r(Richiesta("x", "https://localhost/x", f"<x><ns:{tag} xmlns:ns='u'>Testo Sintetico Riservato</ns:{tag}></x>".encode()),
      None, None)
    assert b"Riservato" not in scritti["richiesta.xml"]


def test_dati_di_test_non_in_chiaro_con_cf_codificato_in_entita(registro):
    """Modalità identita_di_test: un CF reale scritto in entità numeriche non deve sfuggire al controllo."""
    medico = "PROVAX00X00X000Y"
    basic = "Basic " + base64.b64encode(f"{medico}:pw".encode()).decode()
    cf = "".join(f"&#{ord(c)};" for c in SINTETICO)
    r, scritti = registro(identita_di_test={medico})
    r(Richiesta("sac.prova", "https://demservicetest.sanita.finanze.it/x",
                f"<x><cfMedico1>{medico}</cfMedico1><messaggio>{cf}</messaggio></x>".encode(),
                {"Authorization": basic}), None, None)
    meta = json.loads(scritti["meta.json"])
    assert meta["modalita"] == "redatta" and "codici fiscali" in meta["in_chiaro_negato"]
    assert SINTETICO not in scritti["richiesta.xml"].decode()


def test_cf_percent_encoded_nella_query_redatto(registro):
    r, scritti = registro()
    cf = "".join(f"%{ord(c):02X}" for c in SINTETICO)
    r(Richiesta("x", f"https://localhost/assistiti/{cf}?cf={cf}", b""), None, None)
    meta = json.loads(scritti["meta.json"])
    assert SINTETICO not in meta["url"] and cf not in meta["url"]
