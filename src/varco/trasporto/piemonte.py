# SPDX-License-Identifier: EUPL-1.2
"""Canale verso SIRPED, il SAR della Regione Piemonte (gestito dal CSI Piemonte).

Fonti, tutte pubbliche (servizi.regione.piemonte.it, scheda «SIRPED», sezione Documentazione):
  - *Accesso ai servizi delle ricette dematerializzate mediante autenticazione forte*,
    REL-STC-01 **V04 del 02/03/2026** (in breve REL-STC-01);
  - *Specifiche dei requisiti di integrazione SAR - Cartelle cliniche MMG/PLS*, RE-SRS-SAR V05 dell'08/05/2018;
  - per CreateAuth/CheckToken/RevokeAuth, i WSDL e gli XSD del *Kit per lo sviluppo A2F* del Sistema TS
    (ver. 20250902): REL-STC-01, par. 4.2, dice che sono «gli stessi previsti da Sistema TS».

Il SAR piemontese parla il tracciato del SAC «in analogia al SAC» (RE-SRS-SAR, par. 4.4): stessi
messaggi, stessi namespace del MEF (l'esempio del par. 4.3.6 usa
`http://invioprescrittorichiesta.xsd.dem.sanita.finanze.it`). Cosa cambia sta qui, nel canale:

  - credenziali **RUPAR Piemonte** (utente, password, pincode) al posto di quelle del Sistema TS
    (RE-SRS-SAR, par. 3.1.1 e 6.2); pincode e CF dell'assistito cifrati con un **certificato della
    Regione**, non con SanitelCF (par. 3.1.2 e 6.4): il canale non cifra, cifra il servizio;
  - **secondo fattore** (REL-STC-01, cap. 3-4), due modalità, una sola per gestionale:
      * MAIL: l'Id-Sessione (UUID) lo chiede il gestionale con CreateAuth e arriva per mail al medico.
        Header `Authorization: Basic` (RUPAR), `X-idSessione: Bearer <Id-Sessione>`,
        `X-Gestionale: <codice>_<azienda>` (par. 4.2.5);
      * OAUTH2: Authorization Code con PKCE (trasporto/piemonte_oauth2.py). Header
        `X-OAuth2-Authorization: Bearer <JWT>`, **niente** Basic, `pinCode` vuoto nel corpo (par. 4.3.6);
  - SOAPAction: quelle dei WSDL del MEF (le stesse del SAC);
  - **nessun endpoint pubblicato**: gli URL di test li dà il CSI con l'autocertificazione, quelli di
    produzione dopo. Il canale vuole gli URL espliciti, servizio per servizio.

Il canale si rifiuta di partire verso un host non locale senza un'`AdesionePiemonte`: il codice del
gestionale «viene rilasciato da Regione Piemonte al momento dell'avvio dell'attività di
autocertificazione» (REL-STC-01, par. 4.2.1), non da questo kit.

Scritto e verificato sulle specifiche: NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from typing import Callable
from urllib.parse import urlparse

from ..ambienti import Endpoint
from ..credenziali import Credenziali
from ..errori import ConfigurazioneNonValida
from .http import Richiesta, Trasporto, TrasportoHTTP, consegna
from .sac import RispostaGrezza
from .soap import imbusta, sbusta

# REL-STC-01, par. 4.2.1: «codRegione ... valorizzare con 010»
CODICE_REGIONE_PIEMONTE = "010"
# Permessi dell'Id-Sessione (campo `applicazione`) e scope OAuth2 (par. 4.2.1 e 4.3.1)
PERMESSI = ("prescrizione", "erogazione", "presa_in_carico")
# Margine sugli orologi per `nbf`: RFC 7519, par. 4.1.5, ammette «some small leeway, usually no more
# than a few minutes». Un token che diventa valido fra un'ora resta rifiutato (giro 2, N1).
MARGINE_NBF_S = 60

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_CF = re.compile(r"^[A-Z0-9]{16}$")


class ModalitaPiemonte(str, Enum):
    MAIL = "mail"  # REL-STC-01, par. 3.1 e 4.2: Id-Sessione via mail certificata, credenziali RUPAR
    OAUTH2 = "oauth2"  # REL-STC-01, par. 3.2 e 4.3: JWT via Authorization Code + PKCE


class ServizioPiemonte(str, Enum):
    INVIO = "invio"
    VISUALIZZA = "visualizza"
    ANNULLA = "annulla"
    INTERROGA_NRE = "interroga_nre"
    ID_SESSIONE = "id_sessione"  # CreateAuth, CheckToken, RevokeAuth (solo modalità MAIL)


# SOAPAction dei WSDL del MEF (kit prescrittore e kit A2F): il SAR usa gli stessi.
SOAP_ACTION = {
    ServizioPiemonte.INVIO: "http://invioprescritto.wsdl.dem.sanita.finanze.it/InvioPrescritto",
    ServizioPiemonte.VISUALIZZA: "http://visualizzaprescritto.wsdl.dem.sanita.finanze.it/VisualizzaPrescritto",
    ServizioPiemonte.ANNULLA: "http://annullaprescritto.wsdl.dem.sanita.finanze.it/AnnullaPrescritto",
    ServizioPiemonte.INTERROGA_NRE: "http://interroganreassociati.wsdl.dem.sanita.finanze.it/InterrogaNreUtilizzati",
}
SOAP_ACTION_A2F = {
    "create": "http://wsdl.auth.a2f.sts.sanita.finanze.it/create",
    "checkToken": "http://wsdl.auth.a2f.sts.sanita.finanze.it/checkToken",
    "revoke": "http://wsdl.auth.a2f.sts.sanita.finanze.it/revoke",
}

# Percorsi verso un server LOCALE (strumenti/piemonte_server_finto.py). Scelti da noi: quelli del SAC
# e del servizio A2F del Sistema TS, perché i percorsi di SIRPED non sono pubblicati.
PERCORSI_LOCALI = {
    ServizioPiemonte.INVIO: Endpoint.INVIO,
    ServizioPiemonte.VISUALIZZA: Endpoint.VISUALIZZA,
    ServizioPiemonte.ANNULLA: Endpoint.ANNULLA,
    ServizioPiemonte.INTERROGA_NRE: Endpoint.INTERROGA_NRE,
    ServizioPiemonte.ID_SESSIONE: "/a2f-auth-ws/soap/v1/authentication-service",
}


@dataclass(frozen=True)
class GestionalePiemonte:
    """Il valore di `X-Gestionale`, di `infoAggiuntive` APP e del `client_id` OAuth2 (REL-STC-01, par. 4.2.1):
    «codice del gestionale richiedente seguito da "_XXX", dove XXX è il codice dell'Azienda a cui
    appartiene il gestionale» (esempio: `MIOAPPLICATIVO_301`).

    `codice`: lo rilascia la Regione all'avvio dell'autocertificazione.
    `azienda`: codice a 3 caratteri dell'Azienda (ASR, o codice ARPE per le private). Per il
    gestionale di un MMG la specifica non dice quale Azienda indicare: va chiesto (docs/SAR_PIEMONTE.md).
    """

    codice: str
    azienda: str

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", self.codice or ""):
            raise ConfigurazioneNonValida(f"codice del gestionale non valido: {self.codice!r} (niente spazi né '_')")
        if not re.fullmatch(r"[0-9A-Za-z]{3}", self.azienda or ""):
            raise ConfigurazioneNonValida(f"codice dell'Azienda: servono 3 caratteri, non {self.azienda!r}")

    @property
    def valore(self) -> str:
        return f"{self.codice}_{self.azienda}"


@dataclass(frozen=True)
class AdesionePiemonte:
    """Prova che è partita l'autocertificazione con la Regione: senza, il canale non chiama SIRPED.

    `riferimento`: protocollo o riferimento della richiesta inviata a supporto.sar@csi.it
    (processo SIRPED-01 V01, par. 3, punto 1).
    """

    riferimento: str
    gestionale: GestionalePiemonte

    def __post_init__(self):
        if not (self.riferimento or "").strip():
            raise ConfigurazioneNonValida("AdesionePiemonte: serve il riferimento della richiesta di autocertificazione")


def _locale(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


def controlla_id_sessione(valore: str) -> str:
    """L'Id-Sessione è un UUID (REL-STC-01, par. 4.2.1; `tokenType` dello XSD A2F: 36 caratteri)."""
    v = (valore or "").strip()
    if not _UUID.fullmatch(v):
        raise ConfigurazioneNonValida("Id-Sessione non valido: SIRPED rilascia un UUID di 36 caratteri")
    return v


