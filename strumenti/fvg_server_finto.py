# SPDX-License-Identifier: EUPL-1.2
"""Server SAR FVG FINTO, in locale, per provare il client del kit senza chiamare la Regione FVG né Insiel.

Non è il SAR e non ne imita la logica sanitaria: risponde con buste costruite da noi sugli XSD
pubblicati da Insiel (`wsdl_prescritto.zip`), perché le specifiche (Idof-dem-AT-01 dell'11/02/2026)
non pubblicano nessuna risposta.

Cosa controlla davvero su ogni richiesta, come dice la specifica:
  - HTTPS con **mutua autenticazione**: il server chiede il certificato client e lo verifica con
    la CA di prova (cap. 4: «Richiede quindi la presenza di 2 certificati»); senza certificato
    l'handshake fallisce;
  - il CF del certificato (CN «CF/...» come una CNS) è il medico che invia: cfMedico2 se c'è,
    altrimenti cfMedico1 (par. 2.2);
  - `User-Agent` nel formato del par. 3.1, con lo stesso ProdottoCME dell'attributo `prodottoCme`;
  - SOAP 1.1, SOAPAction vuota (WSDL), elemento radice giusto per l'endpoint del cap. 5;
  - il Body valida contro lo XSD ufficiale FVG, se gli si passa la cartella `wsdl/sar` dello zip;
  - `prodottoCme` = quello atteso (accreditamento finto);
  - `codiceAss` si decifra (RSA PKCS#1 v1.5, base64) con la chiave privata del certificato di
    cifratura di prova (par. 4.6) e dà un «Codice Fiscale/STP/ENI/altro» (p. 18 e XSD): un CF,
    un codice STP o ENI (3 lettere e 13 cifre), o un altro codice alfanumerico fino a 16
    caratteri; un codice STP vuole il tipo ricetta ST (p. 18: «coerente con ... Tipo Ricetta»);
    un codice che comincia per STP/ENI senza le 13 cifre non passa come «altro» (giro 2, N3);
  - `codRegione` = "060" (p. 16), conservato e restituito dalla visualizzazione (giro 2, N5);
  - specialistica: `versioneCR` presente, con la patch (par. 3.1, p. 13), e `codCatalogoPrescr`
    in ogni riga (p. 21). Lo XSD li lascia facoltativi: il controllo è qui;
  - farmaceutica: niente `codCatalogoPrescr`, `tipoAccesso`, `numeroNota`, riservati alla
    specialistica (p. 21; giro 2, N2);
  - lista degli NRE: filtri NRE, lotto (`codLotto` dentro l'NRE, p. 15), CF assistito, tipo e
    periodo di compilazione (p. 29), estremi compresi.

Revisione esterna del 02/10/2026: questi controlli mancavano e client e server davano successo
insieme. Sono scritti qui leggendo la specifica, non copiati dal client.

Regole di merito FINTE, solo per esercitare i percorsi del client (i codici 1020, 1120, 1125,
5005 sono quelli visti nell'ambiente di test del SAC; 0196 e 060120-060130 vengono dalla specifica FVG):
  - nota AIFA "999" -> rifiuto 9999 con errore 1020 (E);
  - gruppo di equivalenza "DPC" -> comunicazione 0196 «CONTIENE FARMACI IN DPC...» (par. 4.2);
  - codice di catalogo "999999" -> rifiuto con errore 060120 nell'ElencoErroriRicette (par. 2.3.6);
    "999998" -> lo stesso codice come SOAP Fault. La specifica non dice quale delle due forme usi
    il SAR: il client le riconosce entrambe. NB: 060120 non rispetta `codEsitoType` (4 cifre),
    quindi la prima forma NON è valida contro lo XSD FVG: è un difetto della specifica, voluto qui;
  - NRE inesistente -> 5005; secondo annullamento -> 1120; annullamento con cfMedico che non è
    il titolare o da un medico diverso da chi ha prescritto -> 1125;
  - verifica del sostituto con titolare = sostituto -> errore 1212 in ElencoErrori;
  - riga specialistica con `numeroNota` (DM 9/12/2015) -> `ElencoNota/Nota` nella ricevuta con
    `tipoAmbulatorio` "AMB-PROVA" (p. 22: il campo lo restituisce il SAC, non il medico).

Uso dai test: `with ServerFVG(...) as s: s.url`. Solo libreria standard, lxml e cryptography.
"""

from __future__ import annotations

