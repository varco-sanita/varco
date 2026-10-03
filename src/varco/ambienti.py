# SPDX-License-Identifier: EUPL-1.2
"""Ambienti del Sistema TS e guardia anti-produzione.

La guardia lavora sull'URL finale (non solo sull'enum): anche un indirizzo
scritto a mano in configurazione viene fermato se punta alla produzione. L'host si
normalizza come lo vedrà la rete (percent-encoding, IDNA, maiuscole, punto finale) e gli
IP letterali diversi dal loopback contano come produzione: vedi `host_normalizzati`.
Il punto in cui la guardia scatta per ogni canale è `trasporto.http.consegna`.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Iterable
from urllib.parse import unquote, urlparse

from .errori import AmbienteBloccato

HOST_TEST = "demservicetest.sanita.finanze.it"
HOST_PRODUZIONE = "demservice.sanita.finanze.it"
DOMINIO_SOGEI = "sanita.finanze.it"

# Gateway FSE 2.0 (it-fse-support, openapi/gateway): "-val" è l'ambiente di validazione.
# Il kit oggi NON chiama il gateway (servono certificati rilasciati da Sogei): la guardia
# c'è lo stesso, così nessun trasporto del kit può raggiungere la produzione per sbaglio.
HOST_FSE_VALIDAZIONE = "modipa-val.fse.salute.gov.it"
HOST_FSE_PRODUZIONE = "modipa.fse.salute.gov.it"
DOMINIO_FSE = "fse.salute.gov.it"


# SIST della Regione Puglia (SAR, InnovaPuglia). Specifiche di integrazione v4.03.27, par. 6.1:
# collaudo e produzione stanno sulla RUPAR (rete privata regionale), raggiungibili solo con
# un'adesione (VPN, modulo di richiesta, CNS). Il kit non li chiama senza due atti espliciti:
# il flag sul trasporto e un'adesione dichiarata nel canale (trasporto/sist.py).
HOST_SIST_COLLAUDO = "pddasl-preprod.sanita.regione.rsr.rupar.puglia.it"
HOST_SIST_PRODUZIONE = "pdd-virtasl.rmmg.rsr.rupar.puglia.it"
DOMINIO_PUGLIA = "puglia.it"


# SAR della Regione Friuli-Venezia Giulia (Insiel). Specifiche Idof-dem-AT-01 dell'11/02/2026, cap. 5:
# sono pubblicati SOLO gli host di collaudo, raggiungibili in mutua autenticazione TLS con i
# certificati rilasciati da Insiel. Gli host di produzione non sono pubblicati: per prudenza ogni
# altro host *.fvg.it o *.insiel.it conta come produzione. Il collaudo, come quello del SIST,
# è un sistema reale della Regione: serve il flag del collaudo regionale e un'AdesioneFVG.
HOST_FVG_COLLAUDO = frozenset({
    "demtest.sanita.fvg.it",  # SAR dematerializzata, autenticazione con CRS/CNS (mTLS)
    "sartest.sanita.fvg.it",  # SAR MIR (Medici in Rete): richiesta lotti
    "isweb-collaudo.sanita.fvg.it",  # soluzione federata: access token
    "apiweb-collaudo.sanita.fvg.it",  # soluzione federata: Piattaforma di Interoperabilità Regionale
})
DOMINI_FVG = ("fvg.it", "insiel.it")


# SAR della Regione Piemonte (SIRPED, gestito dal CSI Piemonte). Specifiche REL-STC-01 V04 del
# 02/03/2026 e RE-SRS-SAR Cartelle cliniche MMG/PLS V05: NESSUN host è pubblicato. Gli URL di test e
# di produzione li dà la Regione, tramite il CSI, dopo la richiesta di autocertificazione (processo
# SIRPED-01 V01, par. 3; piano dei test RE-TES-01, par. 2.1). L'unico nome che compare è un esempio
# con un segnaposto, «tst-rel-xxxx.csi.it» (REL-STC-01, par. 4.3.6).
# Regola del kit, prudente:
#   - ogni host di questi domini conta come PRODUZIONE;
#   - fa eccezione solo un host il cui nome dice «collaudo» (un'etichetta che comincia o finisce con
#     tst, test o collaudo, come nell'esempio della specifica). Anche quello è un sistema reale della
#     Regione: serve il flag del collaudo regionale E l'host deve essere dichiarato a mano nel
#     trasporto (`TrasportoHTTP(collaudi_piemonte=...)`), cioè copiato dal kit ricevuto dal CSI.
DOMINI_PIEMONTE = ("piemonte.it", "csi.it", "csipiemonte.it", "salutepiemonte.it", "sistemapiemonte.it",
                   "ruparpiemonte.it")


# SAR della Regione Umbria (PuntoZero S.c.a r.l., in house della Regione). Specifiche pubbliche su
# github.com/punto-zero/umbria-sar-support (wiki, «Base URL»): API REST con mTLS e due JWT, come il
# gateway FSE 2.0. Pubblicati un host di TEST e uno di PRODUZIONE. Regola del kit, prudente:
#   - ogni host *.umbria.it o *.puntozeroscarl.it conta come PRODUZIONE, tranne l'host di test;
#   - l'host di test è un sistema REALE della Regione (i certificati di test sono pubblici, ma usarli
#     non è un'adesione): come gli altri collaudi regionali vuole il flag del collaudo e un'AdesioneUmbria.
HOST_UMBRIA_TEST = "api-salute-test.regione.umbria.it"
HOST_UMBRIA_PRODUZIONE = "api-salute.regione.umbria.it"
DOMINI_UMBRIA = ("umbria.it", "puntozeroscarl.it")


# --- host come lo vedrà la rete ---------------------------------------------------------
#
# La guardia confronta nomi; chi apre il socket può vedere un nome diverso da quello scritto.
# `urllib.request.Request` decodifica il percent-encoding dell'host («finanze.%69t» -> «finanze.it»),
# il modulo `socket` lo codifica in IDNA (che trasforma le lettere a larghezza piena e i punti
# 。．｡), il DNS ignora maiuscole e punto finale. La guardia prova TUTTE le letture dell'URL
# (quella di `urlparse` e quella di `urllib`), normalizzate così, e blocca se una sola è vietata.
#
# IP letterali: un indirizzo IP non dice di chi è. Politica del kit: l'IP di loopback (127.0.0.0/8,
# ::1, anche nelle forme ::ffff:127.x, 127.1, 0x7f.1) vale come localhost; ogni altro IP letterale
# (pubblico, privato, 0.0.0.0, anche scritto in decimale o esadecimale) conta come PRODUZIONE e
# vuole `consenti_produzione=True`. Un nome che il DNS risolve verso un host di produzione (un
# alias) la guardia non lo vede: resta a carico di chi configura (docs/MINACCE.md).

_PUNTI = ("\u3002", "\uff0e", "\uff61")


def _togli_porta_e_utente(h: str) -> str:
    h = h.rpartition("@")[2]
    if h.startswith("["):
        return h[1:].partition("]")[0]
    if h.count(":") == 1:
        h = h.partition(":")[0]
    return h


def _varianti(grezzo: str) -> set[str]:
    h = _togli_porta_e_utente(grezzo.strip())
    for _ in range(5):  # anche la doppia codifica (%2569 -> %69 -> i): prudenza
        decodificato = unquote(h)
        if decodificato == h:
            break
        h = _togli_porta_e_utente(decodificato.strip())
    h = unicodedata.normalize("NFKC", h)
    for punto in _PUNTI:
        h = h.replace(punto, ".")
    base = h.casefold().strip().rstrip(".")
    varianti = {base}
    try:
        # come il modulo socket: codec IDNA (nameprep: NFKC + casefold, etichetta per etichetta)
        varianti.add(base.encode("idna").decode("ascii").lower().rstrip("."))
    except UnicodeError:
        pass  # il socket rifiuterebbe il nome; resta la forma NFKC
    return varianti


def host_normalizzati(url: str) -> frozenset[str]:
    """Tutti i nomi a cui l'URL può portare, normalizzati come li vedranno urllib, http.client e il DNS."""
    grezzi: list[str] = []
    try:
        grezzi.append(urlparse(url).hostname or "")
    except ValueError:
        pass
    try:
        grezzi.append(urllib.request.Request(url).host or "")
    except Exception:  # noqa: BLE001 - URL che urllib non apre: restano le altre letture
        pass
    m = re.match(r"^[^:/?#]*:?//([^/?#]*)", url.strip())
    if m:
        grezzi.append(m.group(1))
    hosts: set[str] = set()
    for g in grezzi:
        hosts |= _varianti(g)
    return frozenset(hosts) or frozenset({""})


