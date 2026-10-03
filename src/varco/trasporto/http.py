# SPDX-License-Identifier: EUPL-1.2
"""Trasporto HTTPS generico: nessuna conoscenza di SOAP, SAC o ricette.

Responsabilità:
  - guardia anti-produzione sull'URL finale (prima di aprire qualunque socket); i canali passano
    da `consegna`, che la applica anche quando il trasporto non è `TrasportoHTTP`;
  - limite di frequenza per host (default: 1 richiesta/secondo);
  - verifica TLS con i certificati di sistema (il server di test usa un
    certificato pubblico; il .pem autofirmato del kit MEF è scaduto nel 2025);
  - registrazione opzionale di richiesta/risposta (per prove e audit).

Solo libreria standard: domani sotto ci può stare anche un trasporto REST
(gateway FSE 2.0) con la stessa interfaccia `Trasporto`.
"""

from __future__ import annotations

import contextlib
import contextvars
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import weakref
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol
from urllib.parse import urlparse

from ..ambienti import indirizzo_ip, verifica_url_consentito
from ..errori import AmbienteBloccato, ErroreTrasporto


@dataclass(frozen=True)
class Richiesta:
    servizio: str  # nome logico, es. "sac.invioPrescritto"
    url: str
    corpo: bytes
    intestazioni: dict[str, str] = field(default_factory=dict)
    metodo: str = "POST"


@dataclass(frozen=True)
class Risposta:
    stato_http: int
    corpo: bytes
    intestazioni: dict[str, str]
    durata_s: float
    # Contratto del trasporto: se ha seguito redirect, l'URL a cui è arrivato davvero. `consegna` lo
    # rivaluta con la guardia. None = l'URL della richiesta (nessun redirect seguito).
    url_finale: str | None = None


Registratore = Callable[[Richiesta, "Risposta | None", "BaseException | None"], None]


class Trasporto(Protocol):
    def invia(self, richiesta: Richiesta) -> Risposta: ...


def permessi_del_trasporto(trasporto: object) -> tuple[bool, bool, frozenset[str]]:
    """I flag che un trasporto dichiara: (consenti_produzione, consenti_collaudo_regionale, collaudi_piemonte).

    `TrasportoHTTP` li riceve nel costruttore; un trasporto proprio li dichiara come attributi con lo
    stesso nome. Valgono solo se sono proprio `True`: un trasporto che non li dichiara non ha permessi.
    """
    produzione = getattr(trasporto, "consenti_produzione", False) is True
    collaudo = getattr(trasporto, "consenti_collaudo_regionale", False) is True
    dichiarati = getattr(trasporto, "collaudi_piemonte", None) or ()
    if isinstance(dichiarati, str):
        dichiarati = (dichiarati,)
    return produzione, collaudo, frozenset(str(h) for h in dichiarati)


# --- guardia di rete durante l'invio --------------------------------------------------------------
#
# Un trasporto proprio è codice dell'integratore: può seguire redirect (un normale opener urllib lo
# fa), riscrivere l'URL, usare requests o httpx. Controllare solo l'URL iniziale non basta: un 302
# verso la produzione porterebbe la richiesta, con l'Authorization, fino al connect. Per questo
# `consegna` tiene accesa una guardia per tutta la durata di `invia`: un audit hook di processo
# (PEP 578) rivaluta la guardia su OGNI richiesta urllib (anche ogni salto di redirect), su ogni
# connect di http.client (e sull'host del tunnel, se c'è un proxy), su ogni risoluzione DNS
# (`socket.getaddrinfo`, `gethostbyname`: ci passano anche requests, httpx e i socket grezzi) e su
# ogni connect di socket verso un NOME. Un host vietato solleva `AmbienteBloccato` lì, prima del
# socket; se il trasporto ingoia l'eccezione, `consegna` la risolleva comunque al ritorno.
# A quale chiamata appartiene un accesso (issue #2: un thread estraneo del gestionale faceva fallire
# una risposta SAC già arrivata):
#   - il thread di `invia` (contextvar) e i thread AVVIATI da lui o dai suoi thread durante la chiamata
#     (un pool creato dal trasporto): `threading.Thread.start` (e, dove c'è, l'evento di audit
#     `_thread.start_new_thread` / `_thread.start_joinable_thread`) lega il nuovo thread alla guardia
#     del thread che lo avvia. Lì valgono i permessi della chiamata, e una violazione ricade sulla chiamata;
#   - un thread senza legame (un altro servizio del gestionale, un pool nato prima della chiamata):
#     si ferma solo ciò che è vietato PER NOME secondo tutte le guardie attive (un host di produzione,
#     o un IP che il modulo socket ha risolto da un nome vietato). Un IP letterale che nessuno ha
#     risolto non si attribuisce a nessuno e passa. La violazione NON ricade sulla chiamata in corso.
#   Un trasporto che usa un pool già esistente può legarne i thread con `contextvars.copy_context()`
#   o con `esegui_nella_guardia` (sotto).
#
# Principio (giro 3 di revisione, N1): l'host si valuta nel momento in cui i byte stanno per partire,
# non solo quando si apre il socket.
#   - `socket.connect` verso un IP: l'IP vale solo se il modulo `socket` l'ha restituito risolvendo un
#     NOME, e quel nome passa la guardia ADESSO, con i permessi di questa chiamata. Un IP che nessuno
#     ha risolto davanti alla guardia (un resolver proprio, un indirizzo scritto nel codice, una cache
#     esterna) non si può attribuire a nessun host: vale come un IP letterale, cioè PRODUZIONE.
#   - `http.client.send`: ogni scrittura su una connessione http.client (urllib, requests/urllib3)
#     rivaluta l'host della connessione e quello del tunnel. Così una connessione già aperta verso la
#     produzione e riusata da un pool viene fermata prima che richiesta e Authorization partano.
# Restano fuori i trasporti che scrivono su socket già aperti senza passare da http.client (httpx,
# socket grezzi riusati): Python non offre un evento su `send`. Il contratto di `consegna` li esclude.

