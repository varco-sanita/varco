# SPDX-License-Identifier: EUPL-1.2
"""Controesempi del giro 3 di revisione esterna (03/10/2026), area 1: SAC, guardia, registro.

Rapporto: kit-mmg-review/2026-10-02-giro3/1-sac-guardia-registro.md. Ogni test riproduce il
controesempio del revisore con gli stessi valori (prima della correzione falliva); i test «famiglia»
coprono le varianti dello stesso principio. Nessuna rete: un audit hook di sicurezza ferma ogni
connect che la guardia lasciasse passare; le scritture del registratore si catturano in memoria.
Dati sintetici.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from varco import AmbienteBloccato
from varco.trasporto import RegistratoreFile, Richiesta, Risposta
from varco.trasporto import http as modulo_http
from varco.trasporto.http import consegna
from varco.trasporto.registro import Redattore

SEGRETO = "segreto-sintetico"
NOME = "Mario Sintetico"
DIAGNOSI = "diagnosi riservata"
CF = "RSSMRA80A01H501U"
TEST = "https://demservicetest.sanita.finanze.it/x"


class ArrestoRevisore(Exception):
    """L'hook di sicurezza del test: se arriva qui, la guardia del kit NON ha fermato il connect."""


_ARRESTO = {"attivo": False}


def _hook_arresto(evento, args):
    if _ARRESTO["attivo"] and evento == "socket.connect":
        raise ArrestoRevisore(f"socket.connect {args[1]!r}")


@contextlib.contextmanager
def arresto_revisore():
    """Come il revisore: l'hook della libreria installato PRIMA dell'hook di arresto."""
    modulo_http._installa_hook()
    if not getattr(_hook_arresto, "installato", False):
        sys.addaudithook(_hook_arresto)
        _hook_arresto.installato = True
    _ARRESTO["attivo"] = True
    try:
        yield
    finally:
        _ARRESTO["attivo"] = False


class TrasportoConnect:
    """Trasporto custom che apre un socket verso un IP ottenuto per conto suo."""

    def __init__(self, ip: str, famiglia=socket.AF_INET):
        self.ip, self.famiglia = ip, famiglia

    def invia(self, richiesta):
        with socket.socket(self.famiglia) as s:
            s.connect((self.ip, 443))
        return Risposta(200, b"<x/>", {}, 0.0)


# ====================================================================== N1 scenario A: IP numerico


def test_g3_n1a_ip_custom_bloccato_dalla_guardia():
    """Giro 3, N1 A: «IP CUSTOM: ArrestoRevisore socket.connect ('192.0.2.1', 443)» invece di AmbienteBloccato."""
    with arresto_revisore():
        with pytest.raises(AmbienteBloccato):
            consegna(TrasportoConnect("192.0.2.1"), Richiesta("sac.prova", TEST, b"<x/>"))


@pytest.mark.parametrize("ip,famiglia", [
    ("10.0.0.1", socket.AF_INET),          # privato
    ("8.8.8.8", socket.AF_INET),           # pubblico
    ("2001:db8::1", socket.AF_INET6),      # IPv6
    ("::ffff:192.0.2.1", socket.AF_INET6), # IPv4 mappato in IPv6
])
def test_g3_n1a_famiglia_ip_mai_risolti(ip, famiglia):
    """Famiglia N1 A: qualunque IP non risolto davanti alla guardia vale come IP letterale (produzione)."""
    if famiglia == socket.AF_INET6 and not socket.has_ipv6:
        pytest.skip("IPv6 non disponibile")
    with arresto_revisore():
        with pytest.raises(AmbienteBloccato):
            consegna(TrasportoConnect(ip, famiglia), Richiesta("sac.prova", TEST, b"<x/>"))


