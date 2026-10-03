# SPDX-License-Identifier: EUPL-1.2
"""Controesempi del giro 2 di revisione esterna (02/10/2026), area 1: SAC, guardia, registro.

Rapporto: kit-mmg-review/2026-10-02-giro2/1-sac-guardia-registro.md. Ogni test riproduce il
controesempio del revisore con gli stessi valori: prima della correzione falliva. Nessuna rete: le
connessioni si fermano prima del socket, le scritture del registratore si catturano in memoria.
Dati sintetici (RSSMRA80A01H501U non è una persona).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import dataclasses
import datetime as dt
import email.message
import io
import json
import urllib.request
import urllib.response
import xml.etree.ElementTree as ET
from importlib import resources
from pathlib import Path
from unittest.mock import patch

import pytest

from varco import AmbienteBloccato, Credenziali
from varco.ricetta import xml_sac
from varco.ricetta.modello import Assistito, Prescrittore, Ricetta, Riga, TipoPrescrizione
from varco.trasporto import CanaleSAC, RegistratoreFile, Richiesta, Risposta
from varco.trasporto.http import consegna
from varco.trasporto import registro as modulo_registro
from varco.trasporto.registro import TAG_REDATTI, Redattore
from varco.trasporto.soap import imbusta

SEGRETO = "segreto-sintetico"
CF = "RSSMRA80A01H501U"
NOME = "Mario Sintetico"


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


def _registratore(registro, in_chiaro: bool):
    if in_chiaro:
        with pytest.warns(RuntimeWarning):
            return registro(registra_dati_personali_in_chiaro=True)
    return registro()


# ====================================================================== residuo bug 4


def test_g2_bug4_messaggio_libero_redatto(registro):
    """Giro 2, residuo bug 4: il registratore predefinito conservava
    `<messaggio>Mario Sintetico - diagnosi riservata</messaggio>`."""
    r, scritti = registro()
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"),
      Risposta(200, b"<x><messaggio>Mario Sintetico - diagnosi riservata</messaggio></x>", {}, 0.0), None)
    corpo = scritti["risposta.xml"].decode()
    assert NOME not in corpo and "riservata" not in corpo
    assert "[REDATTO:testo_libero:" in corpo


def test_g2_bug4_comunicazione_sac_redatta_codice_leggibile(registro):
    """Giro 2, residuo bug 4: dentro la vera `Comunicazione` del SAC il messaggio si toglie, il codice resta."""
    ns = xml_sac.NS_TIPI
    corpo = (f'<r xmlns:t="{ns}"><t:Comunicazione><t:codice>9001</t:codice>'
             f"<t:messaggio>{NOME} - diagnosi riservata</t:messaggio></t:Comunicazione></r>").encode()
    r, scritti = registro()
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), Risposta(200, corpo, {}, 0.0), None)
    testo = scritti["risposta.xml"].decode()
    assert NOME not in testo and "9001" in testo


# ====================================================================== N1: redirect


class Arresto(Exception):
    pass


class _Finto302(urllib.request.BaseHandler):
    """Handler locale del revisore: il SAC di test risponde 302 verso la produzione."""

    handler_order = 100  # prima di HTTPSHandler

    def https_open(self, req):
        if req.host != "demservicetest.sanita.finanze.it":
            return None  # gli altri host proseguono verso HTTPSHandler (e l'arresto)
        intestazioni = email.message.Message()
        intestazioni["Location"] = "https://demservice.sanita.finanze.it/x"
        risposta = urllib.response.addinfourl(io.BytesIO(b""), intestazioni, req.full_url, 302)
        risposta.msg = "Found"
        return risposta


class AdattatoreUrllib:
    """Trasporto proprio di un integratore: un normale opener urllib, che segue i redirect."""

    def __init__(self, ingoia_errori: bool = False, in_un_thread: bool = False):
        with patch("urllib.request.getproxies", return_value={}):
            self.opener = urllib.request.build_opener(_Finto302())
        self.ingoia_errori = ingoia_errori
        self.in_un_thread = in_un_thread

    def _apri(self, richiesta: Richiesta) -> Risposta:
        req = urllib.request.Request(richiesta.url, data=richiesta.corpo, headers=richiesta.intestazioni,
                                     method=richiesta.metodo)
        with self.opener.open(req, timeout=5) as r:
            return Risposta(r.status, r.read(), dict(r.headers.items()), 0.0)

    def invia(self, richiesta: Richiesta) -> Risposta:
        try:
            if self.in_un_thread:
                with concurrent.futures.ThreadPoolExecutor(1) as ex:
                    return ex.submit(self._apri, richiesta).result()
            return self._apri(richiesta)
        except Arresto:
            raise
        except Exception:  # noqa: BLE001 - un adattatore che nasconde gli errori
            if self.ingoia_errori:
                return Risposta(599, b"", {}, 0.0)
            raise


@contextlib.contextmanager
def _arresto_prima_del_socket():
    raggiunti: list[tuple[str, bool]] = []

    def ferma(conn):
        # il revisore: «host raggiunto prima del socket», con l'Authorization nella richiesta
        raggiunti.append((conn.host, True))
        raise Arresto(conn.host)

    with patch("http.client.HTTPSConnection.connect", ferma), patch("http.client.HTTPConnection.connect", ferma):
        yield raggiunti


@pytest.mark.parametrize("variante", ["semplice", "ingoia_errori", "in_un_thread"])
def test_g2_n1_redirect_del_trasporto_custom_non_raggiunge_la_produzione(variante):
    """Giro 2, N1: un adattatore urllib seguiva il 302 verso demservice.sanita.finanze.it con
    Authorization («SOCKET NON APERTO; host raggiunto prima del socket= demservice...»)."""
    adattatore = AdattatoreUrllib(ingoia_errori=variante == "ingoia_errori", in_un_thread=variante == "in_un_thread")
    canale = CanaleSAC(Credenziali("utente-sintetico", "password-sintetica", "1234"),
                       base_url="https://demservicetest.sanita.finanze.it", trasporto=adattatore)
    with _arresto_prima_del_socket() as raggiunti:
        with pytest.raises(AmbienteBloccato):
            canale.chiama("sac.prova", "/x", "azione", ET.Element("x"))
    assert raggiunti == [], f"connessione verso la produzione tentata: {raggiunti}"


def test_g2_n1_redirect_verso_un_altro_host_di_test_resta_possibile():
    """Il blocco riguarda gli host vietati, non ogni rete: un salto verso un host di test arriva al connect."""

    class VersoTest(_Finto302):
        def https_open(self, req):
            if req.host != "demservicetest.sanita.finanze.it" or req.selector != "/x":
                return None
            m = email.message.Message()
            m["Location"] = "https://demservicetest.sanita.finanze.it/y"
            risposta = urllib.response.addinfourl(io.BytesIO(b""), m, req.full_url, 302)
            risposta.msg = "Found"
            return risposta

    adattatore = AdattatoreUrllib()
    with patch("urllib.request.getproxies", return_value={}):
        adattatore.opener = urllib.request.build_opener(VersoTest())
    with _arresto_prima_del_socket() as raggiunti:
        with pytest.raises(Arresto):
            consegna(adattatore, Richiesta("x", "https://demservicetest.sanita.finanze.it/x", b"<x/>"))
    assert [h for h, _ in raggiunti] == ["demservicetest.sanita.finanze.it"]


def test_g2_n1_url_finale_dichiarato_dal_trasporto_rivalutato():
    """Contratto: un trasporto che segue redirect riporta `Risposta.url_finale`; la guardia lo rivaluta."""

    class Dichiara:
        def invia(self, richiesta):
            return Risposta(200, b"<x/>", {}, 0.0, url_finale="https://demservice.sanita.finanze.it/x")

    with pytest.raises(AmbienteBloccato):
        consegna(Dichiara(), Richiesta("x", "https://demservicetest.sanita.finanze.it/x", b""))


def test_g2_n1_la_guardia_vale_anche_per_socket_e_dns_diretti():
    """Un trasporto che non usa urllib (socket, requests, httpx passano da getaddrinfo) è fermato lo stesso."""
    import socket

    class Grezzo:
        def invia(self, richiesta):
            socket.getaddrinfo("demservice.sanita.finanze.it", 443)
            return Risposta(200, b"", {}, 0.0)

    with _arresto_del_dns():
        with pytest.raises(AmbienteBloccato):
            consegna(Grezzo(), Richiesta("x", "https://demservicetest.sanita.finanze.it/x", b""))


_DNS_ARRESTO = {"attivo": False}


def _hook_arresto_dns(evento, args):
    # rete di sicurezza del test: se la guardia del kit non ferma la risoluzione, la ferma questo
    # (viene DOPO quello del kit: un hook che solleva interrompe i successivi)
    if _DNS_ARRESTO["attivo"] and evento == "socket.getaddrinfo" and "finanze" in str(args[0]):
        raise Arresto(f"DNS raggiunto: {args[0]}")


@contextlib.contextmanager
def _arresto_del_dns():
    import sys
    from varco.trasporto import http as modulo_http

    if hasattr(modulo_http, "_installa_hook"):
        modulo_http._installa_hook()  # prima quello del kit
    if not getattr(_hook_arresto_dns, "installato", False):
        sys.addaudithook(_hook_arresto_dns)
        _hook_arresto_dns.installato = True
    _DNS_ARRESTO["attivo"] = True
    try:
        yield
    finally:
        _DNS_ARRESTO["attivo"] = False


# ====================================================================== N2: XML illeggibile


def _senza_nul(b: bytes) -> str:
    return b.decode("utf-8", "replace").replace(chr(0), "")


@pytest.mark.parametrize("chiaro", [False, True])
def test_g2_n2_utf16_con_bom_non_scritto(chiaro):
    """Giro 2, N2 scenario B: «UTF16 False True True» / «UTF16 True True True» (password e CF recuperabili
    togliendo i NUL)."""
    r = Redattore()
    b = f"<x><password>{SEGRETO}</password><cfAssistito>{CF}</cfAssistito></x>".encode("utf-16")
    out = _senza_nul(r.corpo(b, redigi=not chiaro))
    assert SEGRETO not in out and CF not in out
    assert out.startswith("[NON SCRITTO:") and f"{len(b)} byte" in out


@pytest.mark.parametrize("chiaro", [False, True])
def test_g2_n2_xml_troncato_non_scritto(chiaro):
    """Giro 2, N2 scenario A: «TRONCATO <Envelope><Body><password>segreto-sintetico</password>
    <cognNome>Mario Sintetico</cognNome></Body>»."""
    b = (b"<Envelope><Body><password>segreto-sintetico</password>"
         b"<cognNome>Mario Sintetico</cognNome></Body>")
    out = Redattore().corpo(b, redigi=not chiaro).decode()
    assert SEGRETO not in out and NOME not in out
    assert out.startswith("[NON SCRITTO:")


@pytest.mark.parametrize("chiaro", [False, True])
@pytest.mark.parametrize("corpo", [
    f"<x><password>{SEGRETO}</password><cfAssistito>{CF}</cfAssistito></x>".encode("utf-16"),
    b"<Envelope><Body><password>segreto-sintetico</password><cognNome>Mario Sintetico</cognNome></Body>",
], ids=["utf16", "troncato"])
def test_g2_n2_attraverso_il_registratore(registro, chiaro, corpo):
    """Giro 2, N2: lo stesso attraverso RegistratoreFile (scritture intercettate), redatto e in chiaro."""
    r, scritti = _registratore(registro, chiaro)
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), Risposta(500, corpo, {}, 0.0), None)
    out = _senza_nul(scritti["risposta.xml"])
    assert SEGRETO not in out and CF not in out and NOME not in out


def test_g2_n2_comando_minimale_del_rapporto():
    """Giro 2, il comando minimale del rapporto (N2, N4, N5) con le sue quattro stampe."""
    r = Redattore()
    for chiaro in (False, True):
        b = "<x><password>segreto-sintetico</password><cfAssistito>RSSMRA80A01H501U</cfAssistito></x>".encode("utf-16")
        out = r.corpo(b, redigi=not chiaro).decode("utf-8", "replace").replace(chr(0), "")
        assert ("segreto-sintetico" in out, "RSSMRA80A01H501U" in out) == (False, False), ("UTF16", chiaro)
    b = b"<Envelope><Body><password>segreto-sintetico</password><cognNome>Mario Sintetico</cognNome></Body>"
    assert "segreto-sintetico" not in r.corpo(b).decode() and "Mario" not in r.corpo(b).decode()
    attr = r.corpo(b"<x><birthTime value=\"19800101\"/><telecom value=\"tel:+390612345678\"/>"
                   b"<password value=\"segreto-sintetico\"/></x>").decode()
    for v in ("19800101", "390612345678", "segreto-sintetico"):
        assert v not in attr, v
    assert "segreto-sintetico" not in r.breve("password=***segreto-sintetico")


def test_g2_n2_testo_semplice_e_xml_ben_formato_restano_scritti():
    """Il segnaposto vale per ciò che non si legge: un XML valido e un testo semplice si scrivono (redatti)."""
    r = Redattore()
    assert r.corpo(b"<x><codEsito>0000</codEsito></x>") == b"<x><codEsito>0000</codEsito></x>"
    assert r.corpo(b"Service Unavailable") == b"Service Unavailable"
    assert r.corpo(b"<x/>", redigi=False) == b"<x/>"


# ====================================================================== N3: campi del modello


def _ricetta(riga: Riga) -> Ricetta:
    pr = Prescrittore("PROVAX00X00X000Y", "130", "201", "F")
    ass = Assistito(codice_fiscale="PNIMRA70A01H501P", provincia="AQ", asl="201")
    return Ricetta(pr, ass, TipoPrescrizione.FARMACEUTICA, [riga], data_compilazione=dt.datetime(2026, 9, 30, 16, 5, 7))


def test_g2_n3_descr_gruppo_equival_redatta(registro):
    """Giro 2, N3: «NUOVO CAMPO descrGruppoEquival; XSD ERRORI= [] / DESCRIZIONE CON NOME/DIAGNOSI SU DISCO= True»."""
    from varco.schemi import errori_xsd, lxml_disponibile

    ric = _ricetta(Riga(1, codice_gruppo_equivalenza="G3B",
                        descrizione_gruppo_equivalenza="Mario Sintetico - diagnosi riservata"))
    ric.valida()
    el = xml_sac.richiesta_invio(ric, "1234567890", lambda s: f"CIFRATO{len(s)}")
    if lxml_disponibile():
        assert errori_xsd(el) == []
    r, scritti = registro()
    r(Richiesta("sac.invioPrescritto", "https://localhost/x", imbusta(el)), None, None)
    assert b"Mario Sintetico" not in scritti["richiesta.xml"]
    assert b"riservata" not in scritti["richiesta.xml"]
    assert b"G3B" in scritti["richiesta.xml"]  # il codice del gruppo resta leggibile


def _elementi_xsd_sac() -> set[str]:
    xs = "{http://www.w3.org/2001/XMLSchema}"
    nomi: set[str] = set()
    for f in resources.files("varco.schemi").iterdir():
        if f.name.endswith(".xsd"):
            nomi |= {e.get("name") for e in ET.fromstring(f.read_bytes()).iter(xs + "element") if e.get("name")}
    return nomi


def test_g2_n3_ogni_elemento_degli_xsd_sac_e_classificato():
    """Giro 2, N3: ogni elemento degli XSD SAC è o leggibile (allowlist) o redatto. Un elemento nuovo
    negli schemi senza classificazione fa fallire il test; a runtime, nel dubbio, si redige."""
    TAG_SAC_LEGGIBILI = modulo_registro.TAG_SAC_LEGGIBILI
    redatti = set(TAG_REDATTI) | set(modulo_registro.TAG_SAC_REDATTI)
    nomi = _elementi_xsd_sac()
    assert len(nomi) > 90
    non_classificati = sorted(n for n in nomi if n not in TAG_SAC_LEGGIBILI and n not in redatti)
    assert non_classificati == []
    doppi = sorted(TAG_SAC_LEGGIBILI & redatti)
    assert doppi == []
    assert sorted(TAG_SAC_LEGGIBILI - nomi) == []  # niente nomi inventati nella allowlist


# I campi del modello che finiscono in un tag LEGGIBILE: codici, flag, quantità. Ogni altro campo
# testuale (nomi, descrizioni, note, testi) deve uscire redatto. Un campo nuovo del modello che
# arriva in chiaro su disco fa fallire il test finché non si decide da che parte sta.
_CAMPI_LEGGIBILI = {
    "Riga.codice", "Riga.codice_gruppo_equivalenza", "Riga.codice_motivazione_non_sost", "Riga.nota_aifa",
    "Riga.codice_catalogo", "Riga.tipo_accesso", "Riga.numero_nota", "Riga.condizione_erogabilita",
    "Riga.appropriatezza",
    "Prescrittore.codice_regione", "Prescrittore.codice_asl", "Prescrittore.codice_specializzazione",
    "Prescrittore.codice_struttura",
    "Assistito.provincia", "Assistito.asl", "Assistito.tipo_ricetta",
    "Ricetta.indicazione",
}


def test_g2_n3_ogni_campo_testuale_del_modello_e_classificato():
    """Giro 2, N3: ogni campo `str` di Riga, Assistito, Prescrittore e Ricetta, riempito con un marcatore
    e serializzato nel tracciato SAC, esce redatto salvo i campi elencati come leggibili."""
    def marcati(cls, prefisso, **fissi):
        valori = dict(fissi)
        for f in dataclasses.fields(cls):
            if f.name not in valori and "str" in str(f.type) and "Enum" not in str(f.type):
                valori[f.name] = f"QQ{prefisso}.{f.name}QQ"
        return cls(**valori)

    riga = marcati(Riga, "Riga", quantita=1)
    pr = marcati(Prescrittore, "Prescrittore")
    ass = marcati(Assistito, "Assistito", codice_regione=None)
    ric = marcati(Ricetta, "Ricetta", prescrittore=pr, assistito=ass, tipo=TipoPrescrizione.FARMACEUTICA,
                  righe=(riga,), classe_priorita=None, data_compilazione=dt.datetime(2026, 9, 30, 16, 5, 7))
    el = xml_sac.richiesta_invio(ric, "1234567890", lambda s: f"CIFRATO{len(s)}")
    serializzato = ET.tostring(el, encoding="unicode")
    redatto = Redattore().corpo(imbusta(el)).decode()
    import re
    in_xml = set(re.findall(r"QQ(\w+\.\w+)QQ", serializzato))
    sopravvissuti = set(re.findall(r"QQ(\w+\.\w+)QQ", redatto))
    assert len(in_xml) > 30  # il marcatore arriva davvero nel tracciato
    assert sorted(sopravvissuti - _CAMPI_LEGGIBILI) == []
    assert "QQRiga.descrizione_gruppo_equivalenzaQQ" not in redatto


def test_g2_n3_tag_sac_sconosciuto_redatto_nel_dubbio():
    """A runtime: un tag nel namespace SAC che non è nella allowlist si redige."""
    ns = xml_sac.NS_TIPI
    out = Redattore().corpo(f'<r xmlns:t="{ns}"><t:campoNuovo>{NOME}</t:campoNuovo>'
                            f"<t:codEsito>0000</t:codEsito></r>".encode()).decode()
    assert NOME not in out and "0000" in out


# ====================================================================== N4: forma del valore


def test_g2_n4_credenziale_che_inizia_con_asterischi_breve():
    """Giro 2, N4: «ERRORE password=***segreto-sintetico» da Redattore.breve."""
    out = Redattore().breve("password=***segreto-sintetico")
    assert SEGRETO not in out and "***" not in out


@pytest.mark.parametrize("valore", ["***segreto-sintetico", "[REDATTO:segreto-sintetico]"])
@pytest.mark.parametrize("chiaro", [False, True])
def test_g2_n4_attraverso_il_registratore(registro, chiaro, valore):
    """Giro 2, N4: «RuntimeError('password=***segreto-sintetico')» nel JSON destinato al file."""
    r, scritti = _registratore(registro, chiaro)
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), None, RuntimeError(f"password={valore}"))
    meta = json.loads(scritti["meta.json"])
    assert SEGRETO not in meta["errore"], meta["errore"]


# ====================================================================== N5: attributi


@pytest.mark.parametrize("chiaro", [False, True])
def test_g2_n5_attributi_dei_tag_sensibili(registro, chiaro):
    """Giro 2, N5: «<x><birthTime value="19800101" /><telecom value="tel:+390612345678" />
    <password value="segreto-sintetico" /></x>»."""
    corpo = (b'<x><birthTime value="19800101"/><telecom value="tel:+390612345678"/>'
             b'<password value="segreto-sintetico"/></x>')
    out = Redattore().corpo(corpo, redigi=not chiaro).decode()
    assert SEGRETO not in out
    if not chiaro:
        assert "19800101" not in out and "390612345678" not in out
    r, scritti = _registratore(registro, chiaro)
    r(Richiesta("sac.prova", "https://localhost/x", corpo), None, None)
    scritto = scritti["richiesta.xml"].decode()
    assert SEGRETO not in scritto
    if not chiaro:
        assert "19800101" not in scritto and "390612345678" not in scritto


def test_g2_n5_attributi_dei_figli_di_un_tag_sensibile():
    """Sotto un tag personale anche gli attributi dei figli: `<name><given value=.../></name>`."""
    out = Redattore().corpo(b'<x><addr use="H"><city value="Roma Sintetica"/></addr></x>').decode()
    assert "Roma Sintetica" not in out