def _host(url: str) -> str:
    """Il nome (normalizzato) che usa urllib; per le decisioni la guardia usa `host_normalizzati`."""
    try:
        return next(iter(sorted(_varianti(urllib.request.Request(url).host or ""))))
    except Exception:  # noqa: BLE001
        return sorted(host_normalizzati(url))[0]


def indirizzo_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """L'IP se `host` è un indirizzo letterale, in ogni forma che getaddrinfo accetta (127.1, 0x7f.1, 2130706433)."""
    h = host.strip("[]").partition("%")[0]
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    if h and re.fullmatch(r"[0-9a-fA-FxX.]+", h):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(h))
        except (OSError, ValueError):
            return None
    return None


def _ip_loopback(ip) -> bool:
    mappato = getattr(ip, "ipv4_mapped", None)
    return (mappato or ip).is_loopback


def _nel_dominio(host: str, domini) -> bool:
    return any(host == d or host.endswith("." + d) for d in domini)


def _regione_puglia(host: str) -> bool:
    return _nel_dominio(host, (DOMINIO_PUGLIA,))


def _regione_fvg(host: str) -> bool:
    return _nel_dominio(host, DOMINI_FVG)


def _regione_piemonte(host: str) -> bool:
    return _nel_dominio(host, DOMINI_PIEMONTE)