def test_g3_n1a_ip_risolto_da_un_host_di_test_passa():
    """Gruppo di controllo: l'IP che getaddrinfo ha restituito per l'host di TEST arriva al connect."""

    def finto(host, port, *a, **k):
        assert host == "demservicetest.sanita.finanze.it"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", port))]

    class ConDNS:
        def invia(self, richiesta):
            ip = socket.getaddrinfo("demservicetest.sanita.finanze.it", 443)[0][4][0]
            with socket.socket() as s:
                s.connect((ip, 443))
            return Risposta(200, b"", {}, 0.0)

    with arresto_revisore(), patch.object(modulo_http, "_getaddrinfo_originale", finto):
        with pytest.raises(ArrestoRevisore):
            consegna(ConDNS(), Richiesta("sac.prova", TEST, b"<x/>"))


@pytest.mark.parametrize("risolto,connesso,famiglia", [
    ("203.0.113.7", "::ffff:203.0.113.7", socket.AF_INET6),
    ("::ffff:203.0.113.7", "203.0.113.7", socket.AF_INET),
])
def test_g4_n1_ip_risolto_da_test_vale_anche_in_forma_mappata(risolto, connesso, famiglia):
    """Verifica mirata giro 4 (r4), N1: l'IP risolto dall'host di TEST veniva bloccato se il connect usava
    l'altra forma (IPv4 <-> IPv4-mapped): stessa destinazione, due chiavi diverse nella cache."""
    if famiglia == socket.AF_INET6 and not socket.has_ipv6:
        pytest.skip("IPv6 non disponibile")

    def finto(host, port, *a, **k):
        fam = socket.AF_INET6 if ":" in risolto else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (risolto, port))]

    class ConDNS:
        def invia(self, richiesta):
            socket.getaddrinfo("demservicetest.sanita.finanze.it", 443)
            with socket.socket(famiglia) as s:
                s.connect((connesso, 443))
            return Risposta(200, b"", {}, 0.0)

    with patch.dict(modulo_http._ip_risolti, clear=True), arresto_revisore(), \
            patch.object(modulo_http, "_getaddrinfo_originale", finto):
        with pytest.raises(ArrestoRevisore):  # = la guardia ha lasciato passare il connect
            consegna(ConDNS(), Richiesta("sac.prova", TEST, b"<x/>"))