# ------------------------------------------------------------------ JWT (letto, non verificato)


@dataclass(frozen=True)
class ContenutoJWT:
    """Il payload del JWT di SIRPED (REL-STC-01, par. 4.3.2), letto SENZA verificare la firma.

    Serve ai controlli locali (chi è l'utente, quando scade, quali permessi). La verifica della firma
    con la chiave del servizio JWKS sta in `piemonte_oauth2.verifica_firma`.
    """

    intestazione: dict
    payload: dict

    @property
    def sub(self) -> str | None:
        return self.payload.get("sub")

    @property
    def cf_utente(self) -> str | None:
        """`sub`, oppure `userData.cfutente` («valorizzato come il campo sub»)."""
        return (self.sub or self._user().get("cfutente") or None)

    @property
    def id_sessione(self) -> str | None:
        return self._user().get("idSessione")

    @property
    def scope(self) -> tuple[str, ...]:
        s = self.payload.get("scope") or self._user().get("scope") or ""
        return tuple(s.split()) if isinstance(s, str) else tuple(s)

    @property
    def scadenza(self) -> int | None:
        """`exp` (REL-STC-01, p. 31: Unix time). Assente: None. Presente ma non numerico (`"1"`,
        `true`): ConfigurazioneNonValida, non «mai scaduto» (revisione esterna 02/10/2026)."""
        return self._istante("exp")

    @property
    def valido_dal(self) -> int | None:
        """`nbf` (REL-STC-01, p. 30: «Istante in cui il token diventa valido», Unix time). Assente: None.
        Revisione esterna giro 2, 5-sar-piemonte N1: prima nessuno lo guardava."""
        return self._istante("nbf")

    def _istante(self, nome: str) -> int | None:
        """Un istante Unix del payload: assente None; non numerico (`"1"`, `true`) o non finito
        (`Infinity`, che json accetta) ConfigurazioneNonValida, non «mai» (giro 2, N5: OverflowError)."""
        if nome not in self.payload:
            return None
        v = self.payload[nome]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ConfigurazioneNonValida(f"JWT con {nome} non numerico o non finito: {v!r}")
        return int(v)

    def non_ancora_valido(self, adesso: float | None = None) -> bool:
        """nbf nel futuro, oltre il margine per gli orologi non allineati (RFC 7519, par. 4.1.5)."""
        nbf = self.valido_dal
        return nbf is not None and (adesso if adesso is not None else time.time()) + MARGINE_NBF_S < nbf

    def scaduto(self, adesso: float | None = None) -> bool:
        exp = self.scadenza
        return exp is not None and (adesso if adesso is not None else time.time()) >= exp

    def _user(self) -> dict:
        u = self.payload.get("userData")
        return u if isinstance(u, dict) else {}


