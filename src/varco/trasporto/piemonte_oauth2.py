# SPDX-License-Identifier: EUPL-1.2
"""Id-Sessione di SIRPED in modalità OAuth2: Authorization Code con PKCE (REL-STC-01 V04, par. 4.3).

Il flusso:
  1. il gestionale apre nel BROWSER del medico `GET /oauth2/authorize` con client_id, redirect_uri,
     scope, state e code_challenge (S256). Il kit costruisce l'URL (`ClientOAuth2Piemonte.autorizzazione`):
     non apre browser, non tiene un server di callback (li deve dare chi integra);
  2. il medico entra con SPID/CIE/TS-CNS/CNS di livello 2 o più, sceglie ruolo e collocazione e
     autorizza; il browser torna alla redirect_uri con `code` e `state` (o con `error`);
  3. `leggi_callback` controlla lo `state` (CSRF) e prende il `code`;
  4. `scambia_codice`: `POST /oauth2/token` (grant_type=authorization_code, code, redirect_uri,
     client_id, code_verifier) → access_token (JWT). Niente refresh_token: alla scadenza serve una
     nuova autorizzazione del medico (par. 4.3.2);
  5. il JWT va nell'header `X-OAuth2-Authorization: Bearer <jwt>` di ogni chiamata SOAP (par. 4.3.6).

Servizi regionali in più (YAML «api-docs_idsessione.yaml» del 22/10/2025): `/sessionid/verify` e
`/sessionid/revoke`, con `Authorization: Bearer <jwt>`; chiave pubblica su `/.well-known/jwks.json`.

L'host dell'Authorization Server NON è pubblicato (nel YAML: `https://tbd/reloauthserver`): si passa
esplicito, e la guardia del trasporto lo tratta come ogni host della Regione o del CSI.

Scritto e verificato sulle specifiche: NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urlparse

from ..ambienti import verifica_url_consentito
from ..errori import ConfigurazioneNonValida, ErroreKit, ErroreTrasporto
from .http import Richiesta, Trasporto, TrasportoHTTP, consegna, permessi_del_trasporto
from .piemonte import PERMESSI, AdesionePiemonte, ContenutoJWT, GestionalePiemonte, _locale, leggi_jwt

# Lo scope del cittadino (CUP Unico Regionale) non riguarda un MMG: il kit non lo chiede.
SCOPE_AMMESSI = PERMESSI


class ErroreAutorizzazione(ErroreKit):
    """La callback dell'Authorization Server riporta `error` (par. 4.3.1), o lo state non torna."""

    def __init__(self, codice: str, descrizione: str | None = None):
        self.codice = codice
        self.descrizione = descrizione
        super().__init__(f"autorizzazione negata o non valida: {codice}" + (f" ({descrizione})" if descrizione else ""))


def _b64url(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).rstrip(b"=").decode("ascii")


def challenge_s256(verifier: str) -> str:
    """code_challenge = BASE64URL(SHA256(ASCII(code_verifier))), RFC 7636 par. 4.2.

    NB: l'esempio in shell del par. 4.3.1 toglie «+» e «/» invece di tradurli in «-» e «_»: dà un
    valore diverso da questo in circa 3 casi su 4 (docs/SAR_PIEMONTE.md, sez. 7). Il kit segue la RFC.
    """
    _controlla_verifier(verifier)
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


_NON_RISERVATI = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _controlla_verifier(verifier: str) -> None:
    if not 43 <= len(verifier or "") <= 128 or not set(verifier) <= _NON_RISERVATI:
        raise ConfigurazioneNonValida("code_verifier: 43-128 caratteri tra A-Z a-z 0-9 - . _ ~ (RFC 7636, par. 4.1)")


def nuovo_verifier() -> str:
    """64 caratteri base64url da 48 byte casuali: dentro i 43-128 della RFC e della specifica."""
    return _b64url(secrets.token_bytes(48))


@dataclass(frozen=True)
class RichiestaAutorizzazione:
    """Ciò che serve ricordare tra l'apertura del browser e la callback. Contiene il verifier: non va
    scritto nei log né mandato altrove."""

    url: str
    state: str
    code_verifier: str
    redirect_uri: str
    scope: tuple[str, ...]