def test_g4_n1_forma_mappata_di_un_ip_di_produzione_resta_bloccata():
    """Controllo: unificare le chiavi non deve sbloccare la forma mappata di un IP risolto da PRODUZIONE."""

    def finto(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", port))]

    class ConDNS:
        def invia(self, richiesta):
            socket.getaddrinfo("demservice.sanita.finanze.it", 443)
            with socket.socket(socket.AF_INET6) as s:
                s.connect(("::ffff:203.0.113.9", 443))
            return Risposta(200, b"", {}, 0.0)

    if not socket.has_ipv6:
        pytest.skip("IPv6 non disponibile")
    with patch.dict(modulo_http._ip_risolti, clear=True), arresto_revisore(), \
            patch.object(modulo_http, "_getaddrinfo_originale", finto):
        with pytest.raises(AmbienteBloccato):
            consegna(ConDNS(), Richiesta("sac.prova", TEST, b"<x/>"))


def test_g3_n1a_ip_risolto_da_un_host_di_produzione_bloccato_senza_flag():
    """Famiglia N1 A: un IP che il socket ha risolto per la PRODUZIONE (una cache del trasporto) resta vietato
    senza `consenti_produzione`, anche se la chiamata parte verso il test."""

    def finto(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", port))]

    class Cache:
        consenti_produzione = False
        ip = None

        def invia(self, richiesta):
            with socket.socket() as s:
                s.connect((self.ip, 443))
            return Risposta(200, b"", {}, 0.0)

    with patch.object(modulo_http, "_getaddrinfo_originale", finto):
        modulo_http._installa_hook()
        # risoluzione fatta prima, fuori dalla chiamata (la cache del trasporto)
        Cache.ip = modulo_http._getaddrinfo("demservice.sanita.finanze.it", 443)[0][4][0]
    with arresto_revisore():
        with pytest.raises(AmbienteBloccato):
            consegna(Cache(), Richiesta("sac.prova", TEST, b"<x/>"))


def test_g3_n1a_loopback_resta_consentito():
    """Gruppo di controllo: 127.0.0.1 vale come localhost (server finti locali)."""
    with arresto_revisore():
        with pytest.raises(ArrestoRevisore):
            consegna(TrasportoConnect("127.0.0.1"), Richiesta("sac.prova", "https://localhost/x", b"<x/>"))


# ====================================================================== N1 scenario B: connessione riusata


class SocketCattura:
    def __init__(self):
        self.inviati: list[bytes] = []

    def sendall(self, b):
        self.inviati.append(bytes(b))

    def makefile(self, *a, **k):
        return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

    def close(self):
        pass


def _pool(classe, host, tunnel=None):
    conn = classe(host, 443) if tunnel is None else classe("127.0.0.1", 3128)
    if tunnel:
        conn.set_tunnel(tunnel, 443)
    sock = SocketCattura()
    conn.sock = sock  # connessione già aperta nel pool: nessun DNS, nessun connect
    return conn, sock


class TrasportoPool:
    def __init__(self, conn):
        self.conn = conn

    def invia(self, richiesta):
        self.conn.request("POST", "/x", body=b"ricetta-sintetica",
                          headers={"Authorization": "Basic c2ludGV0aWNvOnNlZ3JldG8="})
        self.conn.getresponse()
        return Risposta(200, b"", {}, 0.0, url_finale="https://demservice.sanita.finanze.it/x")


def test_g3_n1b_connessione_di_produzione_nel_pool_bloccata_prima_dei_byte():
    """Giro 3, N1 B: «POOL BYTES: POST /x … Authorization: Basic …» consegnati al socket prima del blocco."""
    conn, sock = _pool(http.client.HTTPConnection, "demservice.sanita.finanze.it")
    with pytest.raises(AmbienteBloccato):
        consegna(TrasportoPool(conn), Richiesta("sac.prova", TEST, b"<x/>"))
    assert sock.inviati == [], sock.inviati


@pytest.mark.parametrize("classe,host,tunnel", [
    (http.client.HTTPSConnection, "demservice.sanita.finanze.it", None),
    (http.client.HTTPConnection, None, "demservice.sanita.finanze.it"),   # tunnel CONNECT via proxy
    (http.client.HTTPConnection, "DEMSERVICE.sanita.finanze.it.", None),  # maiuscole e punto finale
    (http.client.HTTPConnection, "192.0.2.1", None),                      # IP letterale
], ids=["https", "tunnel", "maiuscole", "ip"])
def test_g3_n1b_famiglia_connessioni_riusate(classe, host, tunnel):
    """Famiglia N1 B: ogni scrittura http.client rivaluta host e tunnel, qualunque sia la forma."""
    conn, sock = _pool(classe, host, tunnel)
    if tunnel:
        conn._tunnel = lambda: None  # tunnel già stabilito
    with pytest.raises(AmbienteBloccato):
        consegna(TrasportoPool(conn), Richiesta("sac.prova", TEST, b"<x/>"))
    assert sock.inviati == []


def test_g3_n1b_connessione_di_test_nel_pool_scrive():
    """Gruppo di controllo: verso l'host di test i byte partono (la guardia non blocca tutto)."""
    conn, sock = _pool(http.client.HTTPConnection, "demservicetest.sanita.finanze.it")

    class Test(TrasportoPool):
        def invia(self, richiesta):
            super().invia(richiesta)
            return Risposta(200, b"", {}, 0.0)

    assert consegna(Test(conn), Richiesta("sac.prova", TEST, b"<x/>")).stato_http == 200
    assert any(b"ricetta-sintetica" in b for b in sock.inviati)


# ====================================================================== registro: fixture


@pytest.fixture
def registro():
    """RegistratoreFile con le scritture catturate in memoria (come il revisore: `_scrivi` intercettato)."""
    scritti: dict[str, bytes] = {}

    def cattura(self, p, b):
        scritti[p.name.rsplit("_", 1)[-1]] = b
        return p

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(Path, "mkdir"))
        stack.enter_context(patch.object(Path, "glob", lambda self, pattern: iter(())))
        stack.enter_context(patch.object(RegistratoreFile, "_scrivi", cattura))

        def crea(chiaro: bool = False):
            if chiaro:
                with pytest.warns(RuntimeWarning):
                    return RegistratoreFile("/NON_SCRIVERE", registra_dati_personali_in_chiaro=True), scritti
            return RegistratoreFile("/NON_SCRIVERE"), scritti

        yield crea


def _tutto(scritti: dict[str, bytes]) -> str:
    return "\n".join(b.decode("utf-8", "replace") for b in scritti.values())


# ====================================================================== N2: XML dentro l'eccezione


@pytest.mark.parametrize("chiaro", [False, True], ids=["predefinito", "in_chiaro"])
def test_g3_n2_password_xml_nell_eccezione(registro, chiaro):
    """Giro 3, N2: «RuntimeError('<Envelope><Body><password>segreto-sintetico</password>…')» nel campo errore."""
    r, scritti = registro(chiaro)
    errore = RuntimeError("<Envelope><Body><password>segreto-sintetico</password></Body></Envelope>")
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), None, errore)
    meta = json.loads(scritti["meta.json"])
    assert SEGRETO not in meta["errore"], meta["errore"]
    assert SEGRETO not in _tutto(scritti)