import base64
import datetime as _dt
import http.server
import itertools
import re
import ssl
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
_V = "-v1.0"
NS_TIPI = f"http://tipodati.xsd.dem.sanita.fvg.it{_V}"
NS_RIC = {
    "InvioPrescrittoRicevuta": f"http://invioprescrittoricevuta.xsd.dem.sanita.fvg.it{_V}",
    "VisualizzaPrescrittoRicevuta": f"http://visualizzaprescrittoricevuta.xsd.dem.sanita.fvg.it{_V}",
    "AnnullaPrescrittoRicevuta": f"http://annullaprescrittoricevuta.xsd.dem.sanita.fvg.it{_V}",
    "VerificaPosizioneMedicoSostitutoRicevuta": f"http://verificaposizionemedicosostitutoricevuta.xsd.dem.sanita.fvg.it{_V}",
    "InterrogaNreUtilRicevuta": "http://interroganreutilricevuta.xsd.dem.sanita.fvg.it",
}
NS_TIPI_NRE = "http://tipodati.xsd.dem.sanita.fvg.it"

# percorso (cap. 5) -> (radice attesa della richiesta, XSD della richiesta nella cartella wsdl/sar)
ENDPOINT = {
    "/SARWs/InvioPrescrittoSecure": ("InvioPrescrittoRichiesta", "invioPrescritto/v1.0/InvioPrescrittoRichiesta-v1.0.xsd"),
    "/SARWs/visualizzaPrescrittoSecure": ("VisualizzaPrescrittoRichiesta", "visualizzaPrescritto/v1.0/VisualizzaPrescrittoRichiesta-v1.0.xsd"),
    "/SARWs/annullaPrescrittoSecure": ("AnnullaPrescrittoRichiesta", "annullaPrescritto/v1.0/AnnullaPrescrittoRichiesta-v1.0.xsd"),
    "/SARWs/GestoreAutorizzazioniSecure": ("VerificaPosizioneMedicoSostitutoRichiesta",
                                           "gestoreAutorizzazioni/v1.0/VerificaPosizioneMedicoSostitutoRichiesta-v1.0.xsd"),
    "/SARWs/InterrogaNreUtil": ("InterrogaNreUtilRichiesta", "interrogaNreUtilizzati/InterrogaNreUtilRichiesta.xsd"),
}
# XSD delle ricevute, per i test sulle risposte sintetiche
XSD_RICEVUTE = {
    "InvioPrescrittoRicevuta": "invioPrescritto/v1.0/InvioPrescrittoRicevuta-v1.0.xsd",
    "VisualizzaPrescrittoRicevuta": "visualizzaPrescritto/v1.0/VisualizzaPrescrittoRicevuta-v1.0.xsd",
    "AnnullaPrescrittoRicevuta": "annullaPrescritto/v1.0/AnnullaPrescrittoRicevuta-v1.0.xsd",
    "VerificaPosizioneMedicoSostitutoRicevuta": "gestoreAutorizzazioni/v1.0/VerificaPosizioneMedicoSostitutoRicevuta-v1.0.xsd",
    "InterrogaNreUtilRicevuta": "interrogaNreUtilizzati/InterrogaNreUtilRicevuta.xsd",
}
_UA = re.compile(r"^(?P<prod>\S+)/(?P<ver>\S+) (?P<so>.+)/(?P<vso>\S+) (?P<cf>[A-Z0-9]{16})/(?P<dev>\S+)$")
_CF = re.compile(r"[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]")
_STP_ENI = re.compile(r"(STP|ENI)[0-9]{13}")
_ALTRO = re.compile(r"[A-Z0-9]{1,16}")
_VERSIONE_CR = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


# ------------------------------------------------------------------ buste di risposta


def busta(corpo: str) -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="{NS_SOAP}">'
            f"<soapenv:Body>{corpo}</soapenv:Body></soapenv:Envelope>").encode("utf-8")


def fault(testo: str) -> bytes:
    return busta(f"<soapenv:Fault><faultcode>soapenv:Server</faultcode><faultstring>{escape(testo)}</faultstring></soapenv:Fault>")


def _x(tag: str, valore: str | None, pre: str = "") -> str:
    return "" if valore is None else f"<{pre}{tag}>{escape(valore)}</{pre}{tag}>"


def _errori(errori: list[tuple[str, str, str, str]], pre: str = "r:", td: str = "td:", nome: str = "ElencoErroriRicette") -> str:
    if not errori:
        return ""
    righe = "".join(
        f"<{td}ErroreRicetta>{_x('codEsito', c, td)}{_x('esito', t, td)}{_x('progPresc', p, td)}{_x('tipoErrore', g, td)}</{td}ErroreRicetta>"
        for c, t, p, g in errori
    )
    return f"<{pre}{nome}>{righe}</{pre}{nome}>"