def e_regione_puglia(url: str) -> bool:
    """True per qualunque host *.puglia.it (SIST, portali, RUPAR)."""
    return any(_regione_puglia(h) for h in host_normalizzati(url))


def e_collaudo_sist(url: str) -> bool:
    """True solo per l'ambiente di collaudo del SIST (pddasl-preprod): è un ambiente REALE della Regione."""
    return any(h == HOST_SIST_COLLAUDO for h in host_normalizzati(url))


def e_regione_fvg(url: str) -> bool:
    """True per qualunque host *.fvg.it o *.insiel.it (SAR, piattaforma regionale, Insiel)."""
    return any(_regione_fvg(h) for h in host_normalizzati(url))


def e_collaudo_fvg(url: str) -> bool:
    """True solo per gli host di collaudo del SAR FVG pubblicati nelle specifiche Insiel (cap. 5)."""
    return any(h in HOST_FVG_COLLAUDO for h in host_normalizzati(url))


def e_regione_piemonte(url: str) -> bool:
    """True per qualunque host della Regione Piemonte o del CSI Piemonte (SIRPED, portali, RUPAR)."""
    return any(_regione_piemonte(h) for h in host_normalizzati(url))


def _regione_umbria(host: str) -> bool:
    return _nel_dominio(host, DOMINI_UMBRIA)


def e_regione_umbria(url: str) -> bool:
    """True per qualunque host *.umbria.it o *.puntozeroscarl.it (SAR, FSE regionale, portali)."""
    return any(_regione_umbria(h) for h in host_normalizzati(url))


def e_collaudo_umbria(url: str) -> bool:
    """True solo per l'host di test del SAR Umbria pubblicato da PuntoZero: è un ambiente REALE della Regione."""
    return any(h == HOST_UMBRIA_TEST for h in host_normalizzati(url))