ERRORI_FAMIGLIA = [
    # XML con namespace e prefissi, entità, CDATA, attributi
    '<s:Envelope xmlns:s="urn:x"><s:Body><pinCode>segreto-sintetico</pinCode></s:Body></s:Envelope>',
    "Risposta: &lt;password&gt;segreto-sintetico&lt;/password&gt; fine",
    "<x><token><![CDATA[segreto-sintetico]]></token></x>",
    '<x><password value="segreto-sintetico"/></x>',
    # XML dentro testo, più frammenti
    "prima <a>ok</a> poi <password>segreto-sintetico</password> dopo",
    # XML che il parser non legge: nel dubbio non si scrive
    "<Envelope><password>segreto-sintetico</password>",
    "<s:Envelope><password>segreto-sintetico</password></s:Envelope>",
    "<password >segreto-sintetico",
    # JSON annidato dentro l'eccezione
    '{"password":{"value":"segreto-sintetico"}}',
    'errore 401: {"error":{"credenziali":[{"password":"segreto-sintetico"}]}}',
    '{"password": "segreto-sintetico", ',
    # JSON dentro XML dentro l'eccezione
    '<x><dettaglio>{"token":{"v":"segreto-sintetico"}}</dettaglio></x>',
]


@pytest.mark.parametrize("chiaro", [False, True], ids=["predefinito", "in_chiaro"])
@pytest.mark.parametrize("testo", ERRORI_FAMIGLIA)
def test_g3_n2_famiglia_strutture_nell_eccezione_e_negli_header(registro, chiaro, testo):
    """Famiglia N2: qualunque struttura (XML, entità, CDATA, attributi, JSON, JSON in XML, illeggibile)
    dentro un errore o un header: la credenziale non arriva su disco in nessuna modalità."""
    r, scritti = registro(chiaro)
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>", {"X-Dettaglio": testo}),
      Risposta(500, b"<x/>", {"X-Errore": testo}, 0.0), RuntimeError(testo))
    assert SEGRETO not in _tutto(scritti), _tutto(scritti)


def test_g3_n2_messaggi_comuni_restano_leggibili(registro):
    """Gruppo di controllo: un errore di rete comune («<urlopen error …>») resta leggibile nel meta."""
    from varco.errori import ErroreTrasporto

    r, scritti = registro()
    e = ErroreTrasporto("Errore di rete verso localhost: <urlopen error [Errno 61] Connection refused>")
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), None, e)
    assert "Connection refused" in json.loads(scritti["meta.json"])["errore"]


# ====================================================================== N3: SOAP Fault


FAULT = (b'<?xml version="1.0" encoding="UTF-8"?><env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">'
         b"<env:Body><env:Fault><faultcode>env:Server</faultcode>"
         b"<faultstring>Mario Sintetico - diagnosi riservata</faultstring></env:Fault></env:Body></env:Envelope>")