def _comunicazioni(com: list[tuple[str, str]], pre: str = "r:", td: str = "td:") -> str:
    if not com:
        return ""
    righe = "".join(f"<{td}Comunicazione>{_x('codice', c, td)}{_x('messaggio', m, td)}</{td}Comunicazione>" for c, m in com)
    return f"<{pre}ElencoComunicazioni>{righe}</{pre}ElencoComunicazioni>"


def _radice(nome: str, interno: str, ns_tipi: str = NS_TIPI) -> str:
    return f'<r:{nome} xmlns:r="{NS_RIC[nome]}" xmlns:td="{ns_tipi}">{interno}</r:{nome}>'


def _note(note: list[tuple[str, str, str]], pre: str = "r:", td: str = "td:") -> str:
    if not note:
        return ""
    righe = "".join(f"<{td}Nota>{_x('progrPresc', p, td)}{_x('codProdPrest', c, td)}{_x('tipoAmbulatorio', a, td)}</{td}Nota>"
                    for p, c, a in note)
    return f"<{pre}ElencoNota>{righe}</{pre}ElencoNota>"


def ricevuta_invio(codice: str, nre: str | None = None, codice_aut: str | None = None, data: str | None = None,
                   errori=(), comunicazioni=(), note=()) -> bytes:
    interno = (_x("nre", nre, "r:") + _x("codAutenticazione", codice_aut, "r:") + _x("dataInserimento", data, "r:")
               + _x("codEsitoInserimento", codice, "r:") + _errori(list(errori)) + _comunicazioni(list(comunicazioni))
               + _note(list(note)))
    return busta(_radice("InvioPrescrittoRicevuta", interno))


def ricevuta_visualizza(codice: str, ricetta: "RicettaFinta | None" = None, errori=(), comunicazioni=()) -> bytes:
    interno = ""
    if ricetta is not None:
        righe = "".join(
            "<td:DettaglioPrescrizione>" + "".join(_x(t, v, "td:") for t, v in r) + "</td:DettaglioPrescrizione>"
            for r in ricetta.righe
        )
        interno = (_x("nre", ricetta.nre, "r:") + _x("cfMedico1", ricetta.titolare, "r:") + _x("cfMedico2", ricetta.sostituto, "r:")
                   + _x("codRegione", ricetta.cod_regione, "r:") + _x("tipoPrescrizione", ricetta.tipo, "r:")
                   + _x("dataCompilazione", ricetta.data_compilazione, "r:")
                   + f"<r:ElencoDettagliPrescrizioni>{righe}</r:ElencoDettagliPrescrizioni>"
                   + _x("statoProcesso", ricetta.stato, "r:") + _x("codAutenticazione", ricetta.codice_aut, "r:")
                   + _x("dataInserimento", ricetta.data_inserimento, "r:"))
    interno += _x("codEsitoVisualizzazione", codice, "r:") + _errori(list(errori)) + _comunicazioni(list(comunicazioni))
    return busta(_radice("VisualizzaPrescrittoRicevuta", interno))


def ricevuta_annulla(codice: str, nre: str, errori=(), comunicazioni=()) -> bytes:
    interno = _x("nre", nre, "r:") + _x("codEsitoAnnullamento", codice, "r:") + _errori(list(errori)) + _comunicazioni(list(comunicazioni))
    return busta(_radice("AnnullaPrescrittoRicevuta", interno))


def ricevuta_verifica_sostituto(titolare: str, sostituto: str, asl: str, abilitato: bool, messaggio: str | None = None,
                                errori=()) -> bytes:
    # in questa ricevuta il contenitore degli errori si chiama ElencoErrori (XSD), non ElencoErroriRicette
    interno = (_x("cfMedicoTitolare", titolare, "r:") + _x("cfMedicoSostituto", sostituto, "r:") + _x("codASLAo", asl, "r:")
               + _x("isAbilitato", "true" if abilitato else "false", "r:") + _x("message", messaggio, "r:")
               + _errori(list(errori), nome="ElencoErrori"))
    return busta(_radice("VerificaPosizioneMedicoSostitutoRicevuta", interno))


def ricevuta_interroga_nre(codice: str, record: list["RicettaFinta"]) -> bytes:
    righe = "".join(
        "<td:NreUtilRecord>" + _x("nre", r.nre, "td:") + _x("cfMedico", r.titolare, "td:") + _x("tipoPrescrizione", r.tipo, "td:")
        + _x("dataCompilazioneRicetta", r.data_compilazione, "td:") + _x("provenienza", "0", "td:")
        + _x("codAutenticazione", r.codice_aut, "td:") + "</td:NreUtilRecord>"
        for r in record
    )
    elenco = f"<r:ElencoNreUtilRecord>{righe}</r:ElencoNreUtilRecord>" if record else ""
    interno = _x("codEsitoInterrogaNreUtilizzati", codice, "r:") + elenco
    return busta(_radice("InterrogaNreUtilRicevuta", interno, NS_TIPI_NRE))