def _b64url(parte: str) -> bytes:
    return base64.urlsafe_b64decode(parte + "=" * (-len(parte) % 4))


def leggi_jwt(token: str) -> ContenutoJWT:
    """Decodifica `<Header>.<Payload>.<Signature>` senza verificare la firma."""
    if token is not None and not isinstance(token, str):
        raise ConfigurazioneNonValida(f"token JWT non valido: è un {type(token).__name__}, non una stringa")
    parti = (token or "").strip().split(".")
    if len(parti) != 3 or not all(parti[:2]):
        raise ConfigurazioneNonValida("token JWT non valido: servono tre parti separate da '.'")
    try:
        intestazione, payload = json.loads(_b64url(parti[0])), json.loads(_b64url(parti[1]))
    except (ValueError, binascii.Error) as e:
        raise ConfigurazioneNonValida(f"token JWT non leggibile: {e}") from None
    if not isinstance(intestazione, dict) or not isinstance(payload, dict):
        raise ConfigurazioneNonValida("token JWT non valido: intestazione e payload devono essere oggetti JSON")
    return ContenutoJWT(intestazione, payload)


# ------------------------------------------------------------------ canale


class CanalePiemonte:
    """Canale SOAP verso SIRPED. Una modalità per canale, come per gestionale (richiesta di autocertificazione)."""

    def __init__(
        self,
        modalita: ModalitaPiemonte,
        *,
        credenziali: Credenziali | None = None,
        id_sessione: Callable[[], str] | None = None,
        token_jwt: Callable[[], str] | None = None,
        cf_medico: str | None = None,
        adesione: AdesionePiemonte | None = None,
        gestionale_di_prova: GestionalePiemonte | None = None,
        url: dict[ServizioPiemonte, str] | None = None,
        base_url: str | None = None,
        trasporto: Trasporto | None = None,
    ):
        """
        Modalità MAIL: `credenziali` RUPAR (utente, password, pincode in chiaro, `cf_medico`) e
        `id_sessione`, una funzione che restituisce l'Id-Sessione arrivato per mail (il kit non legge
        la posta: lo chiede al medico chi integra). L'Id-Sessione si chiede con `ServizioIdSessione.crea`.

        Modalità OAUTH2: `token_jwt`, una funzione che restituisce il `TokenPiemonte` di
        `ClientOAuth2Piemonte.scambia_codice` (meglio: porta anche la scadenza da `expires_in`) oppure
        la sola stringa JWT, e `cf_medico`. Niente credenziali RUPAR: la specifica vieta la Basic in
        questa modalità. Il canale legge il JWT (la firma la verifica `scambia_codice`) e si rifiuta
        di partire se è scaduto, se la scadenza non si conosce (né `exp` né `expires_in`), se manca il
        permesso «prescrizione» o se è di un altro medico: in produzione SIRPED controlla che il CF
        del prescrittore sia quello del token (piano dei test SIRPED-TES-01 V02, par. 3.2).

        `url`: endpoint per servizio, dal kit di test del CSI. `base_url`: solo verso localhost.
        """
        self.modalita = ModalitaPiemonte(modalita)
        self.trasporto = trasporto or TrasportoHTTP()
        if base_url is not None:
            if not _locale(base_url):
                raise ConfigurazioneNonValida("base_url vale solo verso localhost: verso SIRPED si passano gli URL "
                                              "ricevuti dal CSI con `url={ServizioPiemonte.INVIO: ...}`")
            base = base_url.rstrip("/")
            self.url = {s: base + p for s, p in PERCORSI_LOCALI.items()}
        else:
            self.url = {}
        self.url.update(url or {})
        verso_regione = any(not _locale(u) for u in self.url.values())
        if verso_regione:
            if adesione is None:
                raise ConfigurazioneNonValida(
                    "Verso SIRPED serve un'AdesionePiemonte: il codice del gestionale e gli URL li dà la Regione, "
                    "tramite il CSI, con l'autocertificazione"
                )
            if gestionale_di_prova is not None:
                raise ConfigurazioneNonValida("gestionale_di_prova vale solo verso localhost")
            gestionale = adesione.gestionale
        else:
            gestionale = adesione.gestionale if adesione else gestionale_di_prova
            if gestionale is None:
                raise ConfigurazioneNonValida("serve un'AdesionePiemonte o, verso localhost, un gestionale_di_prova")
        self.adesione = adesione
        self.gestionale = gestionale

        if self.modalita is ModalitaPiemonte.MAIL:
            if credenziali is None:
                raise ConfigurazioneNonValida("modalità MAIL: servono le credenziali RUPAR (utente, password, pincode)")
            if token_jwt is not None:
                raise ConfigurazioneNonValida("modalità MAIL: il JWT vale solo nella modalità OAUTH2")
            if len(credenziali.utente) > 16:
                raise ConfigurazioneNonValida("utente RUPAR oltre 16 caratteri: lo XSD A2F (userId) non lo ammette")
            self.credenziali = credenziali
            self.cf_medico = credenziali.cf.upper()
            self._id_sessione = id_sessione
        else:
            if credenziali is not None:
                raise ConfigurazioneNonValida(
                    "modalità OAUTH2: niente credenziali RUPAR, la Basic va tolta (REL-STC-01, par. 4.3.6)")
            if token_jwt is None or not cf_medico:
                raise ConfigurazioneNonValida("modalità OAUTH2: servono token_jwt e cf_medico")
            if id_sessione is not None:
                raise ConfigurazioneNonValida("modalità OAUTH2: l'Id-Sessione sta dentro il JWT, non si passa a parte")
            self.credenziali = None
            self.cf_medico = cf_medico.upper()
            self._id_sessione = None
        if not _CF.fullmatch(self.cf_medico):
            raise ConfigurazioneNonValida(f"codice fiscale del medico non valido: {self.cf_medico!r}")
        self._token_jwt = token_jwt

    # ------------------------------------------------------------------

    def url_di(self, servizio: ServizioPiemonte) -> str:
        try:
            return self.url[servizio]
        except KeyError:
            raise ConfigurazioneNonValida(
                f"nessun URL per {servizio.value}: SIRPED non pubblica gli endpoint, vanno presi dal kit del CSI "
                f"e passati con url={{ServizioPiemonte.{servizio.name}: ...}}"
            ) from None

    def _basic(self) -> str:
        c = self.credenziali
        return "Basic " + base64.b64encode(f"{c.utente}:{c.password}".encode()).decode()

    def _token_corrente(self) -> tuple[str, float | None]:
        """(JWT, scadenza nota fuori dal JWT). `token_jwt` può dare la stringa o un `TokenPiemonte`."""
        t = self._token_jwt()
        if isinstance(t, str):
            return t.strip(), None
        access = getattr(t, "access_token", None)
        if not isinstance(access, str):
            raise ConfigurazioneNonValida("token_jwt deve restituire il JWT o un TokenPiemonte")
        scadenza = getattr(t, "scadenza", None)
        return access.strip(), (float(scadenza) if isinstance(scadenza, (int, float)) and not isinstance(scadenza, bool) else None)

    def jwt(self, token: str | None = None, scadenza: float | None = None, adesso: float | None = None) -> ContenutoJWT:
        """Il JWT (quello corrente, se non lo si passa), letto e controllato: scadenza nota e non
        passata, intestato al medico del canale. La scadenza è la più vicina tra `exp` del JWT e quella
        del `TokenPiemonte` (da `expires_in`). La firma NON si verifica qui: la verifica `scambia_codice`."""
        if self._token_jwt is None:
            raise ConfigurazioneNonValida("il JWT esiste solo nella modalità OAUTH2")
        if token is None:
            token, scadenza = self._token_corrente()
        contenuto = leggi_jwt(token)
        scadenze = [x for x in (contenuto.scadenza, scadenza) if x is not None]
        if not scadenze:
            raise ConfigurazioneNonValida("JWT senza exp e senza scadenza nota: passare a token_jwt il TokenPiemonte di "
                                          "scambia_codice, che conosce expires_in (REL-STC-01, par. 4.3.2)")
        if (adesso if adesso is not None else time.time()) >= min(scadenze):
            raise ConfigurazioneNonValida("JWT scaduto: SIRPED non concede il refresh, serve una nuova autorizzazione "
                                          "del medico (REL-STC-01, par. 4.3.2)")
        if contenuto.non_ancora_valido(adesso):
            raise ConfigurazioneNonValida(f"JWT non ancora valido: nbf {contenuto.valido_dal} nel futuro (REL-STC-01, p. 30)")
        cf = (contenuto.cf_utente or "").upper()
        dichiarato = str(contenuto._user().get("cfutente") or "").upper()
        if contenuto.sub and dichiarato and dichiarato != contenuto.sub.upper():
            raise ConfigurazioneNonValida("JWT incoerente: sub e userData.cfutente indicano utenti diversi "
                                          "(REL-STC-01, par. 4.3.2: cfutente è «valorizzato come il campo sub»)")
        if cf != self.cf_medico:
            raise ConfigurazioneNonValida(f"il JWT è di {cf or 'un utente non indicato'}, il medico del canale è "
                                          f"{self.cf_medico}")
        return contenuto

    def intestazioni(self, servizio: ServizioPiemonte, soap_action: str) -> dict[str, str]:
        h = {
            "Content-Type": "text/xml;charset=UTF-8",
            "SOAPAction": f'"{soap_action}"',
            "User-Agent": "varco/0.1 (+EUPL-1.2)",
        }
        if self.modalita is ModalitaPiemonte.OAUTH2:
            token, scadenza = self._token_corrente()
            contenuto = self.jwt(token, scadenza)  # controlli locali prima di mandare il token
            if servizio is not ServizioPiemonte.ID_SESSIONE and "prescrizione" not in contenuto.scope:
                # scope = «permessi effettivamente concessi» (REL-STC-01, p. 29-30): senza, SIRPED rifiuta
                raise ConfigurazioneNonValida(f"il JWT non concede il permesso «prescrizione» (scope: "
                                              f"{' '.join(contenuto.scope) or 'nessuno'})")
            h["X-OAuth2-Authorization"] = f"Bearer {token}"
            return h
        h["Authorization"] = self._basic()
        if servizio is not ServizioPiemonte.ID_SESSIONE:
            # CreateAuth/CheckToken/RevokeAuth: solo credenziali RUPAR; l'APP sta nel corpo (infoAggiuntive)
            if self._id_sessione is None:
                raise ConfigurazioneNonValida("modalità MAIL: manca l'Id-Sessione (id_sessione=...), da chiedere "
                                              "con CreateAuth: arriva per mail al medico")
            h["X-idSessione"] = f"Bearer {controlla_id_sessione(self._id_sessione())}"
            h["X-Gestionale"] = self.gestionale.valore
        return h

    def chiama(self, servizio: ServizioPiemonte, corpo: ET.Element, soap_action: str | None = None
               ) -> tuple[ET.Element, RispostaGrezza]:
        if servizio is ServizioPiemonte.ID_SESSIONE and self.modalita is not ModalitaPiemonte.MAIL:
            raise ConfigurazioneNonValida("CreateAuth/CheckToken/RevokeAuth valgono solo nella modalità MAIL")
        azione = soap_action or SOAP_ACTION[servizio]
        richiesta = Richiesta(
            servizio=f"piemonte.{servizio.value}",
            url=self.url_di(servizio),
            corpo=imbusta(corpo),
            intestazioni=self.intestazioni(servizio, azione),
        )
        risposta = consegna(self.trasporto, richiesta)  # guardia anche con un trasporto proprio
        grezza = RispostaGrezza(richiesta.corpo, risposta.corpo, risposta.stato_http, risposta.durata_s)
        return sbusta(risposta.corpo, risposta.stato_http), grezza