def test_g3_n3_faultstring_nel_file_risposta(registro):
    """Giro 3, N3: il file risposta conteneva «Mario Sintetico - diagnosi riservata» integrale."""
    r, scritti = registro()
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), Risposta(500, FAULT, {}, 0.0), None)
    testo = scritti["risposta.xml"].decode()
    assert NOME not in testo and DIAGNOSI not in testo
    assert "env:Server" in testo  # il codice resta leggibile


def test_g3_n3_faultstring_corpo_minimo():
    """Giro 3, N3: `r.corpo(b"<Fault><faultstring>Mario Sintetico - diagnosi riservata</faultstring></Fault>")`."""
    out = Redattore().corpo(b"<Fault><faultstring>Mario Sintetico - diagnosi riservata</faultstring></Fault>").decode()
    assert NOME not in out and DIAGNOSI not in out


@pytest.mark.parametrize("corpo", [
    # SOAP 1.2: Reason/Text
    b'<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"><e:Body><e:Fault><e:Code><e:Value>e:Sender'
    b'</e:Value></e:Code><e:Reason><e:Text xml:lang="it">Mario Sintetico - diagnosi riservata</e:Text></e:Reason>'
    b"</e:Fault></e:Body></e:Envelope>",
    # dettaglio del Fault con elementi propri
    b"<Fault><faultcode>Client</faultcode><faultstring>errore</faultstring><detail><errore>"
    b"Mario Sintetico - diagnosi riservata</errore></detail></Fault>",
    # faultstring con entità
    b"<Fault><faultstring>Mario&#32;Sintetico - diagnosi&#x20;riservata</faultstring></Fault>",
    # faultstring con JSON dentro
    b'<Fault><faultstring>{"paziente":"Mario Sintetico - diagnosi riservata"}</faultstring></Fault>',
], ids=["soap12", "detail", "entita", "json"])
def test_g3_n3_famiglia_fault(corpo):
    """Famiglia N3: SOAP 1.2, detail, entità, JSON nel testo del Fault."""
    out = Redattore().corpo(corpo).decode()
    assert NOME not in out and DIAGNOSI not in out, out


def test_g3_n3_faultstring_nell_errore_soap_del_meta(registro):
    """Famiglia N3: lo stesso testo arriva anche nell'eccezione ErroreSOAP, cioè nel campo `errore` del meta."""
    from varco.errori import ErroreSOAP

    r, scritti = registro()
    e = ErroreSOAP("env:Server", "Mario Sintetico - diagnosi riservata", 500, FAULT)
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), Risposta(500, FAULT, {}, 0.0), e)
    tutto = _tutto(scritti)
    assert NOME not in tutto and DIAGNOSI not in tutto, tutto
    assert "env:Server" in json.loads(scritti["meta.json"])["errore"]


# ====================================================================== N4: JSON annidato


@pytest.mark.parametrize("chiaro", [False, True], ids=["predefinito", "in_chiaro"])
def test_g3_n4_password_json_strutturata(chiaro):
    """Giro 3, N4: «JSON False {"password": {"value": "segreto-sintetico"}}» / «JSON True {…}»."""
    r = Redattore()
    out = r.corpo(b'{"password":{"value":"segreto-sintetico"}}', redigi=not chiaro).decode()
    assert SEGRETO not in out, out


@pytest.mark.parametrize("chiaro", [False, True], ids=["predefinito", "in_chiaro"])
@pytest.mark.parametrize("corpo", [
    b'{"password":{"value":"segreto-sintetico"}}',
    b'{"a":{"b":{"client_secret":{"x":[{"y":"segreto-sintetico"}]}}}}',
    b'{"token":["segreto-sintetico", {"v":"segreto-sintetico"}]}',
    b'[{"api_key":{"value":"segreto-sintetico"}}]',
    b'{"dati":"{\\"password\\":{\\"value\\":\\"segreto-sintetico\\"}}"}',   # JSON come stringa nel JSON
    b'<x><corpo>{"pin":{"value":"segreto-sintetico"}}</corpo></x>',        # JSON dentro XML
    b'{"xml":"<a><password>segreto-sintetico</password></a>"}',            # XML dentro JSON
], ids=["originale", "profondo", "lista", "radice_lista", "json_in_stringa", "json_in_xml", "xml_in_json"])
def test_g3_n4_famiglia_json_annidato(registro, chiaro, corpo):
    """Famiglia N4: a ogni profondità e in ogni involucro; attraverso RegistratoreFile, redatto e in chiaro."""
    r, scritti = registro(chiaro)
    r(Richiesta("sac.prova", "https://localhost/x", corpo), Risposta(200, corpo, {}, 0.0), None)
    assert SEGRETO not in _tutto(scritti), _tutto(scritti)


