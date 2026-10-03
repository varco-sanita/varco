# SPDX-License-Identifier: EUPL-1.2
"""SIRPED FINTO, in locale, per provare il client del kit senza chiamare la Regione Piemonte né il CSI.

Non è SIRPED e non ne imita la logica sanitaria. Le specifiche (REL-STC-01 V04, RE-SRS-SAR V05, piano
dei test SIRPED-TES-01 V02) non pubblicano NESSUNA risposta né un codice d'errore dei servizi di
prescrizione con autenticazione forte: le buste e i testi d'errore qui sono SCRITTI DA NOI.
Le ricevute di prescrizione sono nel tracciato del MEF («il tracciato del messaggio di risposta sarà
quello adottato dal SAC», RE-SRS-SAR, par. 3.5.1); quelle di CreateAuth/CheckToken/RevokeAuth negli XSD
del kit A2F del Sistema TS.

Cosa controlla davvero, come dice la specifica:
  SOAP di prescrizione (percorsi del SAC, scelti da noi: quelli di SIRPED non sono pubblicati)
    - modalità MAIL (par. 4.1.1, 4.2.5): `Authorization: Basic` con un utente RUPAR noto, header
      `X-idSessione: Bearer <UUID>` rilasciato da CreateAuth a QUELL'utente e QUEL gestionale, non
      scaduto né revocato, `X-Gestionale` censito; `pinCode` che si decifra col certificato della
      Regione (di prova) nel pincode dell'utente;
    - modalità OAUTH2 (par. 4.3.6): `X-OAuth2-Authorization: Bearer <JWT>` firmato da questo server,
      non scaduto, Id-Sessione non revocato, scope «prescrizione»; NESSUN header `Authorization`;
      `pinCode` vuoto;
    - il corpo valida contro gli XSD del MEF inclusi nel kit; `codiceAss` si decifra in un CF;
    - facoltativo (`controllo_cf_prescrittore=True`): il CF del prescrittore (cfMedico2, altrimenti
      cfMedico1) è quello dell'utente autenticato, come farà SIRPED in produzione (SIRPED-TES-01, par. 3.2).
  CreateAuth/CheckToken/RevokeAuth (par. 4.2): SOAPAction del WSDL A2F, XSD A2F (se gli si passa la
  cartella), Basic RUPAR, userId = utente della Basic, pincode, cfUtente, codRegione 010,
  contesto RICETTA-DEM, APP censita, permessi concessi = richiesti ∩ profili del configuratore (finto).
  Una nuova richiesta invalida l'Id-Sessione precedente (cap. 3). In ambiente di test l'Id-Sessione
  torna nelle comunicazioni (par. 4.2.1, V04).
  OAuth2 (par. 4.3): /oauth2/authorize (il «consenso» lo dà il server per l'utente di prova: niente
  SPID), /oauth2/token con PKCE S256 (RFC 7636), /.well-known/jwks.json, /sessionid/verify,
  /sessionid/revoke (DELETE come nel testo, GET come nell'esempio e nel YAML).
  Revisione esterna del 02/10/2026, controlli aggiunti dalla specifica:
    - scope concessi = richiesti ∩ profili dell'utente sul configuratore (p. 22-23: i profili sono
      quelli degli `utenti` con quel CF); intersezione vuota -> access_denied;
    - il code resta legato all'utente che ha autorizzato: lo scambio dà il token a LUI;
    - CheckToken e RevokeAuth: l'Id-Sessione vale per la coppia utente-gestionale (cap. 3, 4.2):
      un altro gestionale (APP) riceve «non rilasciato»;
    - /sessionid/revoke con un JWT scaduto -> 401 (p. 36), anche se l'Id-Sessione è valido.
  Giro 2 di revisione:
    - `nbf` nel futuro (p. 30): JWT rifiutato nel SAR, 401 in verify e revoke;
    - l'Id-Sessione del JWT deve essere della coppia `sub`/`aud` del token (p. 30, cap. 3): un JWT di A
      con l'Id-Sessione di B o di un altro gestionale è «non rilasciato» nel SAR e 401 in verify/revoke;
    - SAR con sessione scaduta e JWT valido: «Id-Sessione scaduto»;
    - redirect_uri con query: il ritorno aggiunge `&code=...`, non un secondo `?`.

Regole di merito FINTE (codici visti nel SAC di test): nota AIFA "999" -> 9999/1020; NRE inesistente ->
5005; secondo annullamento -> 1120; annullamento di un altro medico -> 1125.

Uso dai test: `with ServerPiemonte(...) as s: s.url`. Solo libreria standard, cryptography e
(facoltativo, per gli XSD A2F) lxml.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import http.server
import itertools
import json
import math
import secrets
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from xml.sax.saxutils import escape

NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_TIPI = "http://tipodati.xsd.dem.sanita.finanze.it"
NS_RIC = {
    "InvioPrescrittoRicevuta": "http://invioprescrittoricevuta.xsd.dem.sanita.finanze.it",
    "VisualizzaPrescrittoRicevuta": "http://visualizzaprescrittoricevuta.xsd.dem.sanita.finanze.it",
    "AnnullaPrescrittoRicevuta": "http://annullaprescrittoricevuta.xsd.dem.sanita.finanze.it",
    "InterrogaNreUtilRicevuta": "http://interroganreutilricevuta.xsd.dem.sanita.finanze.it",
}
NS_AUT = "http://authservice.xsd.wsdl.auth.a2f.sts.sanita.finanze.it"
NS_DAT = "http://datatype.xsd.wsdl.auth.a2f.sts.sanita.finanze.it"

PERCORSO_A2F = "/a2f-auth-ws/soap/v1/authentication-service"
PREFISSO_OAUTH = "/reloauthserver"
# percorso -> (radice attesa, SOAPAction del WSDL MEF)
ENDPOINT_SAR = {
    "/DemRicettaPrescrittoServicesWeb/services/demInvioPrescritto": (
        "InvioPrescrittoRichiesta", "http://invioprescritto.wsdl.dem.sanita.finanze.it/InvioPrescritto"),
    "/DemRicettaPrescrittoServicesWeb/services/demVisualizzaPrescritto": (
        "VisualizzaPrescrittoRichiesta", "http://visualizzaprescritto.wsdl.dem.sanita.finanze.it/VisualizzaPrescritto"),
    "/DemRicettaPrescrittoServicesWeb/services/demAnnullaPrescritto": (
        "AnnullaPrescrittoRichiesta", "http://annullaprescritto.wsdl.dem.sanita.finanze.it/AnnullaPrescritto"),
    "/DemRicettaInterrogazioniServicesWeb/services/demInterrogaNreUtilizzati": (
        "InterrogaNreUtilRichiesta", "http://interroganreassociati.wsdl.dem.sanita.finanze.it/InterrogaNreUtilizzati"),
}
A2F_OPERAZIONI = {
    "http://wsdl.auth.a2f.sts.sanita.finanze.it/create": "CreateAuthReq",
    "http://wsdl.auth.a2f.sts.sanita.finanze.it/checkToken": "CheckTokenReq",
    "http://wsdl.auth.a2f.sts.sanita.finanze.it/revoke": "RevokeAuthReq",
}
XSD_A2F = "sts-a2f-service.v0.1.xsd"

# Testi d'errore SCRITTI DA NOI sulle descrizioni del piano dei test SIRPED-TES-01 V02 (i codici veri
# non sono pubblicati). Le autenticazioni RUPAR seguono RE-SRS-SAR, par. 4.1.
ERR_CREDENZIALI = "Credenziali invalide (from client)"
ERR_SENZA_ID_SESSIONE = "L'autenticazione con credenziale RUPAR richiede l'invio dell'Id-Sessione"
ERR_ID_NON_RILASCIATO = "Id-Sessione non rilasciato dal sistema regionale"
ERR_ID_SCADUTO = "Id-Sessione scaduto"
ERR_ID_REVOCATO = "Id-Sessione revocato"
ERR_SENZA_GESTIONALE = "Codice del gestionale non indicato"
ERR_GESTIONALE_IGNOTO = "Gestionale non censito nel sistema regionale"
ERR_JWT = "Token JWT non valido"
ERR_BASIC_CON_JWT = "Con il token JWT non e' consentita la basic authentication"


def _ora_xml(t: float) -> str:
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


# ------------------------------------------------------------------ buste di risposta


def busta(corpo: str) -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="{NS_SOAP}">'
            f"<soapenv:Body>{corpo}</soapenv:Body></soapenv:Envelope>").encode("utf-8")


def fault(testo: str, codice: str = "soapenv:Client") -> bytes:
    return busta(f"<soapenv:Fault><faultcode>{codice}</faultcode><faultstring>{escape(testo)}</faultstring></soapenv:Fault>")


def _x(tag: str, valore: str | None, pre: str = "") -> str:
    return "" if valore is None else f"<{pre}{tag}>{escape(valore)}</{pre}{tag}>"


def _errori(errori, codice_ok: str = "0000") -> str:
    if not errori:
        errori = [(codice_ok, "Operazione eseguita correttamente", "0", None)]
    righe = "".join(f"<td:ErroreRicetta>{_x('codEsito', c, 'td:')}{_x('esito', t, 'td:')}{_x('progPresc', p, 'td:')}"
                    f"{_x('tipoErrore', g, 'td:')}</td:ErroreRicetta>" for c, t, p, g in errori)
    return f"<r:ElencoErroriRicette>{righe}</r:ElencoErroriRicette>"


def _comunicazioni(com) -> str:
    righe = "".join(f"<td:Comunicazione>{_x('codice', c, 'td:')}{_x('messaggio', m, 'td:')}</td:Comunicazione>" for c, m in com)
    return f"<r:ElencoComunicazioni>{righe}</r:ElencoComunicazioni>" if com else ""


def _radice(nome: str, interno: str) -> str:
    return f'<r:{nome} xmlns:r="{NS_RIC[nome]}" xmlns:td="{NS_TIPI}">{interno}</r:{nome}>'


def ricevuta_invio(codice: str, nre=None, codice_aut=None, data=None, errori=()) -> bytes:
    interno = (_x("nre", nre, "r:") + _x("codAutenticazione", codice_aut, "r:") + _x("dataInserimento", data, "r:")
               + _x("codEsitoInserimento", codice, "r:") + _errori(list(errori))
               + _comunicazioni([("0100", "Nessuna comunicazione.")]))
    return busta(_radice("InvioPrescrittoRicevuta", interno))


def ricevuta_visualizza(codice: str, r: "RicettaFinta | None" = None, errori=()) -> bytes:
    interno = ""
    if r is not None:
        righe = "".join("<td:DettaglioPrescrizione>" + "".join(_x(t, v, "td:") for t, v in riga) + "</td:DettaglioPrescrizione>"
                        for riga in r.righe)
        interno = (_x("nre", r.nre, "r:") + _x("cfMedico1", r.titolare, "r:") + _x("cfMedico2", r.sostituto, "r:")
                   + _x("codRegione", "010", "r:") + _x("tipoPrescrizione", r.tipo, "r:")
                   + _x("dataCompilazione", r.data_compilazione, "r:")
                   + f"<r:ElencoDettagliPrescrizioni>{righe}</r:ElencoDettagliPrescrizioni>"
                   + _x("statoProcesso", r.stato, "r:") + _x("codAutenticazione", r.codice_aut, "r:")
                   + _x("dataInserimento", r.data_inserimento, "r:"))
    interno += _x("codEsitoVisualizzazione", codice, "r:") + _errori(list(errori))
    return busta(_radice("VisualizzaPrescrittoRicevuta", interno))


def ricevuta_annulla(codice: str, nre: str, errori=()) -> bytes:
    return busta(_radice("AnnullaPrescrittoRicevuta", _x("nre", nre, "r:") + _x("codEsitoAnnullamento", codice, "r:")
                         + _errori(list(errori))))


def ricevuta_interroga_nre(codice: str, record) -> bytes:
    righe = "".join(
        "<td:NreUtilRecord>" + _x("nre", r.nre, "td:") + _x("cfMedico", r.titolare, "td:") + _x("tipoPrescrizione", r.tipo, "td:")
        + _x("dataCompilazioneRicetta", r.data_compilazione, "td:") + _x("provenienza", "0", "td:")
        + _x("codAutenticazione", r.codice_aut, "td:") + "</td:NreUtilRecord>"
        for r in record
    )
    elenco = f"<r:ElencoNreUtilRecord>{righe}</r:ElencoNreUtilRecord>" if record else ""
    return busta(_radice("InterrogaNreUtilRicevuta", _x("codEsitoInterrogaNreUtilizzati", codice, "r:") + elenco + _errori([])))


def risposta_a2f(nome: str, codice: str, *, errori=(), info=(), comunicazioni=(), info_token=None) -> bytes:
    """CreateAuthRes / CheckTokenRes / RevokeAuthRes nell'ordine dello XSD A2F."""
    parti = [_x("codEsito", codice, "a:")]
    if errori:
        parti.append("<a:errori>" + "".join(
            f"<d:errore>{_x('tipoErrore', t, 'd:')}{_x('codEsito', c, 'd:')}{_x('descrEsito', s, 'd:')}</d:errore>"
            for t, c, s in errori) + "</a:errori>")
    if info_token is not None:
        stato, descr, ini, fine = info_token
        parti.append(f"<a:infoToken>{_x('stato', str(stato), 'd:')}{_x('descrizione', descr, 'd:')}"
                     f"{_x('dataInizioValidita', ini, 'd:')}{_x('dataFineValidita', fine, 'd:')}</a:infoToken>")
    for chiave, valore in info[:1]:  # lo XSD ammette UN solo <info>
        parti.append(f"<a:info>{_x('chiave', chiave, 'd:')}{_x('valore', valore, 'd:')}</a:info>")
    if comunicazioni:
        parti.append("<a:comunicazioni>" + "".join(
            f"<d:comunicazione>{_x('codice', c, 'd:')}{_x('messaggio', m, 'd:')}</d:comunicazione>" for c, m in comunicazioni
        ) + "</a:comunicazioni>")
    return busta(f'<a:{nome} xmlns:a="{NS_AUT}" xmlns:d="{NS_DAT}">{"".join(parti)}</a:{nome}>')