_ETICHETTA_COLLAUDO = re.compile(r"^(?:tst|test|collaudo)(?:-|\d|$)|-(?:tst|test|collaudo)$")


def e_collaudo_piemonte(url: str) -> bool:
    """True per un host piemontese il cui NOME è da collaudo (es. «tst-rel-xxxx.csi.it» della specifica).

    Solo il nome: perché il kit lo chiami servono anche il flag del collaudo regionale e la
    dichiarazione esplicita dell'host (vedi `verifica_url_consentito`). «attestazioni.regione...»
    NON è un collaudo: si guardano le etichette intere, non le sottostringhe.
    """
    return any(_collaudo_piemonte(h) for h in host_normalizzati(url))


def _collaudo_piemonte(host: str) -> bool:
    if not _regione_piemonte(host):
        return False
    return any(_ETICHETTA_COLLAUDO.search(e) for e in host.split(".")[:-2])


def e_collaudo_regionale(url: str) -> bool:
    """Collaudo di un SAR regionale (SIST Puglia, SAR FVG, SIRPED Piemonte, SAR Umbria): ambienti REALI delle Regioni."""
    return e_collaudo_sist(url) or e_collaudo_fvg(url) or e_collaudo_piemonte(url) or e_collaudo_umbria(url)


class Ambiente(str, Enum):
    TEST = "test"
    PRODUZIONE = "produzione"


BASE_URL = {
    Ambiente.TEST: f"https://{HOST_TEST}",
    Ambiente.PRODUZIONE: f"https://{HOST_PRODUZIONE}",
}


def e_produzione(url: str) -> bool:
    """True se l'URL punta a un host di produzione (Sogei/MEF, gateway FSE, Regione Puglia, Regione FVG, Regione Piemonte,
    Regione Umbria).

    Regola prudente: qualunque host *.sanita.finanze.it che non contenga "test"
    nel nome è trattato come produzione; lo stesso per *.fse.salute.gov.it (gateway
    FSE 2.0) che non sia l'ambiente di validazione "-val", e per *.puglia.it che non
    sia il collaudo SIST (che però ha una sua guardia: vedi `verifica_url_consentito`), e per
    *.fvg.it e *.insiel.it che non siano i collaudi del SAR FVG (stessa guardia del SIST), e per gli
    host della Regione Piemonte e del CSI (*.piemonte.it, *.csi.it, ...) che non abbiano un nome da
    collaudo (guardia propria: flag del collaudo più host dichiarato), e per *.umbria.it e
    *.puntozeroscarl.it che non siano l'host di test del SAR Umbria (che ha la guardia del SIST).
    """
    return any(_produzione(h) for h in host_normalizzati(url))


def _produzione(host: str) -> bool:
    ip = indirizzo_ip(host)
    if ip is not None:
        # IP letterale: non si sa di chi è. Solo il loopback vale come localhost (vedi sopra)
        return not _ip_loopback(ip)
    if host in (HOST_PRODUZIONE, HOST_FSE_PRODUZIONE, HOST_SIST_PRODUZIONE):
        return True
    if _regione_puglia(host):
        # prudenza: ogni host della Regione Puglia che non sia il collaudo SIST conta come produzione
        # (compreso wsit-virtasl.rmmg.rsr.rupar.puglia.it, l'indirizzo scritto nei WSDL ufficiali)
        return host != HOST_SIST_COLLAUDO
    if _regione_fvg(host):
        # prudenza: gli host di produzione del SAR FVG non sono pubblicati, quindi ogni host della
        # Regione FVG o di Insiel che non sia uno dei collaudi elencati conta come produzione
        return host not in HOST_FVG_COLLAUDO
    if _regione_umbria(host):
        # prudenza: ogni host della Regione Umbria o di PuntoZero che non sia l'host di test del SAR
        # conta come produzione (api-salute.regione.umbria.it compreso)
        return host != HOST_UMBRIA_TEST
    if _regione_piemonte(host):
        # prudenza: SIRPED non pubblica nessun host; conta come produzione ogni host della Regione
        # Piemonte o del CSI che non abbia un nome da collaudo (che però ha una sua guardia)
        return not _collaudo_piemonte(host)
    if host == DOMINIO_FSE or host.endswith("." + DOMINIO_FSE):
        # prudenza: qualunque host del gateway FSE che non sia esplicitamente "-val" è produzione
        return "-val." not in host
    return host.endswith(DOMINIO_SOGEI) and "test" not in host