def test_g3_n4_dati_personali_annidati_redatti():
    """Famiglia N4, dati personali: un contenitore JSON classificato (cognome) si redige intero."""
    out = Redattore().corpo(b'{"cognome":{"valore":"Sintetico"},"esito":"0000"}').decode()
    assert "Sintetico" not in out and "0000" in out


# ====================================================================== giro 4: varianti trovate dalla verifica mirata


XML_PASSWORD = "<Envelope><Body><password>segreto-sintetico</password></Body></Envelope>"


@pytest.mark.parametrize("chiaro", [False, True], ids=["predefinito", "in_chiaro"])
@pytest.mark.parametrize("valore", [XML_PASSWORD, '{"password":{"value":"segreto-sintetico"}}',
                                    "x &lt;password&gt;segreto-sintetico&lt;/password&gt;"])
def test_g4_n2_user_agent_strutturato(registro, chiaro, valore):
    """Verifica giro 4, N2: lo stesso XML nell'header User-Agent restava in chiaro in modalità predefinita
    (ramo `user_agent()` fuori dalla redazione strutturata)."""
    r, scritti = registro(chiaro)
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>", {"User-Agent": valore}), None, None)
    assert SEGRETO not in _tutto(scritti), _tutto(scritti)


@pytest.mark.parametrize("chiaro", [False, True], ids=["predefinito", "in_chiaro"])
@pytest.mark.parametrize("testo", [
    r'{"password":{"value":"segreto-sintetico"}}',           # il controesempio della verifica
    r'{"password":"segreto-sintetico"}',
    '{"password":"segreto-sintetico", "nota":"it\'s"}',             # apice e virgolette: repr li sfuggirebbe
    "riga1\n<password>segreto-sintetico</password>",                # a capo: repr lo scriverebbe \\n
], ids=["u0077", "u0070", "apici", "a_capo"])
def test_g4_n2_eccezione_letta_dagli_argomenti(registro, chiaro, testo):
    """Verifica giro 4, N2: JSON con escape Unicode nella chiave dentro l'eccezione restava nel meta
    (repr(e) raddoppiava le barre rovesciate). Famiglia: ciò che repr sfugge."""
    r, scritti = registro(chiaro)
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), None, RuntimeError(testo))
    assert SEGRETO not in _tutto(scritti), _tutto(scritti)


@pytest.mark.parametrize("args", [(XML_PASSWORD, 2), (b"<password>segreto-sintetico</password>",),
                                  ({"password": "segreto-sintetico"},)], ids=["due_argomenti", "bytes", "dict"])
def test_g4_n2_eccezione_con_argomenti_non_stringa(registro, args):
    """Famiglia: più argomenti, argomenti bytes o dict."""
    r, scritti = registro()
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), None, RuntimeError(*args))
    assert SEGRETO not in _tutto(scritti), _tutto(scritti)