# ------------------------------------------------------------------ stato e regole


@dataclass
class RicettaFinta:
    nre: str
    titolare: str
    sostituto: str | None
    tipo: str
    data_compilazione: str
    codice_aut: str
    data_inserimento: str
    righe: list[list[tuple[str, str]]]
    stato: str = "3"
    cf_assistito: str | None = None  # in chiaro, decifrato da codiceAss
    cod_regione: str = "060"  # quello ricevuto: la visualizzazione lo restituisce, non lo inventa


@dataclass
class StatoFVG:
    ricette: dict[str, RicettaFinta] = field(default_factory=dict)
    richieste: list[dict] = field(default_factory=list)  # percorso, user-agent, cf del certificato
    contatore: itertools.count = field(default_factory=lambda: itertools.count(1))
    sostituti_abilitati: set[tuple[str, str, str]] = field(default_factory=set)


def _locale(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _figli(el: ET.Element) -> dict[str, str]:
    return {_locale(c.tag): (c.text or "").strip() for c in el}


def _cf_certificato(peer: dict | None) -> str | None:
    if not peer:
        return None
    for rdn in peer.get("subject", ()):
        for k, v in rdn:
            if k == "commonName":
                m = _CF.search(v.upper())
                if m:
                    return m.group(0)
    return None


class _Gestore(http.server.BaseHTTPRequestHandler):
    server_version = "SARFVGFinto/0.1"

    def log_message(self, *a):  # silenzio
        pass

    def _rispondi(self, codice: int, corpo: bytes) -> None:
        self.send_response(codice)
        self.send_header("Content-Type", "text/xml;charset=UTF-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_POST(self):  # noqa: N802
        s: ServerFVG = self.server.finto  # type: ignore[attr-defined]
        corpo = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        cf_cert = _cf_certificato(self.connection.getpeercert())
        ua = self.headers.get("User-Agent", "")
        s.stato.richieste.append({"percorso": self.path, "user_agent": ua, "cf_certificato": cf_cert,
                                  "soapaction": self.headers.get("SOAPAction")})
        try:
            stato, risposta = s.gestisci(self.path, self.headers, corpo, cf_cert)
        except Exception as e:  # il server finto non deve mai cadere in silenzio
            stato, risposta = 500, fault(f"errore del server finto: {e!r}")
        self._rispondi(stato, risposta)


class ServerFVG:
    """Server HTTPS con mutua autenticazione su 127.0.0.1. Vedi la docstring del modulo."""

    def __init__(self, *, certificato_server: Path, chiave_server: Path, ca_client: Path, chiave_cifratura_pem: bytes,
                 prodotto_cme: str = "VARCO-PROVA", xsd_dir: Path | None = None,
                 sostituti_abilitati: set[tuple[str, str, str]] | None = None):
        from cryptography.hazmat.primitives import serialization

        self.prodotto_cme = prodotto_cme
        self.xsd_dir = Path(xsd_dir) if xsd_dir else None
        self._schemi: dict[str, object] = {}
        self._chiave_cf = serialization.load_pem_private_key(chiave_cifratura_pem, password=None)
        self.stato = StatoFVG(sostituti_abilitati=set(sostituti_abilitati or ()))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2  # par. 2.3.2: TLS 1.2
        ctx.load_cert_chain(str(certificato_server), str(chiave_server))
        ctx.load_verify_locations(str(ca_client))
        ctx.verify_mode = ssl.CERT_REQUIRED  # mutua autenticazione
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Gestore)
        self._httpd.socket = ctx.wrap_socket(self._httpd.socket, server_side=True)
        self._httpd.finto = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self._httpd.server_address[1]}"

    def __enter__(self) -> "ServerFVG":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *a) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    # ------------------------------------------------------------------ controlli

    def _schema(self, relativo: str):
        if self.xsd_dir is None:
            return None
        if relativo not in self._schemi:
            from lxml import etree

            self._schemi[relativo] = etree.XMLSchema(etree.parse(str(self.xsd_dir / relativo)))
        return self._schemi[relativo]

    def _decifra_cf(self, cifrato: str) -> str:
        from cryptography.hazmat.primitives.asymmetric import padding

        return self._chiave_cf.decrypt(base64.b64decode(cifrato, validate=True), padding.PKCS1v15()).decode("ascii")

    def gestisci(self, percorso: str, intestazioni, corpo: bytes, cf_cert: str | None) -> tuple[int, bytes]:
        if percorso not in ENDPOINT:
            return 404, b"percorso sconosciuto"
        atteso, xsd = ENDPOINT[percorso]
        if cf_cert is None:
            return 500, fault("certificato client senza codice fiscale")
        if intestazioni.get("SOAPAction") != '""':
            return 500, fault(f"SOAPAction inattesa: {intestazioni.get('SOAPAction')!r} (WSDL: vuota)")
        ua = _UA.match(intestazioni.get("User-Agent", ""))
        if ua is None:
            return 500, fault("User-Agent non conforme al par. 3.1")
        radice = ET.fromstring(corpo)
        body = radice.find(f"{{{NS_SOAP}}}Body")
        if body is None or len(body) != 1:
            return 500, fault("busta senza Body")
        el = body[0]
        if _locale(el.tag) != atteso:
            return 500, fault(f"radice {_locale(el.tag)} invece di {atteso}")
        schema = self._schema(xsd)
        if schema is not None:
            from lxml import etree

            if not schema.validate(etree.fromstring(ET.tostring(el))):
                return 500, fault("cvc: " + "; ".join(e.message for e in schema.error_log)[:500])
        prodotto = el.get(f"{{{NS_TIPI}}}prodottoCme")
        if atteso != "InterrogaNreUtilRichiesta":
            if prodotto != self.prodotto_cme:
                return 500, fault(f"prodotto non accreditato: {prodotto!r}")
            if ua.group("prod") != prodotto:
                return 500, fault("ProdottoCME dello User-Agent diverso dall'attributo prodottoCme")
        f = _figli(el)
        if (f.get("pinCode") or "") != "":
            return 500, fault("pinCode valorizzato: nel SAR FVG non è utilizzato")
        metodo = {
            "InvioPrescrittoRichiesta": self._invio,
            "VisualizzaPrescrittoRichiesta": self._visualizza,
            "AnnullaPrescrittoRichiesta": self._annulla,
            "VerificaPosizioneMedicoSostitutoRichiesta": self._verifica_sostituto,
            "InterrogaNreUtilRichiesta": self._interroga,
        }[atteso]
        return metodo(el, f, cf_cert)

    def _invio(self, el, f, cf_cert) -> tuple[int, bytes]:
        inviante = f.get("cfMedico2") or f.get("cfMedico1")
        if cf_cert != inviante:
            return 500, fault(f"il certificato è di {cf_cert}, invia {inviante}")
        if f.get("codRegione") != "060":
            # IDOF-DEM-00001-AT-16-01 p. 16, InvioPrescrittoRichiesta: codRegione «Codice Regione del
            # medico prescrittore "060"», obbligatorio (revisione esterna giro 2, 4-sar-fvg N5)
            return 500, fault(f"codRegione {f.get('codRegione')!r}: atteso \"060\" (p. 16)")
        cf = None
        if f.get("codiceAss"):
            cf = self._decifra_cf(f["codiceAss"])
            if not (_CF.fullmatch(cf) or _STP_ENI.fullmatch(cf) or _ALTRO.fullmatch(cf)):
                return 200, ricevuta_invio("9999", errori=[("1001", "codice assistito non valido", "0", "E")])
            if cf.startswith(("STP", "ENI")) and not _STP_ENI.fullmatch(cf):
                # p. 18: «Codice Fiscale/STP/ENI/altro». «altro» non deve coprire uno STP/ENI malformato
                # (es. troncato al prefisso): revisione esterna giro 2, 4-sar-fvg N3
                return 200, ricevuta_invio("9999", errori=[("1001", "codice STP/ENI malformato", "0", "E")])
            if cf.startswith("STP") and f.get("tipoRic") != "ST":
                return 200, ricevuta_invio("9999", errori=[("1001", "codice STP non coerente con il tipo ricetta", "0", "E")])
        elenco = next(c for c in el if _locale(c.tag) == "ElencoDettagliPrescrizioni")
        righe = [{_locale(c.tag): (c.text or "").strip() for c in d} for d in elenco]
        if f.get("tipoPrescrizione") == "F":
            # p. 21: codCatalogoPrescr e tipoAccesso «da utilizzarsi unicamente per prescrizioni
            # specialistiche», numeroNota «obbligatorio unicamente per le prescrizioni specialistiche
            # trattate dal DM 9 dic 2015». Lo XSD li ammette anche qui (revisione esterna giro 2, N2).
            for r in righe:
                vietati = [t for t in ("codCatalogoPrescr", "tipoAccesso", "numeroNota") if r.get(t)]
                if vietati:
                    return 500, fault(f"{', '.join(vietati)} su una ricetta farmaceutica: solo specialistica (p. 21)")
        if f.get("tipoPrescrizione") == "P":
            versione = elenco.get(f"{{{NS_TIPI}}}versioneCR")
            if not versione or not _VERSIONE_CR.fullmatch(versione):
                return 500, fault(f"versioneCR mancante o senza patch ({versione!r}): obbligatoria per la specialistica (par. 3.1)")
            if any(not r.get("codCatalogoPrescr") for r in righe):
                return 500, fault("codCatalogoPrescr obbligatorio per le ricette specialistiche (par. 4.2)")
        for r in righe:
            if r.get("codCatalogoPrescr") == "999998":
                return 500, fault("060125 prestazione non ancora attiva in dematerializzata")
            if r.get("codCatalogoPrescr") == "999999":
                return 200, ricevuta_invio("9999", errori=[("060120", "prestazione non ancora attiva in dematerializzata", "1", "E")])
            if r.get("notaProd") == "999":
                return 200, ricevuta_invio("9999", errori=[("1020", "nota AIFA non valida", "1", "E")])
        n = next(self.stato.contatore)
        nre = f["nre"] if f.get("nre") else f"0600A{n:010d}"
        if nre in self.stato.ricette:
            return 200, ricevuta_invio("9999", errori=[("1021", "NRE già utilizzato", "0", "E")])
        adesso = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ricetta = RicettaFinta(nre, f["cfMedico1"], f.get("cfMedico2") or None, f["tipoPrescrizione"], f["dataCompilazione"],
                               f"{n:030d}", adesso, [[(k, v) for k, v in r.items() if v] for r in righe], cf_assistito=cf,
                               cod_regione=f["codRegione"])
        self.stato.ricette[nre] = ricetta
        com = [("0196", "CONTIENE FARMACI IN DPC NELLA REGIONE DI PRESCRIZIONE")] if any(r.get("codGruppoEquival") == "DPC" for r in righe) else []
        note = [(str(i), r.get("codProdPrest", ""), "AMB-PROVA") for i, r in enumerate(righe, start=1)
                if r.get("numeroNota") and f["tipoPrescrizione"] == "P"]  # solo prestazioni specialistiche
        return 200, ricevuta_invio("0000", nre, ricetta.codice_aut, adesso, comunicazioni=com, note=note)

    def _visualizza(self, el, f, cf_cert) -> tuple[int, bytes]:
        r = self.stato.ricette.get(f["nre"])
        if r is None:
            return 200, ricevuta_visualizza("9999", errori=[("5005", "NRE inesistente", "0", "E")])
        medici = {r.titolare, r.sostituto} - {None}
        if f["cfMedico"] not in medici or cf_cert not in medici:
            return 200, ricevuta_visualizza("9999", errori=[("1125", "visualizzazione non consentita", "0", "E")])
        return 200, ricevuta_visualizza("0000", r)

    def _annulla(self, el, f, cf_cert) -> tuple[int, bytes]:
        r = self.stato.ricette.get(f["nre"])
        if r is None:
            return 200, ricevuta_annulla("9999", f["nre"], errori=[("5005", "NRE inesistente", "0", "E")])
        if f["cfMedico"] != r.titolare or cf_cert != (r.sostituto or r.titolare):
            return 200, ricevuta_annulla("9999", f["nre"], errori=[("1125", "annullamento non consentito", "0", "E")])
        if r.stato != "3":
            return 200, ricevuta_annulla("9999", f["nre"], errori=[("1120", "ricetta non annullabile", "0", "E")])
        r.stato = "4"
        return 200, ricevuta_annulla("0000", f["nre"])

    def _verifica_sostituto(self, el, f, cf_cert) -> tuple[int, bytes]:
        chiave = (f["cfMedicoTitolare"], f["cfMedicoSostituto"], f["codASLAo"])
        abilitato = chiave in self.stato.sostituti_abilitati
        if f["cfMedicoTitolare"] == f["cfMedicoSostituto"]:
            return 200, ricevuta_verifica_sostituto(*chiave, False, errori=[("1212", "titolare e sostituto coincidono", "0", "E")])
        return 200, ricevuta_verifica_sostituto(*chiave, abilitato, None if abilitato else "sostituto non abilitato (server finto)")

    def _interroga(self, el, f, cf_cert) -> tuple[int, bytes]:
        """Filtri del par. 4.5 (p. 29): NRE, codLotto, cfAssistito, tipoPrescr e le date di
        compilazione (estremi compresi). Il codLotto sta nell'NRE dopo regione (3), raggruppamento
        (2) e identificativo del lotto (1): 7 caratteri per i lotti da 100 (identificativo 0), 6 per
        quelli da 1000 (identificativo 1), p. 15."""
        if f["cfMedico"] != cf_cert:
            return 500, fault("cfMedico diverso dal certificato")
        dal, al = f.get("dataCompilazioneRicettaDal"), f.get("dataCompilazioneRicettaAl")
        if not f.get("nre") and not (dal and al):
            return 500, fault("senza NRE servono le date di compilazione (dal, al)")

        def lotto(nre: str) -> str:
            return nre[6:13] if nre[5:6] == "0" else nre[6:12]

        def passa(r: RicettaFinta) -> bool:
            return (r.titolare == f["cfMedico"]
                    and (not f.get("nre") or r.nre == f["nre"])
                    and (not f.get("codLotto") or lotto(r.nre) == f["codLotto"])
                    and (not f.get("cfAssistito") or r.cf_assistito == f["cfAssistito"])
                    and (not f.get("tipoPrescr") or r.tipo == f["tipoPrescr"])
                    and (not dal or r.data_compilazione >= dal) and (not al or r.data_compilazione <= al))

        return 200, ricevuta_interroga_nre("0000", [r for r in self.stato.ricette.values() if passa(r)])