@dataclass(frozen=True)
class TokenPiemonte:
    """Risposta del servizio token (par. 4.3.2)."""

    access_token: str
    token_type: str | None
    scope: tuple[str, ...]
    client_id: str | None
    expires_in: int | None
    ricevuto_alle: float

    @property
    def contenuto(self) -> ContenutoJWT:
        return leggi_jwt(self.access_token)

    @property
    def scadenza(self) -> float | None:
        """La più vicina tra `exp` del JWT ed `expires_in`. La specifica descrive `expires_in` come
        «Unix time» ma l'esempio vale 7199, cioè una durata in secondi; il kit lo legge come durata se è
        piccolo (sotto un miliardo) e come istante altrimenti. `CanalePiemonte` la usa se `token_jwt`
        restituisce questo oggetto invece della sola stringa (revisione esterna 02/10/2026)."""
        exp = self.contenuto.scadenza
        da_expires = None
        if self.expires_in is not None:
            da_expires = float(self.expires_in) if self.expires_in >= 1_000_000_000 else self.ricevuto_alle + self.expires_in
        note = [float(x) for x in (exp, da_expires) if x is not None]
        return min(note) if note else None


@dataclass(frozen=True)
class InfoSessione:
    """Risposta di GET /sessionid/verify (par. 4.3.4)."""

    stato_http: int
    stato: int | None  # 0 valido, 1 revocato, 2 scaduto
    descrizione: str | None
    inizio_validita: str | None
    fine_validita: str | None
    errore: dict | None

    @property
    def valido(self) -> bool:
        return self.stato_http == 200 and self.stato == 0