def verifica_url_consentito(
    url: str,
    consenti_produzione: bool = False,
    consenti_collaudo_regionale: bool = False,
    collaudi_piemonte: Iterable[str] = (),
) -> None:
    """Solleva AmbienteBloccato se l'URL è di produzione, o di un collaudo regionale, senza il flag esplicito.

    Il collaudo SIST non è un ambiente pubblico come quello del MEF: è un sistema della Regione,
    raggiungibile solo dopo un'adesione. Per questo ha un flag suo, distinto dalla produzione.
    """
    if e_produzione(url) and consenti_produzione is not True:
        raise AmbienteBloccato(
            f"Chiamata verso PRODUZIONE bloccata ({url}). "
            "Serve consenti_produzione=True esplicito, e credenziali reali del medico."
        )
    if e_collaudo_sist(url) and consenti_collaudo_regionale is not True:
        raise AmbienteBloccato(
            f"Chiamata verso il collaudo SIST della Regione Puglia bloccata ({url}). "
            "Serve un'adesione al collaudo concessa da InnovaPuglia e consenti_collaudo_regionale=True esplicito."
        )
    if e_collaudo_fvg(url) and consenti_collaudo_regionale is not True:
        raise AmbienteBloccato(
            f"Chiamata verso il collaudo del SAR della Regione Friuli-Venezia Giulia bloccata ({url}). "
            "Serve un accreditamento concesso da Insiel (codice ProdottoCME, certificati di collaudo) "
            "e consenti_collaudo_regionale=True esplicito."
        )
    if e_collaudo_umbria(url) and consenti_collaudo_regionale is not True:
        raise AmbienteBloccato(
            f"Chiamata verso l'ambiente di test del SAR della Regione Umbria bloccata ({url}). "
            "È un sistema della Regione: servono un'adesione concordata con PuntoZero / Regione Umbria "
            "(AdesioneUmbria) e consenti_collaudo_regionale=True esplicito."
        )
    piemontesi = {h for h in host_normalizzati(url) if _collaudo_piemonte(h)}
    if piemontesi:
        dichiarati: set[str] = set()
        for h in (collaudi_piemonte or ()):
            dichiarati |= _varianti(str(h))
        if consenti_collaudo_regionale is not True or not piemontesi <= dichiarati:
            raise AmbienteBloccato(
                f"Chiamata verso un host di collaudo della Regione Piemonte / CSI bloccata ({url}). "
                "Gli host di SIRPED non sono pubblicati: servono l'autocertificazione avviata con la Regione "
                "(codice del gestionale e URL di test dati dal CSI), consenti_collaudo_regionale=True esplicito "
                "e l'host dichiarato in TrasportoHTTP(collaudi_piemonte=...)."
            )


@dataclass(frozen=True)
class Endpoint:
    """Percorsi dei servizi prescrittore (endpointPrescrittore.txt del kit MEF)."""

    INVIO = "/DemRicettaPrescrittoServicesWeb/services/demInvioPrescritto"
    VISUALIZZA = "/DemRicettaPrescrittoServicesWeb/services/demVisualizzaPrescritto"
    ANNULLA = "/DemRicettaPrescrittoServicesWeb/services/demAnnullaPrescritto"
    INTERROGA_NRE = "/DemRicettaInterrogazioniServicesWeb/services/demInterrogaNreUtilizzati"