@pytest.mark.parametrize("corpo", [
    # il controesempio della verifica: XML con entità dentro una stringa JSON, in chiaro
    json.dumps({"dati": "&lt;Envelope&gt;&lt;password&gt;segreto-sintetico&lt;/password&gt;&lt;/Envelope&gt;"}).encode(),
    # entità numeriche
    json.dumps({"dati": "&#60;password&#62;segreto-sintetico&#60;/password&#62;"}).encode(),
    # percent-encoding di una coppia nome=valore e di un JSON
    json.dumps({"dati": "password%3Dsegreto-sintetico"}).encode(),
    json.dumps({"dati": "%7B%22token%22%3A%7B%22v%22%3A%22segreto-sintetico%22%7D%7D"}).encode(),
    # lo stesso dentro un nodo XML e come testo semplice
    b"<x><d>&amp;lt;password&amp;gt;segreto-sintetico&amp;lt;/password&amp;gt;</d></x>",
    b"testo libero password%3Dsegreto-sintetico",
], ids=["entita_xml_in_json", "entita_numeriche", "percent_kv", "percent_json", "entita_in_xml", "testo"])
def test_g4_n4_markup_e_credenziali_codificati_in_chiaro(registro, corpo):
    """Verifica giro 4, N4: in modalità in chiaro `&lt;password&gt;…` dentro un JSON restava leggibile
    (`credenziali()` non decodifica). Famiglia: entità, percent-encoding, in JSON, XML, testo."""
    r, scritti = registro(True)
    r(Richiesta("sac.prova", "https://localhost/x", corpo), Risposta(200, corpo, {}, 0.0), None)
    assert SEGRETO not in _tutto(scritti), _tutto(scritti)


def test_g4_n4_in_chiaro_senza_credenziali_i_byte_restano_identici():
    """Gruppo di controllo: in chiaro un corpo con entità o percent-encoding ma senza credenziali (e senza
    markup nascosto) resta identico; markup nascosto e illeggibile invece non si scrive (nel dubbio)."""
    for corpo in (json.dumps({"d": "a &amp; b c%20d"}).encode(), b"<x><d>a &amp;amp; b</d></x>", b"a%20b"):
        assert Redattore().corpo(corpo, redigi=False) == corpo
    assert b"NON SCRITTO" in Redattore().corpo(json.dumps({"d": "a &lt;b&gt; c"}).encode(), redigi=False)


J_ANNIDATO = '{"password":{"value":"segreto-sintetico"}}'


def _serializzato(n: int) -> str:
    t = J_ANNIDATO
    for _ in range(n):
        t = json.dumps(t)
    return t


@pytest.mark.parametrize("chiaro", [False, True], ids=["predefinito", "in_chiaro"])
@pytest.mark.parametrize("forma", [
    json.dumps({"dati": json.dumps(J_ANNIDATO)}),               # il controesempio della verifica r2
    json.dumps({"dati": _serializzato(2)}),                     # terzo livello
    _serializzato(1), _serializzato(3),                         # stringa JSON alla radice
    "errore: " + _serializzato(2),                              # dentro un testo
    "<x><d>" + json.dumps(J_ANNIDATO) + "</d></x>",             # stringa JSON dentro XML
    J_ANNIDATO.replace('"', '\\"'),                             # escape senza le virgolette esterne
], ids=["dati_2", "dati_3", "radice_1", "radice_3", "testo_2", "xml_1", "escape_nudo"])
def test_g4r2_json_serializzato_piu_volte(registro, chiaro, forma):
    """Verifica giro 4 (r2), N2/N4: JSON serializzato come stringa a un livello in più
    (`json.dumps({"dati": json.dumps(J)})`) lasciava la password nel corpo, nell'errore e negli header."""
    r, scritti = registro(chiaro)
    r(Richiesta("sac.prova", "https://localhost/x", forma.encode(), {"X-Dettaglio": forma, "User-Agent": forma}),
      Risposta(200, forma.encode(), {"X-Dettaglio": forma}, 0.0), RuntimeError(forma))
    tutto = _tutto(scritti)
    # anche decodificando a mano ogni livello di escape il segreto non c'è
    assert SEGRETO not in tutto and SEGRETO not in tutto.replace("\\\\", "").replace("\\", ""), tutto


def test_g4r2_stringhe_senza_escape_restano_testo():
    """Gruppo di controllo: una stringa tra virgolette senza escape non si tocca; in chiaro i byte restano."""
    corpo = b'{"messaggio": "il medico ha detto \\"ok\\"", "codice": "0000"}'
    out = Redattore().corpo(corpo, redigi=False)
    assert json.loads(out) == json.loads(corpo)
    assert Redattore().corpo(b'testo "tra virgolette" normale', redigi=False) == b'testo "tra virgolette" normale'