_EVENTI_DI_RETE = frozenset({
    "urllib.Request", "http.client.connect", "http.client.send", "socket.getaddrinfo",
    "socket.gethostbyname", "socket.gethostbyname_ex", "socket.connect",
})
_EVENTI_AVVIO_THREAD = frozenset({"_thread.start_new_thread", "_thread.start_joinable_thread"})

# IP restituiti dal modulo socket -> nomi da cui sono stati risolti (ultimi N, di processo).
_MAX_RISOLTI = 4096
_ip_risolti: "dict[str, set[str]]" = {}
_lock_risolti = threading.Lock()


def _chiave_ip(ip) -> str | None:
    if isinstance(ip, (bytes, bytearray)):
        ip = bytes(ip).decode("ascii", "replace")
    if not isinstance(ip, str):
        return None
    indirizzo = indirizzo_ip(ip)
    if indirizzo is None:
        return None
    # ::ffff:a.b.c.d e a.b.c.d sono la stessa destinazione: una sola chiave, o la provenienza si perde
    mappato = getattr(indirizzo, "ipv4_mapped", None)
    return str(mappato if mappato is not None else indirizzo)


def _annota_risolti(nome, ips) -> None:
    if isinstance(nome, (bytes, bytearray)):
        nome = bytes(nome).decode("ascii", "replace")
    if not isinstance(nome, str) or not nome or indirizzo_ip(nome) is not None:
        return  # risolvere un IP letterale non lo lega a nessun nome
    with _lock_risolti:
        for ip in ips:
            chiave = _chiave_ip(ip)
            if chiave is None:
                continue
            _ip_risolti.setdefault(chiave, set()).add(nome)
            if len(_ip_risolti) > _MAX_RISOLTI:
                _ip_risolti.pop(next(iter(_ip_risolti)))


def _nomi_di(ip: str) -> frozenset[str]:
    chiave = _chiave_ip(ip)
    with _lock_risolti:
        return frozenset(_ip_risolti.get(chiave, ())) if chiave else frozenset()


# i resolver originali: presi quando si installa la guardia (rispetta chi li ha già sostituiti)
_getaddrinfo_originale = socket.getaddrinfo
_gethostbyname_originale = socket.gethostbyname
_gethostbyname_ex_originale = socket.gethostbyname_ex


def _getaddrinfo(host, *args, **kwargs):
    risultati = _getaddrinfo_originale(host, *args, **kwargs)
    try:
        _annota_risolti(host, [r[4][0] for r in risultati if r and len(r) > 4 and r[4]])
    except Exception:  # noqa: BLE001 - annotare non deve rompere la risoluzione
        pass
    return risultati


def _gethostbyname(host):
    ip = _gethostbyname_originale(host)
    _annota_risolti(host, [ip])
    return ip


def _gethostbyname_ex(host):
    risultato = _gethostbyname_ex_originale(host)
    try:
        _annota_risolti(host, list(risultato[2]))
    except Exception:  # noqa: BLE001
        pass
    return risultato


class _GuardiaDiRete:
    __slots__ = ("permessi", "violazioni")

    def __init__(self, permessi: tuple[bool, bool, frozenset[str]]):
        self.permessi = permessi
        self.violazioni: list[AmbienteBloccato] = []