# ------------------------------------------------------------------ stato


@dataclass
class UtenteRupar:
    password: str
    pincode: str
    cf: str
    mail_certificata: bool = True
    profili: frozenset[str] = frozenset({"prescrizione"})


@dataclass
class IdSessione:
    valore: str
    utente: str  # utente RUPAR, oppure CF per OAuth2
    gestionale: str
    permessi: tuple[str, ...]
    inizio: float
    fine: float
    revocato_alle: float | None = None


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


@dataclass
class StatoPiemonte:
    ricette: dict[str, RicettaFinta] = field(default_factory=dict)
    sessioni: dict[str, IdSessione] = field(default_factory=dict)
    codici: dict[str, dict] = field(default_factory=dict)  # authorization code -> dati
    richieste: list[dict] = field(default_factory=list)
    contatore: itertools.count = field(default_factory=lambda: itertools.count(1))


def _locale(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _figli(el: ET.Element) -> dict[str, str]:
    return {_locale(c.tag): (c.text or "").strip() for c in el}


class _Gestore(http.server.BaseHTTPRequestHandler):
    server_version = "SIRPEDFinto/0.1"

    def log_message(self, *a):
        pass

    def _rispondi(self, codice: int, corpo: bytes, tipo: str = "text/xml;charset=UTF-8", extra=None) -> None:
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def _gestisci(self, metodo: str):
        s: ServerPiemonte = self.server.finto  # type: ignore[attr-defined]
        corpo = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        s.stato.richieste.append({"metodo": metodo, "percorso": self.path, "intestazioni": dict(self.headers.items())})
        try:
            esito = s.gestisci(metodo, self.path, self.headers, corpo)
        except Exception as e:  # il server finto non deve mai cadere in silenzio
            esito = (500, fault(f"errore del server finto: {e!r}", "soapenv:Server"))
        self._rispondi(*esito)

    def do_POST(self):  # noqa: N802
        self._gestisci("POST")

    def do_GET(self):  # noqa: N802
        self._gestisci("GET")

    def do_DELETE(self):  # noqa: N802
        self._gestisci("DELETE")


class ServerPiemonte:
    """Server HTTP su 127.0.0.1. Vedi la docstring del modulo."""

    def __init__(self, *, chiave_cifratura_pem: bytes, utenti: dict[str, UtenteRupar], gestionali: dict[str, set[str]],
                 redirect_uri: set[str] = frozenset(), utente_oauth2: str | None = None,
                 ambiente_test: bool = True, durata_s: int = 8 * 3600, controllo_cf_prescrittore: bool = False,
                 xsd_a2f: Path | None = None):
        """`gestionali`: APP (`<codice>_<azienda>`) -> permessi del gestionale. `utente_oauth2`: CF dell'utente
        che «autorizza» nel browser finto. `xsd_a2f`: cartella `wsdl` del kit A2F del Sistema TS."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        self._chiave_cf = serialization.load_pem_private_key(chiave_cifratura_pem, password=None)
        self._chiave_jwt = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "rel-oauth2-key"
        self.utenti = dict(utenti)
        self.gestionali = {k: set(v) for k, v in gestionali.items()}
        self.redirect_uri = set(redirect_uri)
        self.utente_oauth2 = utente_oauth2
        self.ambiente_test = ambiente_test
        self.durata_s = durata_s
        self.controllo_cf_prescrittore = controllo_cf_prescrittore
        self.xsd_a2f = Path(xsd_a2f) if xsd_a2f else None
        self._schema_a2f = None
        self.spostamento_s = 0.0  # orologio del server: i test lo spostano in avanti
        self.stato = StatoPiemonte()
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Gestore)
        self._httpd.finto = self  # type: ignore[attr-defined]

    def adesso(self) -> float:
        return time.time() + self.spostamento_s

    def profili_di(self, cf: str) -> frozenset[str]:
        """I profili sul configuratore regionale (finto) dell'utente con quel CF."""
        return frozenset().union(*(u.profili for u in self.utenti.values() if u.cf == cf))

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    @property
    def url_oauth(self) -> str:
        return self.url + PREFISSO_OAUTH

    def __enter__(self) -> "ServerPiemonte":
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    # ------------------------------------------------------------------ utilità

    def _decifra(self, cifrato: str) -> str | None:
        from cryptography.hazmat.primitives.asymmetric import padding

        try:
            return self._chiave_cf.decrypt(base64.b64decode(cifrato, validate=True), padding.PKCS1v15()).decode("ascii")
        except (ValueError, UnicodeDecodeError):
            return None

    def _basic(self, intestazioni) -> tuple[str, UtenteRupar] | None:
        h = intestazioni.get("Authorization") or ""
        if not h.startswith("Basic "):
            return None
        try:
            utente, _, password = base64.b64decode(h[6:]).decode().partition(":")
        except Exception:
            return None
        u = self.utenti.get(utente)
        if u is None or not secrets.compare_digest(u.password, password):
            return None
        return utente, u

    def _sessione(self, valore: str) -> tuple[IdSessione | None, str | None]:
        s = self.stato.sessioni.get(valore)
        if s is None:
            return None, ERR_ID_NON_RILASCIATO
        if s.revocato_alle is not None:
            return s, ERR_ID_REVOCATO
        if self.adesso() >= s.fine:
            return s, ERR_ID_SCADUTO
        return s, None

    def _nuova_sessione(self, utente: str, gestionale: str, permessi: tuple[str, ...]) -> IdSessione:
        # «ad ogni richiesta di un nuovo Id-sessione viene inibita la validità di quello precedente» (cap. 3)
        adesso = self.adesso()
        for vecchia in self.stato.sessioni.values():
            if vecchia.utente == utente and vecchia.gestionale == gestionale and vecchia.revocato_alle is None:
                vecchia.revocato_alle = adesso
        s = IdSessione(str(uuid.uuid4()), utente, gestionale, permessi, adesso, adesso + self.durata_s)
        self.stato.sessioni[s.valore] = s
        return s

    # ------------------------------------------------------------------ JWT

    def _firma(self, payload: dict) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        testa = _b64url(json.dumps({"kid": self.kid, "alg": "RS256", "typ": "JWT"}).encode())
        corpo = _b64url(json.dumps(payload).encode())
        firma = self._chiave_jwt.sign(f"{testa}.{corpo}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{testa}.{corpo}.{_b64url(firma)}"

    def jwt_di_prova(self, **sovrascritti) -> str:
        """Un JWT firmato da questo server, per i test (es. scaduto, altro utente)."""
        adesso = int(self.adesso())
        payload = {"sub": self.utente_oauth2, "aud": next(iter(self.gestionali)), "nbf": adesso, "iat": adesso,
                   "exp": adesso + self.durata_s, "iss": self.url_oauth, "jti": str(uuid.uuid4()), "scope": "prescrizione",
                   "userData": {"cfutente": self.utente_oauth2, "idSessione": str(uuid.uuid4()), "scope": "prescrizione"}}
        payload.update(sovrascritti)
        return self._firma(payload)

    def _leggi_jwt(self, token: str) -> dict | None:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        try:
            testa, corpo, firma = token.split(".")
            self._chiave_jwt.public_key().verify(base64.urlsafe_b64decode(firma + "=" * (-len(firma) % 4)),
                                                 f"{testa}.{corpo}".encode(), padding.PKCS1v15(), hashes.SHA256())
            return json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))
        except (ValueError, InvalidSignature):
            return None

    def _jwt_nel_tempo(self, dati: dict) -> bool:
        """exp e nbf numerici e l'istante attuale tra i due (REL-STC-01, p. 30: nbf «Istante in cui il
        token diventa valido»; p. 31: exp «Scadenza del token»). Prima nbf non si guardava (giro 2, N1)."""
        exp, nbf = dati.get("exp"), dati.get("nbf", 0)
        numeri = all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (exp, nbf))
        return numeri and nbf <= self.adesso() < exp

    def _sessione_del_jwt(self, dati: dict) -> IdSessione | None:
        """L'Id-Sessione del JWT, solo se è della stessa coppia utente-gestionale del token: `sub` è il
        CF «dell'utente a cui il token è stato assegnato», `aud` il gestionale «per cui il token è stato
        rilasciato» (REL-STC-01, p. 30), e l'Id-Sessione vale per utente e gestionale (cap. 3, par. 4.2).
        Prima un JWT di A con l'Id-Sessione di B, di un altro gestionale, passava (giro 2, N2)."""
        sess = self.stato.sessioni.get((dati.get("userData") or {}).get("idSessione", ""))
        if sess is None or sess.utente != dati.get("sub") or sess.gestionale != dati.get("aud"):
            return None
        return sess

    def jwks(self) -> dict:
        n = self._chiave_jwt.public_key().public_numbers().n
        return {"keys": [{"kty": "RSA", "e": "AQAB", "kid": self.kid,
                          "n": _b64url(n.to_bytes((n.bit_length() + 7) // 8, "big"))}]}

    # ------------------------------------------------------------------ smistamento

    def gestisci(self, metodo: str, percorso: str, intestazioni, corpo: bytes):
        p = urlparse(percorso)
        if p.path.startswith(PREFISSO_OAUTH + "/"):
            return self._oauth(metodo, p.path[len(PREFISSO_OAUTH):], parse_qs(p.query), intestazioni, corpo)
        if metodo != "POST":
            return 405, b"metodo non ammesso"
        if p.path == PERCORSO_A2F:
            return self._a2f(intestazioni, corpo)
        if p.path in ENDPOINT_SAR:
            return self._sar(p.path, intestazioni, corpo)
        return 404, b"percorso sconosciuto"

    @staticmethod
    def _body(corpo: bytes) -> ET.Element | None:
        radice = ET.fromstring(corpo)
        body = radice.find(f"{{{NS_SOAP}}}Body")
        return body[0] if body is not None and len(body) == 1 else None

    # ------------------------------------------------------------------ prescrizione (SAR)

    def _autentica_sar(self, intestazioni, f: dict) -> tuple[str | None, bytes | None]:
        """CF dell'utente autenticato, oppure un fault."""
        jwt = intestazioni.get("X-OAuth2-Authorization")
        if jwt is not None:
            if intestazioni.get("Authorization"):
                return None, fault(ERR_BASIC_CON_JWT)
            if not jwt.startswith("Bearer "):
                return None, fault(ERR_JWT)
            dati = self._leggi_jwt(jwt[7:].strip())
            if dati is None or not self._jwt_nel_tempo(dati) or dati.get("aud") not in self.gestionali:
                return None, fault(ERR_JWT)
            sess = self._sessione_del_jwt(dati)
            if sess is None or sess.revocato_alle is not None:
                return None, fault(ERR_ID_REVOCATO if sess else ERR_ID_NON_RILASCIATO)
            if self.adesso() >= sess.fine:  # sessione scaduta anche se il JWT non lo è (giro 2, N2)
                return None, fault(ERR_ID_SCADUTO)
            if "prescrizione" not in (dati.get("scope") or "").split():
                return None, fault("Permesso di prescrizione non concesso")
            if f.get("pinCode"):
                return None, fault("Con il token JWT il pinCode deve essere vuoto")
            return dati["sub"], None
        auth = self._basic(intestazioni)
        if auth is None:
            return None, fault(ERR_CREDENZIALI)
        utente, u = auth
        h = intestazioni.get("X-idSessione")
        if not h:
            return None, fault(ERR_SENZA_ID_SESSIONE)
        sess, errore = self._sessione(h[7:].strip() if h.startswith("Bearer ") else "")
        if errore:
            return None, fault(errore)
        app = intestazioni.get("X-Gestionale")
        if not app:
            return None, fault(ERR_SENZA_GESTIONALE)
        if app not in self.gestionali:
            return None, fault(ERR_GESTIONALE_IGNOTO)
        if sess.utente != utente or sess.gestionale != app or "prescrizione" not in sess.permessi:
            return None, fault(ERR_ID_NON_RILASCIATO)
        if self._decifra(f.get("pinCode") or "") != u.pincode:
            return None, fault("Pincode non congruente con l'utente")
        return u.cf, None

    def _sar(self, percorso: str, intestazioni, corpo: bytes):
        atteso, azione = ENDPOINT_SAR[percorso]
        if intestazioni.get("SOAPAction") != f'"{azione}"':
            return 500, fault(f"SOAPAction inattesa: {intestazioni.get('SOAPAction')!r}")
        el = self._body(corpo)
        if el is None or _locale(el.tag) != atteso:
            return 500, fault(f"radice inattesa, serve {atteso}")
        from varco.schemi import errori_xsd

        problemi = errori_xsd(ET.tostring(el))
        if problemi:
            return 500, fault("cvc: " + "; ".join(problemi)[:500])
        f = _figli(el)
        cf, errore = self._autentica_sar(intestazioni, f)
        if errore:
            return 500, errore
        return {
            "InvioPrescrittoRichiesta": self._invio,
            "VisualizzaPrescrittoRichiesta": self._visualizza,
            "AnnullaPrescrittoRichiesta": self._annulla,
            "InterrogaNreUtilRichiesta": self._interroga,
        }[atteso](el, f, cf)

    def _invio(self, el, f, cf):
        inviante = f.get("cfMedico2") or f.get("cfMedico1")
        if self.controllo_cf_prescrittore and inviante != cf:
            return 200, ricevuta_invio("9999", errori=[("1212", "prescrittore diverso dall'utente autenticato", "0", "E")])
        if f.get("codiceAss") and len(self._decifra(f["codiceAss"]) or "") != 16:
            return 200, ricevuta_invio("9999", errori=[("1001", "codice assistito non valido", "0", "E")])
        righe = [{_locale(c.tag): (c.text or "").strip() for c in d}
                 for d in next(c for c in el if _locale(c.tag) == "ElencoDettagliPrescrizioni")]
        if any(r.get("notaProd") == "999" for r in righe):
            return 200, ricevuta_invio("9999", errori=[("1020", "nota AIFA non valida", "1", "E")])
        n = next(self.stato.contatore)
        nre = f.get("nre") or f"0100A{n:010d}"
        if nre in self.stato.ricette:
            return 200, ricevuta_invio("9999", errori=[("1021", "NRE già utilizzato", "0", "E")])
        adesso = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        r = RicettaFinta(nre, f["cfMedico1"], f.get("cfMedico2") or None, f["tipoPrescrizione"], f["dataCompilazione"],
                         f"{n:030d}", adesso, [[(k, v) for k, v in riga.items() if v] for riga in righe])
        self.stato.ricette[nre] = r
        return 200, ricevuta_invio("0000", nre, r.codice_aut, adesso)

    def _visualizza(self, el, f, cf):
        r = self.stato.ricette.get(f["nre"])
        if r is None:
            return 200, ricevuta_visualizza("9999", errori=[("5005", "NRE inesistente", "0", "E")])
        return 200, ricevuta_visualizza("0000", r)

    def _annulla(self, el, f, cf):
        r = self.stato.ricette.get(f["nre"])
        if r is None:
            return 200, ricevuta_annulla("9999", f["nre"], errori=[("5005", "NRE inesistente", "0", "E")])
        if cf != (r.sostituto or r.titolare):
            return 200, ricevuta_annulla("9999", f["nre"], errori=[("1125", "annullamento non consentito", "0", "E")])
        if r.stato != "3":
            return 200, ricevuta_annulla("9999", f["nre"], errori=[("1120", "ricetta non annullabile", "0", "E")])
        r.stato = "4"
        return 200, ricevuta_annulla("0000", f["nre"])

    def _interroga(self, el, f, cf):
        trovate = [r for r in self.stato.ricette.values() if r.titolare == f["cfMedico"]
                   and (not f.get("nre") or r.nre == f["nre"]) and (not f.get("tipoPrescr") or r.tipo == f["tipoPrescr"])]
        return 200, ricevuta_interroga_nre("0000", trovate)

    # ------------------------------------------------------------------ CreateAuth / CheckToken / RevokeAuth

    def _schema(self):
        if self.xsd_a2f is None:
            return None
        if self._schema_a2f is None:
            from lxml import etree

            self._schema_a2f = etree.XMLSchema(etree.parse(str(self.xsd_a2f / XSD_A2F)))
        return self._schema_a2f

    def _a2f(self, intestazioni, corpo: bytes):
        azione = (intestazioni.get("SOAPAction") or "").strip('"')
        atteso = A2F_OPERAZIONI.get(azione)
        if atteso is None:
            return 500, fault(f"SOAPAction inattesa: {azione!r}")
        el = self._body(corpo)
        if el is None or _locale(el.tag) != atteso:
            return 500, fault(f"radice inattesa, serve {atteso}")
        schema = self._schema()
        if schema is not None:
            from lxml import etree

            if not schema.validate(etree.fromstring(ET.tostring(el))):
                return 500, fault("cvc: " + "; ".join(e.message for e in schema.error_log)[:500])
        nome_ris = atteso.replace("Req", "Res")
        no = lambda testo: (200, risposta_a2f(nome_ris, "1", errori=[("E", "9998", testo)]))  # noqa: E731
        auth = self._basic(intestazioni)
        if auth is None:
            return 500, fault(ERR_CREDENZIALI)
        utente, u = auth
        f = _figli(el)
        ident = {_locale(c.tag): (c.text or "").strip() for c in next((c for c in el if _locale(c.tag) == "identificativo"), [])}
        app = None
        for opz in el.iter():
            if _locale(opz.tag) == "opzione":
                o = _figli(opz)
                if o.get("chiave") == "APP":
                    app = o.get("valore")
        if f.get("userId") != utente or f.get("cfUtente") != u.cf:
            return no("Utente non coerente con le credenziali")
        if ident.get("tipo") != "P" or self._decifra(ident.get("valore") or "") != u.pincode:
            return no("Pincode non valido")
        if f.get("contesto") != "RICETTA-DEM":
            return no("Contesto non valido")
        if not app:
            return no(ERR_SENZA_GESTIONALE)
        if app not in self.gestionali:
            return no(ERR_GESTIONALE_IGNOTO)
        if atteso == "CreateAuthReq":
            if f.get("codRegione") != "010":
                return no("Codice regione non valido")
            richiesti = tuple((f.get("applicazione") or "").split())
            if not richiesti or "prescrizione" in richiesti and "prescrizione" not in self.gestionali[app]:
                return no("Il gestionale non ha i diritti richiesti")
            concessi = tuple(p for p in richiesti if p in u.profili)
            if not concessi:
                return no("Utente non abilitato sul configuratore regionale")
            if not u.mail_certificata:
                return no("Mail non certificata sul PUA")
            s = self._nuova_sessione(utente, app, concessi)
            com = []
            if self.ambiente_test:  # REL-STC-01 V04, par. 4.2.1: solo in test
                com = [("permessi", " ".join(concessi)), ("token", s.valore),
                       ("dataFineValidita", _dt.datetime.fromtimestamp(s.fine).strftime("%d/%m/%Y %H:%M:%S")),
                       ("Working-mode", "TEST")]
            return 200, risposta_a2f(nome_ris, "0", info=[("emailStatus", "Email con token inviata con successo al notificatore regionale")],
                                     comunicazioni=com)
        sess = self.stato.sessioni.get(f.get("token", ""))
        com_test = [("Working-mode", "TEST")] if self.ambiente_test else []
        # l'Id-Sessione è «assegnato ad ogni utente-gestionale-Azienda» (par. 4.2, 4.3): un altro gestionale
        # non lo vede né lo revoca
        if sess is None or sess.utente != utente or sess.gestionale != app:
            return no(ERR_ID_NON_RILASCIATO)
        if atteso == "CheckTokenReq":
            if sess.revocato_alle is not None:
                stato = (1, "Revocato")
            elif self.adesso() >= sess.fine:
                stato = (2, "Scaduto")
            else:
                stato = (0, "Valido")
            return 200, risposta_a2f(nome_ris, "0", info_token=(*stato, _ora_xml(sess.inizio), _ora_xml(sess.fine)),
                                     comunicazioni=com_test)
        # RevokeAuthReq
        if sess.revocato_alle is not None:
            quando = _dt.datetime.fromtimestamp(sess.revocato_alle).strftime("%d/%m/%Y %H:%M:%S")
            return 200, risposta_a2f(nome_ris, "1", errori=[("E", "9998", ERR_ID_REVOCATO)],
                                     info=[("lastRevokePreviousDate", quando)], comunicazioni=com_test)
        if self.adesso() >= sess.fine:
            quando = _dt.datetime.fromtimestamp(sess.fine).strftime("%d/%m/%Y %H:%M:%S")
            return 200, risposta_a2f(nome_ris, "1", errori=[("E", "9998", ERR_ID_SCADUTO)], info=[("expiredDate", quando)],
                                     comunicazioni=com_test)
        sess.revocato_alle = self.adesso()
        return 200, risposta_a2f(nome_ris, "0", info=[("revokeStatus", "Revoca del token eseguita correttamente")],
                                 comunicazioni=com_test)

    # ------------------------------------------------------------------ OAuth2

    def _oauth(self, metodo: str, percorso: str, q: dict, intestazioni, corpo: bytes):
        json_ = lambda codice, dati: (codice, json.dumps(dati).encode(), "application/json")  # noqa: E731
        uno = lambda d, k: (d.get(k) or [""])[0]  # noqa: E731
        if percorso == "/oauth2/authorize" and metodo == "GET":
            redirect = uno(q, "redirect_uri")
            if redirect not in self.redirect_uri:
                return 400, b"Invalid_redirect_uri"
            # la redirect_uri può avere già una query (es. ?tenant=301): i parametri si aggiungono con «&»,
            # non con un secondo «?» (revisione esterna giro 2, N4)
            unione = "&" if urlparse(redirect).query else "?"
            ritorno = lambda **p: (302, b"", "text/plain", {"Location": f"{redirect}{unione}{urlencode(p)}"})  # noqa: E731
            state = uno(q, "state")
            if uno(q, "client_id") not in self.gestionali:
                return ritorno(error="invalid_client", state=state)
            if uno(q, "response_type") != "code" or uno(q, "code_challenge_method") != "S256" or not uno(q, "code_challenge"):
                return ritorno(error="invalid_request", state=state)
            scope = tuple(uno(q, "scope").split())
            if not scope or any(s not in ("prescrizione", "erogazione", "presa_in_carico") for s in scope):
                return ritorno(error="invalid_scope", state=state)
            if self.utente_oauth2 is None:
                return ritorno(error="access_denied", error_description="L'utente non possiede le abilitazioni", state=state)
            # p. 23: «i permessi corrispondono all'intersezione tra i valori indicati nel parametro scope e
            # quelli presenti sul configuratore regionale»
            concessi = tuple(x for x in scope if x in self.profili_di(self.utente_oauth2))
            if not concessi:
                return ritorno(error="access_denied", error_description="L'utente non possiede le abilitazioni", state=state)
            # SIRPED-TES-01-V02 p. 26, A2F-OAU2-TOK-N-03: un gestionale censito ma senza il diritto richiesto
            # riceve «una segnalazione di errore». I permessi del token sono anche quelli del GESTIONALE, non
            # solo dell'utente (issue #9); l'errore è quello del REL-STC-01 p. 27 per un client non abilitato.
            concessi = tuple(x for x in concessi if x in self.gestionali[uno(q, "client_id")])
            if not concessi:
                return ritorno(error="unauthorized_client",
                               error_description="Il gestionale non possiede i diritti richiesti", state=state)
            code = secrets.token_urlsafe(24)
            self.stato.codici[code] = {"client_id": uno(q, "client_id"), "redirect_uri": redirect, "scope": concessi,
                                        "challenge": uno(q, "code_challenge"), "scade": self.adesso() + 300, "usato": False,
                                        "utente": self.utente_oauth2}  # il code è di chi ha autorizzato
            return ritorno(code=code, state=state)
        if percorso == "/oauth2/token" and metodo == "POST":
            f = {k: v[0] for k, v in parse_qs(corpo.decode("ascii")).items()}
            dati = self.stato.codici.get(f.get("code", ""))
            if f.get("grant_type") != "authorization_code":
                return json_(400, {"error": "unsupported_grant_type"})
            if dati is None or dati["usato"] or self.adesso() >= dati["scade"]:
                return json_(400, {"error": "invalid_grant"})
            dati["usato"] = True
            if f.get("client_id") != dati["client_id"] or f.get("redirect_uri") != dati["redirect_uri"]:
                return json_(400, {"error": "invalid_grant"})
            verifier = f.get("code_verifier", "")
            if not 43 <= len(verifier) <= 128 or _b64url(hashlib.sha256(verifier.encode()).digest()) != dati["challenge"]:
                return json_(400, {"error": "invalid_client"})  # «code_challenge e code_verifier non corrispondenti»
            scope = " ".join(dati["scope"])
            utente = dati["utente"]  # non self.utente_oauth2: nel frattempo può essere cambiato
            sess = self._nuova_sessione(utente, dati["client_id"], dati["scope"])
            adesso = int(self.adesso())
            token = self._firma({
                "sub": utente, "aud": dati["client_id"], "nbf": adesso, "iat": adesso,
                "exp": int(sess.fine), "iss": self.url_oauth, "jti": str(uuid.uuid4()), "scope": scope,
                "userData": {"cfutente": utente, "idSessione": sess.valore,
                             "autenticazioneTs": _dt.datetime.fromtimestamp(adesso).strftime("%d/%m/%Y %H:%M.%S.0000"),
                             "livelloAautenticazione": "iso-iec-29115-LoA3", "modAautenticazione": "SpidL2",
                             "organizzazione": dati["client_id"].rsplit("_", 1)[-1], "scope": scope,
                             "clientid": dati["client_id"]},
            })
            return json_(200, {"access_token": token, "scope": scope, "token_type": "Bearer",
                               "client_id": dati["client_id"], "expires_in": int(sess.fine - adesso)})
        if percorso == "/.well-known/jwks.json" and metodo == "GET":
            return json_(200, self.jwks())
        if percorso in ("/sessionid/verify", "/sessionid/revoke"):
            if percorso == "/sessionid/verify" and metodo != "GET" or percorso == "/sessionid/revoke" and metodo not in ("GET", "DELETE"):
                return 405, b"metodo non ammesso", "text/plain"
            h = intestazioni.get("Authorization") or ""
            dati = self._leggi_jwt(h[7:].strip()) if h.startswith("Bearer ") else None
            if dati is None:
                return 401, b"", "application/json"
            if uno(q, "client_id") not in self.gestionali or uno(q, "client_id") != dati.get("aud"):
                return json_(500, {"errore": {"codEsito": "9998", "tipoErrore": "E",
                                              "descrEsito": "Identificativo del gestionale non riconosciuto"}})
            if uno(q, "cfutente") != dati.get("sub"):
                return 401, b"", "application/json"
            exp, nbf = dati.get("exp"), dati.get("nbf", 0)
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (exp, nbf)):
                # NaN o infinito: i confronti sarebbero sempre falsi, né «futuro» né «scaduto» (issue #11)
                return 401, b"", "application/json"
            if self.adesso() < nbf:
                return 401, b"", "application/json"  # token non ancora valido (p. 30): non riconosciuto
            sess = self._sessione_del_jwt(dati)
            if sess is None:
                return 401, b"", "application/json"
            if percorso == "/sessionid/verify":
                stato = (1, "Revocato") if sess.revocato_alle is not None else (2, "Scaduto") if self.adesso() >= sess.fine else (0, "Valido")
                return json_(200, {"infoToken": {"stato": stato[0], "descrizione": stato[1],
                                                 "dataInizioValidita": _ora_xml(sess.inizio), "dataFineValidita": _ora_xml(sess.fine)}})
            if not self._jwt_nel_tempo(dati):
                return 401, b"", "application/json"  # p. 36: «il token jwt fornito è scaduto»
            if sess.revocato_alle is not None or self.adesso() >= sess.fine:
                return 401, b"", "application/json"
            sess.revocato_alle = self.adesso()
            return 200, b"", "application/json"
        return 404, b"percorso sconosciuto", "text/plain"


# ------------------------------------------------------------------ materiale di prova


@dataclass(frozen=True)
class MaterialeRegione:
    certificato_cifratura_pem: bytes  # il certificato «della Regione» per cifrare pincode e CF: DI PROVA
    chiave_cifratura_pem: bytes


def materiale_regione(bit: int = 1024) -> MaterialeRegione:
    """Certificato autofirmato di prova al posto di quello (non pubblico) che la Regione dà ai fornitori.

    1024 bit come SanitelCF: con chiavi oltre 1536 bit il pincode cifrato non sta nei 256 caratteri
    di `identificativo/valore` dello XSD A2F (i test lo provano con 2048)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    k = rsa.generate_private_key(public_exponent=65537, key_size=bit)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VARCO PROVA - certificato di cifratura SIRPED FINTO")])
    adesso = _dt.datetime.now(_dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(adesso - _dt.timedelta(days=1))
            .not_valid_after(adesso + _dt.timedelta(days=30)).sign(k, hashes.SHA256()))
    return MaterialeRegione(
        cert.public_bytes(serialization.Encoding.PEM),
        k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
    )


# ------------------------------------------------------------------ risposte sintetiche per la suite


def risposte_sintetiche() -> dict[str, bytes]:
    """Le buste di conformita/risposte/piemonte/ (A2F). Valori di prova, fissi."""
    token = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    return {
        "crea_ok_test.xml": risposta_a2f(
            "CreateAuthRes", "0", info=[("emailStatus", "Email con token inviata con successo al notificatore regionale")],
            comunicazioni=[("permessi", "prescrizione"), ("token", token), ("dataFineValidita", "01/10/2026 19:00:00"),
                           ("Working-mode", "TEST")]),
        "crea_ok_produzione.xml": risposta_a2f(
            "CreateAuthRes", "0", info=[("emailStatus", "Email con token inviata con successo al notificatore regionale")]),
        "crea_token_senza_working_mode.xml": risposta_a2f(
            "CreateAuthRes", "0", info=[("emailStatus", "Email con token inviata con successo al notificatore regionale")],
            comunicazioni=[("token", token)]),
        "crea_gestionale_ignoto.xml": risposta_a2f("CreateAuthRes", "1", errori=[("E", "9998", ERR_GESTIONALE_IGNOTO)]),
        "crea_avviso.xml": risposta_a2f("CreateAuthRes", "0", errori=[("W", "0001", "avviso di prova")],
                                        comunicazioni=[("permessi", "prescrizione"), ("token", token), ("Working-mode", "TEST")]),
        "crea_fatale.xml": risposta_a2f("CreateAuthRes", "1", errori=[("F", "9999", "errore fatale di prova")]),
        "verifica_valido.xml": risposta_a2f("CheckTokenRes", "0", info_token=(0, "Valido", "2026-10-01T09:00:00.000Z",
                                                                              "2026-10-01T17:00:00.000Z")),
        "verifica_scaduto.xml": risposta_a2f("CheckTokenRes", "0", info_token=(2, "Scaduto", "2026-09-30T09:00:00.000Z",
                                                                               "2026-09-30T17:00:00.000Z")),
        "verifica_revocato.xml": risposta_a2f("CheckTokenRes", "0", info_token=(1, "Revocato", "2026-10-01T09:00:00.000Z",
                                                                                "2026-10-01T17:00:00.000Z")),
        "revoca_ok.xml": risposta_a2f("RevokeAuthRes", "0", info=[("revokeStatus", "Revoca del token eseguita correttamente")]),
        "revoca_gia_revocato.xml": risposta_a2f("RevokeAuthRes", "1", errori=[("E", "9998", ERR_ID_REVOCATO)],
                                                info=[("lastRevokePreviousDate", "30/05/2025 20:51:07")]),
        # forma dell'esempio del par. 4.2.4: <errore> senza il contenitore <errori> (NON valida contro lo XSD)
        "crea_errore_forma_esempio.xml": busta(
            f'<a:CreateAuthRes xmlns:a="{NS_AUT}"><a:codEsito>1</a:codEsito><a:errore><a:tipoErrore>E</a:tipoErrore>'
            f"<a:codEsito>9998</a:codEsito><a:descrEsito>Errore di configurazione nella chiamata al servizio</a:descrEsito>"
            f"</a:errore></a:CreateAuthRes>"),
    }


def jwt_sintetici() -> dict[str, bytes]:
    """JWKS e JWT di prova per i casi PIE-4xx. La chiave è NUOVA a ogni esecuzione: i file cambiano a
    ogni rigenerazione, i casi ne controllano la coerenza (firma, campi), non i byte."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    srv = object.__new__(ServerPiemonte)  # solo per firmare: niente socket
    srv._chiave_jwt = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    srv.kid = "rel-oauth2-key"
    payload = {"sub": "PROVAX00X00X000Y", "aud": "VARCO_301", "nbf": 1790000000, "iat": 1790000000, "exp": 4102444800,
               "iss": "https://esempio.invalid/reloauthserver", "jti": "8d3c2b1a-0000-4000-8000-000000000001",
               "scope": "prescrizione",
               "userData": {"cfutente": "PROVAX00X00X000Y", "idSessione": "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                            "autenticazioneTs": "01/10/2026 11:00.00.0000", "livelloAautenticazione": "iso-iec-29115-LoA3",
                            "modAautenticazione": "SpidL2", "organizzazione": "301", "scope": "prescrizione",
                            "clientid": "VARCO_301"}}
    valido = srv._firma(payload)
    testa, _, firma = valido.split(".")
    alterato_payload = dict(payload, sub="PROVAX00X00X000Z", userData=dict(payload["userData"], cfutente="PROVAX00X00X000Z"))
    alterato = f"{testa}.{_b64url(json.dumps(alterato_payload).encode())}.{firma}"
    jwks = srv.jwks()
    jwks_v = {"keys": [{"kty": "RSA", "e": "AQAB", "kid": "rel-oauth2-key", "v": jwks["keys"][0]["n"]}]}
    return {
        "jwks.json": json.dumps(jwks, indent=2).encode() + b"\n",
        "jwks_campo_v.json": json.dumps(jwks_v, indent=2).encode() + b"\n",
        "jwt_valido.txt": valido.encode() + b"\n",
        "jwt_alterato.txt": alterato.encode() + b"\n",
    }


if __name__ == "__main__":
    argomenti = [a for a in sys.argv[1:] if not a.startswith("--")]
    destinazione = Path(argomenti[0] if argomenti else Path(__file__).resolve().parent.parent / "conformita" / "risposte" / "piemonte")
    destinazione.mkdir(parents=True, exist_ok=True)
    tutte = risposte_sintetiche()
    if "--anche-jwt" in sys.argv:
        tutte |= jwt_sintetici()
    for nome_file, dati in tutte.items():
        (destinazione / nome_file).write_bytes(dati)
        print(destinazione / nome_file)