@pytest.mark.parametrize("chiaro", [False, True], ids=["predefinito", "in_chiaro"])
@pytest.mark.parametrize("forma", [
    "%7B%22token%22%3A%7B%22v%22%3A%22segreto-sintetico%22%7D%7D",            # JSON annidato percent-encoded
    "dettaglio: %7B%22password%22%3A%5B%7B%22v%22%3A%22segreto-sintetico%22%7D%5D%7D",
    "{&quot;password&quot;:{&quot;value&quot;:&quot;segreto-sintetico&quot;}}",  # JSON con entità
], ids=["percent", "percent_testo", "entita"])
def test_g4r2_json_codificato_in_ogni_modalita(registro, chiaro, forma):
    """Famiglia N2/N4: JSON annidato che emerge solo decodificando (percent-encoding, entità), in corpo,
    header ed errore, nelle due modalità."""
    r, scritti = registro(chiaro)
    r(Richiesta("sac.prova", "https://localhost/x", forma.encode(), {"X-Dettaglio": forma}),
      Risposta(200, f"<x><d>{forma}</d></x>".encode(), {}, 0.0), RuntimeError(forma))
    assert SEGRETO not in _tutto(scritti), _tutto(scritti)


@pytest.mark.parametrize("arg", [{"password": {"value": "segreto-sintetico"}},
                                 [{"token": ["segreto-sintetico"]}],
                                 ("x", {"pin": {"v": "segreto-sintetico"}})], ids=["dict", "list", "tuple"])
def test_g4r2_contenitori_python_nell_eccezione(registro, arg):
    """Famiglia N2: un dict/list Python annidato come argomento dell'eccezione (repr con apici singoli)."""
    r, scritti = registro()
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), None, RuntimeError(arg))
    assert SEGRETO not in _tutto(scritti), _tutto(scritti)
    r2, scritti2 = registro()
    r2(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), None, RuntimeError("dettaglio " + repr(arg)))
    assert SEGRETO not in _tutto(scritti2), _tutto(scritti2)


@pytest.mark.parametrize("chiaro", [False, True], ids=["predefinito", "in_chiaro"])
@pytest.mark.parametrize("arg", [
    rb'{"password":{"value":"segreto-sintetico"}}',          # il controesempio della verifica r3
    rb'{"password":"segreto-sintetico"}',
    bytearray(rb'<x><password>segreto-sintetico</password></x>'),
    memoryview(rb'{"token":{"v":"segreto-sintetico"}}'),
    {"dati": rb'{"password":"segreto-sintetico"}'},           # bytes dentro un contenitore
    {'{"pin":{"v":"segreto-sintetico"}}'},                         # un insieme con JSON dentro
    OSError(r'{"password":"segreto-sintetico"}'),             # un'eccezione come argomento
], ids=["bytes_u0077", "bytes_u0070", "bytearray_xml", "memoryview_json", "bytes_in_dict", "insieme", "eccezione"])
def test_g4r3_argomenti_non_stringa_letti_come_testo(registro, chiaro, arg):
    """Verifica giro 4 (r3), N2: `RuntimeError(rb'{"pass\\u0077ord":…}')` lasciava la password nel meta
    (repr dei bytes raddoppiava le barre). Famiglia: bytes, bytearray, memoryview, bytes in un contenitore,
    eccezione come argomento."""
    r, scritti = registro(chiaro)
    r(Richiesta("sac.prova", "https://localhost/x", b"<x/>"), None, RuntimeError(arg))
    assert SEGRETO not in _tutto(scritti), _tutto(scritti)


def test_g4r3_bytes_non_utf8_non_scritti():
    """Nel dubbio non si scrive: bytes che non sono UTF-8 nell'errore diventano un segnaposto."""
    out = Redattore().errore(RuntimeError(b"\xffsegreto-sintetico\xfe"))
    assert SEGRETO not in out and "NON SCRITTO" in out