_guardia_corrente: contextvars.ContextVar[_GuardiaDiRete | None] = contextvars.ContextVar(
    "varco_guardia_di_rete", default=None)
_guardie_attive: dict[int, _GuardiaDiRete] = {}
# threading.Thread avviati durante una chiamata -> la sua guardia (si liberano col thread)
_thread_legati: "weakref.WeakKeyDictionary[threading.Thread, _GuardiaDiRete]" = weakref.WeakKeyDictionary()
_lock_guardie = threading.Lock()
_in_verifica = threading.local()
_hook_installato = False


def _url_di_un_host(host, porta=None) -> str | None:
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    if not isinstance(host, str) or not host:
        return None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"https://{host}:{porta}/" if isinstance(porta, int) else f"https://{host}/"


def _destinazioni(evento: str, args: tuple) -> list[str]:
    if evento == "urllib.Request":
        return [args[0]] if args and isinstance(args[0], str) else []
    if evento == "http.client.connect":
        conn, host, porta = args
        urls = [_url_di_un_host(host, porta)]
        tunnel = getattr(conn, "_tunnel_host", None)  # proxy CONNECT: la vera destinazione
        if tunnel:
            urls.append(_url_di_un_host(tunnel, getattr(conn, "_tunnel_port", None)))
        return [u for u in urls if u]
    if evento in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"):
        u = _url_di_un_host(args[0] if args else None)
        return [u] if u else []
    if evento == "http.client.send":
        conn = args[0] if args else None
        urls = [_url_di_un_host(getattr(conn, "host", None), getattr(conn, "port", None))]
        tunnel = getattr(conn, "_tunnel_host", None)
        if tunnel:
            urls.append(_url_di_un_host(tunnel, getattr(conn, "_tunnel_port", None)))
        return [u for u in urls if u]
    if evento == "socket.connect":
        indirizzo = args[1] if len(args) > 1 else None
        if isinstance(indirizzo, tuple) and indirizzo and isinstance(indirizzo[0], (str, bytes)):
            u = _url_di_un_host(indirizzo[0])
            return [u] if u else []
        return []
    return []


def _verifica_destinazione(evento: str, url: str, permessi) -> None:
    """La guardia su una destinazione. Per `socket.connect` verso un IP: l'IP passa se è consentito
    di suo (loopback, o produzione con il flag) OPPURE se uno dei nomi da cui il modulo socket l'ha
    risolto passa la guardia adesso. Un IP mai risolto davanti alla guardia resta un IP letterale."""
    try:
        verifica_url_consentito(url, *permessi)
        return
    except AmbienteBloccato:
        if evento != "socket.connect":
            raise
        ip = urlparse(url).hostname or ""
        if indirizzo_ip(ip) is None:
            raise
        nomi = _nomi_di(ip)
        for nome in sorted(nomi):
            try:
                verifica_url_consentito(_url_di_un_host(nome) or "", *permessi)
                return
            except AmbienteBloccato:
                continue
        raise


def _guardia_del_thread() -> _GuardiaDiRete | None:
    """La guardia della chiamata a cui appartiene il thread corrente, o None (thread senza legame)."""
    propria = _guardia_corrente.get()
    if propria is not None:
        return propria
    try:
        g = _thread_legati.get(threading.current_thread())
    except TypeError:
        return None
    if g is not None and id(g) in _guardie_attive:
        return g
    return None


def _lega(t: object) -> None:
    """Se chi avvia `t` appartiene a una chiamata, anche `t` le appartiene."""
    g = _guardia_del_thread()
    if g is not None and isinstance(t, threading.Thread):
        with _lock_guardie:
            _thread_legati[t] = g


def _lega_thread(args: tuple) -> None:
    """Evento di audit dell'avvio (Python 3.12+): args[0] è threading.Thread._bootstrap, legato al Thread."""
    if args:
        _lega(getattr(args[0], "__self__", None))


_avvia_thread_originale = threading.Thread.start


def _avvia_thread(self, *args, **kwargs):
    """`threading.Thread.start` con il legame alla guardia: vale anche dove l'evento di audit dell'avvio
    non esiste (Python 3.11, CI del 03/10/2026)."""
    if _guardie_attive:
        try:
            _lega(self)
        except Exception:  # noqa: BLE001 - legare non deve mai impedire l'avvio
            pass
    return _avvia_thread_originale(self, *args, **kwargs)