class ClientOAuth2Piemonte:
    """Client dell'Authorization Server di SIRPED. `base_url`: es. `https://<host>/reloauthserver` (dal CSI).

    Come `CanalePiemonte`, verso un host non locale vuole un'`AdesionePiemonte` (con lo stesso
    gestionale): la terza serratura, oltre al flag del collaudo e all'host dichiarato nel trasporto."""

    def __init__(self, base_url: str, gestionale: GestionalePiemonte, redirect_uri: str, *,
                 trasporto: Trasporto | None = None, adesione: AdesionePiemonte | None = None):
        if not base_url:
            raise ConfigurazioneNonValida("serve l'URL dell'Authorization Server di SIRPED: non è pubblicato, lo dà il CSI")
        if not _locale(base_url):
            if adesione is None:
                raise ConfigurazioneNonValida("verso SIRPED serve un'AdesionePiemonte (riferimento dell'autocertificazione "
                                              "e codice gestionale dati dalla Regione); senza, solo localhost")
            if adesione.gestionale != gestionale:
                raise ConfigurazioneNonValida("il gestionale del client non è quello dell'AdesionePiemonte")
        self.adesione = adesione
        p = urlparse(redirect_uri)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ConfigurazioneNonValida("redirect_uri: serve un URL http(s) assoluto, già comunicato alla Regione")
        self.base_url = base_url.rstrip("/")
        self.gestionale = gestionale
        self.redirect_uri = redirect_uri
        self.trasporto = trasporto or TrasportoHTTP()

    @property
    def client_id(self) -> str:
        return self.gestionale.valore

    # ------------------------------------------------------------------ 1. authorize (nel browser)

    def autorizzazione(self, scope: Iterable[str] = ("prescrizione",), *, state: str | None = None,
                       code_verifier: str | None = None) -> RichiestaAutorizzazione:
        elenco = tuple(dict.fromkeys(scope))
        if not elenco or any(s not in SCOPE_AMMESSI for s in elenco):
            raise ConfigurazioneNonValida(f"scope ammessi: {', '.join(SCOPE_AMMESSI)}")
        state = state or secrets.token_urlsafe(24)
        if len(state) > 500:
            raise ConfigurazioneNonValida("state: al massimo 500 caratteri (par. 4.3.1)")
        verifier = code_verifier or nuovo_verifier()
        # L'URL lo apre il browser, non il trasporto: la guardia si applica qui, prima che il medico si
        # autentichi su un sistema che il kit poi non potrebbe chiamare.
        # Con un trasporto che non è TrasportoHTTP i permessi si leggono se ci sono, altrimenti valgono
        # «niente»: la guardia non si spegne in silenzio.
        self._guardia(f"{self.base_url}/oauth2/authorize")
        parametri = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(elenco),
            "state": state,
            "code_challenge": challenge_s256(verifier),
            "code_challenge_method": "S256",
        }
        return RichiestaAutorizzazione(f"{self.base_url}/oauth2/authorize?{urlencode(parametri)}", state, verifier,
                                       self.redirect_uri, elenco)

    # ------------------------------------------------------------------ 2. callback

    @staticmethod
    def leggi_callback(url_callback: str, richiesta: RichiestaAutorizzazione) -> str:
        """Restituisce il `code`. Prima controlla lo `state` (CSRF), poi un eventuale `error`."""
        p = urlparse(url_callback)
        attesa = urlparse(richiesta.redirect_uri)
        if (p.scheme, p.netloc, p.path) != (attesa.scheme, attesa.netloc, attesa.path):
            raise ErroreAutorizzazione("invalid_redirect_uri", "la callback non è la redirect_uri della richiesta")
        q = {k: v[0] for k, v in parse_qs(p.query, keep_blank_values=True).items()}
        if not secrets.compare_digest(q.get("state", "").encode("utf-8"), richiesta.state.encode("utf-8")):
            raise ErroreAutorizzazione("state_non_valido", "lo state della callback non è quello della richiesta")
        if "error" in q:
            raise ErroreAutorizzazione(q["error"], q.get("error_description"))
        if not q.get("code"):
            raise ErroreAutorizzazione("invalid_request", "callback senza code")
        return q["code"]

    # ------------------------------------------------------------------ 3. token

    def _guardia(self, url: str) -> None:
        verifica_url_consentito(url, *permessi_del_trasporto(self.trasporto))

    def _chiama(self, servizio: str, metodo: str, url: str, corpo: bytes = b"", intestazioni=None):
        # come ogni canale: si consegna da `consegna`, che applica la guardia anche a un trasporto proprio
        richiesta = Richiesta(f"piemonte.oauth2.{servizio}", url, corpo, dict(intestazioni or {}), metodo=metodo)
        return consegna(self.trasporto, richiesta)

    @staticmethod
    def _json(corpo: bytes) -> dict:
        if not corpo.strip():
            return {}
        try:
            dati = json.loads(corpo.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ErroreTrasporto(f"risposta non JSON ({len(corpo)} byte)") from e
        return dati if isinstance(dati, dict) else {"valore": dati}

    def scambia_codice(self, code: str, richiesta: RichiestaAutorizzazione, *, jwks: dict | None = None) -> TokenPiemonte:
        """POST /oauth2/token e verifica del JWT ricevuto, prima di restituirlo:
          - firma con la chiave del JWKS (`jwks`, oppure chiesto a /.well-known/jwks.json): solo
            RS256/384/512, `none` e HMAC rifiutati (`verifica_firma`);
          - `aud` = il client_id di questo gestionale (REL-STC-01, p. 30).
        Un JWT che non passa è un ErroreAutorizzazione: il kit non lo consegna al canale. Fino al
        02/10/2026 qui si controllava solo che fosse leggibile (revisione esterna, punto 8)."""
        corpo = urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": richiesta.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": richiesta.code_verifier,
        }).encode("ascii")
        r = self._chiama("token", "POST", f"{self.base_url}/oauth2/token", corpo,
                         {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        dati = self._json(r.corpo)
        if r.stato_http != 200 or "access_token" not in dati:
            raise ErroreAutorizzazione(str(dati.get("error", f"HTTP {r.stato_http}")), dati.get("error_description"))
        # Da qui ogni difetto della risposta è un ErroreAutorizzazione, mai un'altra eccezione: tipi
        # sbagliati (access_token oggetto o numero, scope numero), numeri non finiti (expires_in NaN o
        # Infinity), JWT o JWKS malformati (revisione esterna giro 2 N5, giro 3 residuo).
        try:
            token = self._token_dalla_risposta(dati)
        except ErroreAutorizzazione:
            raise
        except Exception as e:  # noqa: BLE001
            raise ErroreAutorizzazione("risposta_non_valida", f"{type(e).__name__}: {e}") from None
        try:
            contenuto = verifica_firma(token.access_token, jwks if jwks is not None else self.jwks())
            token.scadenza  # noqa: B018 - exp, se c'è, deve essere un numero finito
            if contenuto.non_ancora_valido():
                raise ConfigurazioneNonValida(f"JWT non ancora valido: nbf {contenuto.valido_dal} nel futuro "
                                              "(REL-STC-01, p. 30)")
            aud = contenuto.payload.get("aud")
        except ErroreAutorizzazione:
            raise
        except ErroreTrasporto:  # il JWKS non si è potuto chiedere: non è un JWT non valido
            raise
        except Exception as e:  # noqa: BLE001
            # Un JWT o un JWKS malformato (alg non stringa, modulo RSA non valido, exp infinito) è un
            # JWT non valido, non un'eccezione fuori dal contratto (revisione esterna giro 2, N5)
            raise ErroreAutorizzazione("jwt_non_valido", f"{type(e).__name__}: {e}") from None
        if aud != self.client_id:
            raise ErroreAutorizzazione("jwt_non_valido", f"aud {aud!r}, atteso {self.client_id!r}")
        return token

    @staticmethod
    def _token_dalla_risposta(dati: dict) -> TokenPiemonte:
        """I campi della risposta del servizio token (par. 4.3.2) con i loro tipi, controllati."""
        access_token = dati["access_token"]
        if not isinstance(access_token, str) or not access_token.strip():
            raise ErroreAutorizzazione("risposta_non_valida", f"access_token non è una stringa: {type(access_token).__name__}")
        token_type = dati.get("token_type", "Bearer")
        if not isinstance(token_type, str) or token_type.lower() != "bearer":
            raise ErroreAutorizzazione("token_type", f"atteso Bearer, ricevuto {token_type!r}")
        scope = dati.get("scope")
        if scope is None:  # assente: nessuno scope dichiarato nella risposta
            scope = ()
        elif isinstance(scope, str):
            scope = tuple(scope.split())
        elif isinstance(scope, list) and all(isinstance(x, str) for x in scope):
            scope = tuple(scope)
        else:
            raise ErroreAutorizzazione("risposta_non_valida", f"scope non è una stringa né una lista di stringhe: {scope!r}")
        client_id = dati.get("client_id")
        if client_id is not None and not isinstance(client_id, str):
            raise ErroreAutorizzazione("risposta_non_valida", f"client_id non è una stringa: {client_id!r}")
        expires_in = dati.get("expires_in")
        if isinstance(expires_in, float) and not math.isfinite(expires_in):
            raise ErroreAutorizzazione("risposta_non_valida", f"expires_in non è un numero finito: {expires_in!r}")
        if expires_in is not None and _numero(expires_in) is None:
            # presente ma non un numero (booleano, testo, lista): risposta malformata, non «assente»
            raise ErroreAutorizzazione("risposta_non_valida", f"expires_in non è un numero: {expires_in!r}")
        return TokenPiemonte(
            access_token=access_token,
            token_type=dati.get("token_type"),
            scope=scope,
            client_id=client_id,
            expires_in=_numero(expires_in),
            ricevuto_alle=time.time(),
        )

    # ------------------------------------------------------------------ servizi regionali

    def _query_utente(self, cf_utente: str) -> str:
        return urlencode({"client_id": self.client_id, "cfutente": cf_utente})

    def verifica_sessione(self, access_token: str, cf_utente: str) -> InfoSessione:
        """GET /sessionid/verify (par. 4.3.4): 200 con infoToken, 401 token non valido, 500 errore."""
        r = self._chiama("verify", "GET", f"{self.base_url}/sessionid/verify?{self._query_utente(cf_utente)}",
                         intestazioni={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
        dati = self._json(r.corpo) if r.stato_http in (200, 500) else {}
        info = dati.get("infoToken") or {}
        stato = info.get("stato")
        return InfoSessione(r.stato_http, int(stato) if isinstance(stato, (int, float, str)) and str(stato).isdigit() else None,
                            info.get("descrizione"), info.get("dataInizioValidita"), info.get("dataFineValidita"),
                            dati.get("errore"))

    def revoca_sessione(self, access_token: str, cf_utente: str, *, metodo: str = "DELETE") -> int:
        """Revoca dell'Id-Sessione (par. 4.3.5). Restituisce lo stato HTTP: 200 revocato, 401 già revocato,
        scaduto o non riconosciuto, 500 errore.

        Metodo: il testo e il piano dei test dicono DELETE, l'esempio e il YAML dicono GET. Il kit usa
        DELETE; `metodo="GET"` per adeguarsi a ciò che il CSI confermerà.
        """
        if metodo not in ("DELETE", "GET"):
            raise ConfigurazioneNonValida("revoca: DELETE (testo) o GET (esempio e YAML)")
        r = self._chiama("revoke", metodo, f"{self.base_url}/sessionid/revoke?{self._query_utente(cf_utente)}",
                         intestazioni={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
        return r.stato_http

    def jwks(self) -> dict:
        """GET /.well-known/jwks.json (par. 4.3.3)."""
        r = self._chiama("jwks", "GET", f"{self.base_url}/.well-known/jwks.json", intestazioni={"Accept": "application/json"})
        if r.stato_http != 200:
            raise ErroreTrasporto(f"JWKS: HTTP {r.stato_http}", r.stato_http, r.corpo)
        return self._json(r.corpo)


# ------------------------------------------------------------------ firma del JWT

_ALGORITMI = {"RS256": "SHA256", "RS384": "SHA384", "RS512": "SHA512"}


def _numero(v) -> int | None:
    """expires_in come numero o come stringa di cifre ("7199"); altro (anche true/false) = assente."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _intero(b64: str) -> int:
    return int.from_bytes(base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)), "big")


def chiave_da_jwks(jwks: dict, kid: str | None = None):
    """La chiave RSA del JWKS. Il modulo si cerca in «n» (RFC 7517, e l'esempio della specifica) e, se
    manca, in «v» (il nome che usa la tabella del par. 4.3.3). Se c'è un `kid`, deve corrispondere."""
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    chiavi = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(chiavi, list) or not chiavi:
        raise ConfigurazioneNonValida("JWKS senza chiavi")
    candidate = [k for k in chiavi if isinstance(k, dict) and str(k.get("kty", k.get("Kty", ""))).upper() == "RSA"]
    if kid is None and len(candidate) > 1:
        raise ConfigurazioneNonValida("JWT senza kid e JWKS con più chiavi RSA: non si sceglie una chiave a caso")
    if kid is not None:
        stesse = [k for k in candidate if k.get("kid") == kid]
        # La tabella del par. 4.3.3 fissa kid = «rel-oauth2-key», ma l'esempio di access_token del par.
        # 4.3.2 ha un kid a forma di UUID. SIRPED usa «una unica chiave»: se il kid non torna e la chiave
        # RSA è una sola, si usa quella (la firma decide comunque).
        candidate = stesse or (candidate if len(candidate) == 1 else [])
    if not candidate:
        raise ConfigurazioneNonValida("nessuna chiave RSA nel JWKS" + (f" con kid {kid!r}" if kid else ""))
    k = candidate[0]
    modulo = k.get("n") or k.get("v")
    if not modulo or not k.get("e"):
        raise ConfigurazioneNonValida("chiave JWKS senza modulo (n/v) o esponente (e)")
    try:
        return RSAPublicNumbers(_intero(k["e"]), _intero(modulo)).public_key()
    except (ValueError, TypeError) as e:  # base64 non valido, «n must be >= 3» (giro 2, N5)
        raise ConfigurazioneNonValida(f"chiave JWKS non valida: {e}") from None


def verifica_firma(token: str, jwks: dict) -> ContenutoJWT:
    """Verifica la firma del JWT con la chiave del JWKS e lo restituisce letto. Solo RS256/384/512: la
    specifica non dice l'algoritmo, il JWKS è RSA. `none` e gli HMAC si rifiutano sempre."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    contenuto = leggi_jwt(token)
    alg = contenuto.intestazione.get("alg")
    if not isinstance(alg, str) or alg not in _ALGORITMI:  # {"alg": []} non è hashable: TypeError (giro 2, N5)
        raise ConfigurazioneNonValida(f"algoritmo del JWT non ammesso: {alg!r}")
    testa, corpo, firma = token.strip().split(".")
    chiave = chiave_da_jwks(jwks, contenuto.intestazione.get("kid"))
    try:
        grezza = base64.urlsafe_b64decode(firma + "=" * (-len(firma) % 4))
    except (ValueError, TypeError):
        raise ConfigurazioneNonValida("firma del JWT non decodificabile") from None
    try:
        chiave.verify(grezza, f"{testa}.{corpo}".encode("ascii"),
                      padding.PKCS1v15(), getattr(hashes, _ALGORITMI[alg])())
    except InvalidSignature:
        raise ConfigurazioneNonValida("firma del JWT non valida") from None
    return contenuto