# ------------------------------------------------------------------ materiale TLS di prova


@dataclass(frozen=True)
class MaterialeTLS:
    ca: Path  # CA di prova: firma il certificato del server e quelli client
    certificato_server: Path
    chiave_server: Path
    client: dict[str, tuple[Path, Path]]  # CF -> (certificato PEM, chiave PEM), come una CNS
    certificato_cifratura: Path  # certificato del CF dell'assistito (al posto di quello di Insiel)
    chiave_cifratura_pem: bytes

    def certificato_client_der(self, cf: str) -> bytes:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        return x509.load_pem_x509_certificate(self.client[cf][0].read_bytes()).public_bytes(serialization.Encoding.DER)

    def contesto_client(self, cf: str | None) -> ssl.SSLContext:
        """Contesto TLS del medico: si fida della CA di prova e presenta il certificato della sua «carta»."""
        ctx = ssl.create_default_context(cafile=str(self.ca))
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        if cf is not None:
            cert, chiave = self.client[cf]
            ctx.load_cert_chain(str(cert), str(chiave))
        return ctx


def materiale_tls(cartella: Path, cf_medici: list[str]) -> MaterialeTLS:
    """Genera CA, certificato del server (127.0.0.1, localhost), certificati client con CN «CF/...» e un
    certificato di cifratura. Tutto autofirmato e di PROVA: niente a che vedere coi certificati Insiel."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    cartella.mkdir(parents=True, exist_ok=True)
    adesso = _dt.datetime.now(_dt.timezone.utc)

    def chiave():
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def nome(cn: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"), x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def scrivi(base: str, cert: x509.Certificate, k) -> tuple[Path, Path]:
        pc, pk = cartella / f"{base}.pem", cartella / f"{base}.key"
        pc.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        pk.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return pc, pk

    def emetti(soggetto: str, k, ca_k, ca_nome, *, ca=False, san=None, eku=None) -> x509.Certificate:
        b = (x509.CertificateBuilder().subject_name(nome(soggetto)).issuer_name(ca_nome).public_key(k.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(adesso - _dt.timedelta(days=1))
             .not_valid_after(adesso + _dt.timedelta(days=30))
             .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
             # i Python recenti verificano in modo stretto (VERIFY_X509_STRICT): servono SKI e AKI
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_k.public_key()), critical=False))
        if san:
            b = b.add_extension(x509.SubjectAlternativeName(san), critical=False)
        if eku:
            b = b.add_extension(x509.ExtendedKeyUsage(eku), critical=False)
        if ca:
            b = b.add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        return b.sign(ca_k, hashes.SHA256())

    ca_k = chiave()
    ca_nome = nome("CA DI PROVA Varco (FVG finto)")
    ca_c = emetti("CA DI PROVA Varco (FVG finto)", ca_k, ca_k, ca_nome, ca=True)
    ca_p, _ = scrivi("ca", ca_c, ca_k)
    srv_k = chiave()
    srv_c = emetti("127.0.0.1", srv_k, ca_k, ca_nome,
                   san=[x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))],
                   eku=[ExtendedKeyUsageOID.SERVER_AUTH])
    srv_p, srv_kp = scrivi("server", srv_c, srv_k)
    client = {}
    for cf in cf_medici:
        k = chiave()
        c = emetti(f"{cf}/0000000000000000.PROVA", k, ca_k, ca_nome, eku=[ExtendedKeyUsageOID.CLIENT_AUTH])
        client[cf] = scrivi(f"carta-{cf}", c, k)
    cif_k = chiave()
    cif_c = emetti("CERTIFICATO DI CIFRATURA DI PROVA (non Insiel)", cif_k, cif_k, nome("CERTIFICATO DI CIFRATURA DI PROVA (non Insiel)"))
    cif_p, cif_kp = scrivi("cifratura", cif_c, cif_k)
    return MaterialeTLS(ca_p, srv_p, srv_kp, client, cif_p, cif_kp.read_bytes())


# ------------------------------------------------------------------ risposte sintetiche per la suite


def risposte_sintetiche() -> dict[str, bytes]:
    """Le buste di conformita/risposte/fvg/ (scritte dalla riga di comando: vedi sotto)."""
    r = RicettaFinta("0600A0000000001", "PROVAX00X00X000Y", None, "F", "2026-10-01 11:00:00", "0" * 29 + "1",
                     "2026-10-01 11:00:01", [[("codGruppoEquival", "G3B"), ("descrGruppoEquival", "GRUPPO DI PROVA"), ("quantita", "1")]])
    r_sost = RicettaFinta("0600A0000000002", "PROVAX00X00X000Y", "PROVAX00X00X000Z", "F", "2026-10-01 11:05:00", "0" * 29 + "2",
                          "2026-10-01 11:05:01", r.righe)
    return {
        "invio_ok.xml": ricevuta_invio("0000", r.nre, r.codice_aut, r.data_inserimento),
        "invio_ok_dpc_0196.xml": ricevuta_invio("0000", "0600A0000000003", "0" * 29 + "3", "2026-10-01 11:10:01",
                                               comunicazioni=[("0196", "CONTIENE FARMACI IN DPC NELLA REGIONE DI PRESCRIZIONE")]),
        "invio_rifiuto_1020.xml": ricevuta_invio("9999", errori=[("1020", "nota AIFA non valida", "1", "E")]),
        "invio_avviso_0001.xml": ricevuta_invio("0001", "0600A0000000004", "0" * 29 + "4", "2026-10-01 11:15:01",
                                               errori=[("1024", "avviso di prova", "0", "W")]),
        "invio_ok_nota_amb01.xml": ricevuta_invio("0000", "0600A0000000005", "0" * 29 + "5", "2026-10-01 11:20:01",
                                                 note=[("1", "88.39.9", "AMB01")]),
        "invio_downgrade_060120.xml": ricevuta_invio("9999", errori=[("060120", "prestazione non ancora attiva in dematerializzata", "1", "E")]),
        "invio_downgrade_fault_060125.xml": fault("060125 prestazione non ancora attiva in dematerializzata"),
        "visualizza_ok.xml": ricevuta_visualizza("0000", r),
        "visualizza_sostituto.xml": ricevuta_visualizza("0000", r_sost),
        "visualizza_nre_inesistente_5005.xml": ricevuta_visualizza("9999", errori=[("5005", "NRE inesistente", "0", "E")]),
        "annulla_ok.xml": ricevuta_annulla("0000", r.nre),
        "annulla_rifiuto_1120.xml": ricevuta_annulla("9999", r.nre, errori=[("1120", "ricetta non annullabile", "0", "E")]),
        "verifica_sostituto_abilitato.xml": ricevuta_verifica_sostituto("PROVAX00X00X000Y", "PROVAX00X00X000Z", "101", True),
        "verifica_sostituto_non_abilitato.xml": ricevuta_verifica_sostituto("PROVAX00X00X000Y", "PROVAX00X00X000Z", "101", False,
                                                                           "sostituto non abilitato"),
        "verifica_sostituto_errore.xml": ricevuta_verifica_sostituto("PROVAX00X00X000Y", "PROVAX00X00X000Y", "101", False,
                                                                     errori=[("1212", "titolare e sostituto coincidono", "0", "E")]),
        "interroga_nre_ok.xml": ricevuta_interroga_nre("0000", [r]),
    }


if __name__ == "__main__":
    import sys

    destinazione = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "conformita" / "risposte" / "fvg")
    destinazione.mkdir(parents=True, exist_ok=True)
    for nome_file, dati in risposte_sintetiche().items():
        (destinazione / nome_file).write_bytes(dati)
        print(destinazione / nome_file)