def _vietato_per_nome(evento: str, url: str, permessi) -> None:
    """Thread senza legame: si ferma un host vietato per nome, o un IP risolto da un nome vietato;
    un IP che nessuno ha risolto davanti alla guardia non si attribuisce a nessuno (issue #2)."""
    ip = urlparse(url).hostname or ""
    if indirizzo_ip(ip) is None:
        verifica_url_consentito(url, *permessi)
        return
    for nome in sorted(_nomi_di(ip)):
        verifica_url_consentito(_url_di_un_host(nome) or "", *permessi)


def _hook_di_rete(evento: str, args: tuple) -> None:
    if not _guardie_attive:
        return
    if evento in _EVENTI_AVVIO_THREAD:
        _lega_thread(args)
        return
    if evento not in _EVENTI_DI_RETE:
        return
    if getattr(_in_verifica, "attivo", False):
        return
    _in_verifica.attivo = True
    try:
        propria = _guardia_del_thread()
        if propria is not None:
            for url in _destinazioni(evento, args):
                try:
                    _verifica_destinazione(evento, url, propria.permessi)
                except AmbienteBloccato as e:
                    propria.violazioni.append(e)  # ricade sulla chiamata: `consegna` la risolleva
                    raise
            return
        with _lock_guardie:
            guardie = list(_guardie_attive.values())
        for url in _destinazioni(evento, args):
            for g in guardie:
                _vietato_per_nome(evento, url, g.permessi)  # si ferma qui, NON si registra sulla chiamata
    finally:
        _in_verifica.attivo = False


def esegui_nella_guardia(funzione: Callable, *args, **kwargs):
    """Per un trasporto che usa un pool di thread già esistente: `pool.submit(esegui_nella_guardia(f), ...)`
    restituisce una funzione che, nel thread del pool, gira con la guardia della chiamata corrente
    (la stessa cosa di `contextvars.copy_context().run`)."""
    contesto = contextvars.copy_context()
    return lambda *a, **k: contesto.run(funzione, *(args + a), **{**kwargs, **k})


def _installa_hook() -> None:
    global _hook_installato, _getaddrinfo_originale, _gethostbyname_originale, _gethostbyname_ex_originale
    with _lock_guardie:
        if not _hook_installato:
            _getaddrinfo_originale = socket.getaddrinfo
            _gethostbyname_originale = socket.gethostbyname
            _gethostbyname_ex_originale = socket.gethostbyname_ex
            sys.addaudithook(_hook_di_rete)  # non si può togliere: con nessuna guardia attiva esce subito
            # i resolver del modulo socket annotano gli IP restituiti e il nome da cui vengono (vedi sopra)
            socket.getaddrinfo = _getaddrinfo
            socket.gethostbyname = _gethostbyname
            socket.gethostbyname_ex = _gethostbyname_ex
            threading.Thread.start = _avvia_thread  # lega alla chiamata i thread avviati durante l'invio
            _hook_installato = True


@contextlib.contextmanager
def guardia_di_rete(permessi: tuple[bool, bool, frozenset[str]]):
    """Tiene accesa la guardia anti-produzione su ogni accesso alla rete del blocco (vedi sopra).
    All'uscita, se un accesso è stato bloccato, solleva `AmbienteBloccato` anche se il codice del
    blocco ha ingoiato l'eccezione."""
    _installa_hook()
    g = _GuardiaDiRete(permessi)
    token = _guardia_corrente.set(g)
    with _lock_guardie:
        _guardie_attive[id(g)] = g
    try:
        try:
            yield g
        except AmbienteBloccato:
            raise
        except BaseException as e:
            if g.violazioni:
                raise g.violazioni[0] from e
            raise
        if g.violazioni:
            raise g.violazioni[0]
    finally:
        with _lock_guardie:
            _guardie_attive.pop(id(g), None)
        _guardia_corrente.reset(token)


def consegna(trasporto: "Trasporto", richiesta: Richiesta) -> Risposta:
    """L'unico punto in cui i canali del kit (SAC, SIST, FVG, Piemonte, client OAuth2; domani il FSE)
    consegnano una richiesta a un trasporto.

    La guardia anti-produzione scatta qui, prima di `invia`, con i permessi dichiarati dal trasporto:
    sostituire `TrasportoHTTP` con un trasporto proprio non la spegne. `TrasportoHTTP` la ripete per
    conto suo, così vale anche per chi lo usa direttamente.

    Contratto del trasporto: la guardia resta accesa per tutto `invia` (redirect, DNS e connect
    compresi, vedi `guardia_di_rete`), e se il trasporto riporta `Risposta.url_finale` la guardia
    si rivaluta anche su quello. Un trasporto che apre la rete fuori da Python (un processo esterno)
    esce da questo controllo: non è ammesso.
    """
    permessi = permessi_del_trasporto(trasporto)
    verifica_url_consentito(richiesta.url, *permessi)
    with guardia_di_rete(permessi):
        risposta = trasporto.invia(richiesta)
    finale = getattr(risposta, "url_finale", None)
    if finale is not None and finale != richiesta.url:
        verifica_url_consentito(finale, *permessi)
    return risposta


class LimitatoreFrequenza:
    """Intervallo minimo tra due richieste verso lo stesso host (thread-safe, di processo)."""

    _lock = threading.Lock()
    _ultima: dict[str, float] = {}

    def __init__(self, intervallo_minimo_s: float = 1.0):
        self.intervallo = intervallo_minimo_s

    def attendi(self, host: str) -> None:
        with self._lock:
            adesso = time.monotonic()
            prossima = self._ultima.get(host, 0.0) + self.intervallo
            attesa = max(0.0, prossima - adesso)
            self._ultima[host] = adesso + attesa
        if attesa:
            time.sleep(attesa)


class _RedirectVietato(urllib.request.HTTPRedirectHandler):
    """I servizi SOAP del SAC (e del SIST) non fanno redirect: seguirne uno aggirerebbe la guardia
    sull'URL (un 302 verso la produzione). Il redirect diventa un errore."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ErroreTrasporto(f"Redirect {code} verso {newurl} rifiutato (il kit non segue redirect)", code)


class TrasportoHTTP:
    def __init__(
        self,
        *,
        timeout_s: float = 30.0,
        intervallo_minimo_s: float = 1.0,
        consenti_produzione: bool = False,
        consenti_collaudo_regionale: bool = False,
        collaudi_piemonte: Iterable[str] = (),
        registratore: Registratore | None = None,
        contesto_tls: ssl.SSLContext | None = None,
    ):
        """`collaudi_piemonte`: host di collaudo di SIRPED (Regione Piemonte, CSI) da considerare
        raggiungibili insieme a `consenti_collaudo_regionale=True`. Le specifiche non ne pubblicano
        nessuno: si copiano dal kit di test che il CSI consegna con l'autocertificazione."""
        if intervallo_minimo_s < 0.5:
            raise ValueError("Intervallo minimo < 0,5 s: il kit non fa più di 2 richieste/s")
        self.timeout_s = timeout_s
        self.limitatore = LimitatoreFrequenza(intervallo_minimo_s)
        self.consenti_produzione = consenti_produzione
        self.consenti_collaudo_regionale = consenti_collaudo_regionale
        self.collaudi_piemonte = frozenset(h.lower().rstrip(".") for h in collaudi_piemonte)
        self.registratore = registratore
        self.contesto_tls = contesto_tls or ssl.create_default_context()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.contesto_tls), _RedirectVietato()
        )

    def invia(self, richiesta: Richiesta) -> Risposta:
        # 1) guardia: prima di tutto, anche prima del limitatore
        verifica_url_consentito(
            richiesta.url, self.consenti_produzione, self.consenti_collaudo_regionale, self.collaudi_piemonte
        )
        parsed = urlparse(richiesta.url)
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise ErroreTrasporto(f"Solo HTTPS (eccetto localhost): {richiesta.url}")
        self.limitatore.attendi(parsed.hostname or "")

        req = urllib.request.Request(
            richiesta.url, data=richiesta.corpo, method=richiesta.metodo, headers=richiesta.intestazioni
        )
        inizio = time.monotonic()
        risposta: Risposta | None = None
        errore: BaseException | None = None
        try:
            try:
                with guardia_di_rete(
                    (self.consenti_produzione is True, self.consenti_collaudo_regionale is True, self.collaudi_piemonte)
                ), self._opener.open(req, timeout=self.timeout_s) as r:
                    risposta = Risposta(r.status, r.read(), dict(r.headers.items()), time.monotonic() - inizio,
                                        url_finale=getattr(r, "url", None) or richiesta.url)
            except urllib.error.HTTPError as e:
                # I SOAP Fault arrivano con HTTP 500: sono risposte, non errori di rete.
                risposta = Risposta(e.code, e.read(), dict(e.headers.items()), time.monotonic() - inizio)
            except ErroreTrasporto as e:
                errore = e
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                errore = ErroreTrasporto(f"Errore di rete verso {parsed.hostname}: {e}")
                raise errore from e
            return risposta
        finally:
            if self.registratore is not None:
                try:
                    self.registratore(richiesta, risposta, errore)
                except Exception:  # il registratore non deve mai rompere la chiamata
                    pass
