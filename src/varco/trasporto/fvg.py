# SPDX-License-Identifier: EUPL-1.2
"""Canale verso il SAR della Regione Friuli-Venezia Giulia (Insiel).

Fonte: *Specifiche di interfaccia applicativa del servizio SAR, Prescrizione ricetta
dematerializzata*, Insiel, Idof-dem-AT-01 dell'11/02/2026 («Documento a libera circolazione»), e i
WSDL/XSD di `wsdl_prescritto.zip` pubblicati su medicinrete.insiel.it.

Il SAR FVG «replica i servizi esposti dal Sistema di Accoglienza Centrale non introducendo
variazioni ai tracciati» (par. 2.2): stessi messaggi del SAC, con namespace propri, un attributo
`prodottoCme` e un attributo `versioneCR`. Cosa cambia sta qui, nel canale:

  - autenticazione (par. 2.1, cap. 4): niente Basic, niente pincode, niente Authorization2F.
    Due modalità:
      * CRS/CNS (predefinita): mutua autenticazione TLS con il certificato della carta del
        medico. Il certificato client sta nel `ssl.SSLContext` del trasporto
        (`TrasportoHTTP(contesto_tls=...)`); il canale riceve il certificato (DER) solo per
        controllare che il CF della carta sia quello del medico che invia (par. 2.2);
      * federata: header `Authorization: Bearer <access token>` e `X-JWT-ASSERTION: <ID token>`.
        Come si ottengono i due token lo dicono due allegati (ISAD-ISR-...) «da richiedere», non
        pubblici: il kit li TRASPORTA (protocollo `TokenFVG`), non li crea;
  - header `User-Agent` obbligatorio (par. 3.1):
    `<ProdottoCME>/<VersioneCME> <S.Operativo>/<Versione S.O.> <CFTitolare>/<DeviceId>`;
  - SOAPAction vuota, come nei WSDL (`soapAction=''`);
  - endpoint di collaudo (cap. 5). Quelli di produzione non sono pubblicati.

Il canale si rifiuta di partire verso un host non locale senza un'`AdesioneFVG`: il codice
ProdottoCME lo attribuisce Insiel «alla richiesta di accreditamento» (par. 3.1), non questo kit.

Scritto e verificato sulle specifiche: NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol
from urllib.parse import urlparse

from ..errori import ConfigurazioneNonValida
from .http import Richiesta, Trasporto, TrasportoHTTP, consegna
from .sac import RispostaGrezza
from .soap import imbusta, sbusta

_CF = re.compile(r"[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]")


def cf_del_certificato(der: bytes) -> str | None:
    """Il codice fiscale scritto nel soggetto del certificato (CN della CNS "CF/numero.hash",
    oppure serialNumber "TINIT-CF"). None se non c'è."""
    from cryptography import x509

    cert = x509.load_der_x509_certificate(der)
    trovati = {m for attr in cert.subject for m in _CF.findall(str(attr.value).upper())}
    return trovati.pop() if len(trovati) == 1 else None


# Versione dell'interfaccia (VersioneAddOn, par. 3.1, Tabella 2): entra nei namespace («-v1.0»).
VERSIONE_ADDON = "1.0"


class ModalitaFVG(str, Enum):
    CNS = "cns"  # par. 2.1.1, modalità predefinita: mTLS con CRS o Carta Operatore
    FEDERATA = "federata"  # par. 2.1.2: access token + ID token, via Piattaforma di Interoperabilità


class ServizioFVG(str, Enum):
    INVIO = "invio"
    VISUALIZZA = "visualizza"
    ANNULLA = "annulla"
    GESTORE_AUTORIZZAZIONI = "gestore_autorizzazioni"
    INTERROGA_NRE = "interroga_nre"


# Cap. 5, endpoint di COLLAUDO. Il cap. 5 scrive /SARWs/...; i WSDL dello zip pubblicato portano
# /SARSpecialistiInterni/... (docs/SAR_FVG.md, sez. 7): il kit segue il documento, che è più recente.
URL_COLLAUDO = {
    ModalitaFVG.CNS: {
        ServizioFVG.INVIO: "https://demtest.sanita.fvg.it/SARWs/InvioPrescrittoSecure",
        ServizioFVG.ANNULLA: "https://demtest.sanita.fvg.it/SARWs/annullaPrescrittoSecure",
        ServizioFVG.VISUALIZZA: "https://demtest.sanita.fvg.it/SARWs/visualizzaPrescrittoSecure",
        ServizioFVG.GESTORE_AUTORIZZAZIONI: "https://demtest.sanita.fvg.it/SARWs/GestoreAutorizzazioniSecure",
    },
    ModalitaFVG.FEDERATA: {
        ServizioFVG.INVIO: "https://apiweb-collaudo.sanita.fvg.it:8243/prescrizione-ssn-test/1.0.0/InvioPrescritto",
        ServizioFVG.ANNULLA: "https://apiweb-collaudo.sanita.fvg.it:8243/prescrizione-ssn-test/1.0.0/AnnullaPrescritto",
        ServizioFVG.VISUALIZZA: "https://apiweb-collaudo.sanita.fvg.it:8243/prescrizione-ssn-test/1.0.0/VisualizzaPrescritto",
        ServizioFVG.GESTORE_AUTORIZZAZIONI: (
            "https://apiweb-collaudo.sanita.fvg.it:8243/prescrizione-ssn-test/1.0.0/GestoreAutorizzazioni"
        ),
    },
}
# Nessun endpoint pubblicato per la lista degli NRE utilizzati: il WSDL dello zip punta a
# http://localhost:8070/SARWs_FVG_TRUNK/InterrogaNreUtil e il cap. 5 non la elenca.

# Percorsi verso un server LOCALE (strumenti/fvg_server_finto.py): gli stessi nomi del cap. 5.
PERCORSI_LOCALI = {
    ServizioFVG.INVIO: "/SARWs/InvioPrescrittoSecure",
    ServizioFVG.ANNULLA: "/SARWs/annullaPrescrittoSecure",
    ServizioFVG.VISUALIZZA: "/SARWs/visualizzaPrescrittoSecure",
    ServizioFVG.GESTORE_AUTORIZZAZIONI: "/SARWs/GestoreAutorizzazioniSecure",
    ServizioFVG.INTERROGA_NRE: "/SARWs/InterrogaNreUtil",
}


@dataclass(frozen=True)
class ApplicativoFVG:
    """Il software, come lo vuole il par. 3.1.

    `prodotto_cme`: codice ProdottoCME attribuito da Insiel con l'accreditamento (Tabella 1).
    `versione_cme`: versione del prodotto, decisa dal fornitore.
    `versione_cr`: versione del catalogo regionale delle prestazioni implementato, con la patch
    (es. "1.4.4"); va nell'attributo `versioneCR` delle ricette di specialistica.
    """

    prodotto_cme: str
    versione_cme: str
    versione_cr: str | None = None


@dataclass(frozen=True)
class PostazioneFVG:
    """La postazione del medico, per lo User-Agent (par. 3.1).

    `cf_titolare`: CF del medico titolare a cui l'installazione è intestata.
    `id_postazione` (DeviceId): il par. 3.1 chiede il MAC address, oppure «altre informazioni (es.
    numero di licenza)» che distinguano le installazioni. Il kit non legge il MAC da sé: lo
    sceglie chi integra (un numero di licenza espone meno della scheda di rete).
    """

    sistema_operativo: str
    versione_so: str
    cf_titolare: str
    id_postazione: str


@dataclass(frozen=True)
class AdesioneFVG:
    """Prova che il software ha un accreditamento al SAR FVG: senza, il canale non parte verso la Regione.

    `riferimento`: protocollo o identificativo della richiesta di accreditamento accolta da Insiel.
    """

    riferimento: str
    applicativo: ApplicativoFVG

    def __post_init__(self):
        if not (self.riferimento or "").strip():
            raise ConfigurazioneNonValida("AdesioneFVG: serve il riferimento dell'accreditamento")
        if not (self.applicativo.prodotto_cme or "").strip():
            raise ConfigurazioneNonValida("AdesioneFVG: serve il codice ProdottoCME attribuito da Insiel")


class TokenFVG(Protocol):
    """Soluzione federata (par. 2.1.2): chi integra fornisce i due token, il kit li mette negli header.

    `access_token()`: dal servizio OAuth2 della Piattaforma di Interoperabilità Regionale
    (allegato [6], da richiedere a Insiel). `id_token()`: JWT utente AAL2 con i dati richiesti dal
    SAC (allegato [5], da richiedere a Insiel). Il kit non sa costruirli: le specifiche non sono pubbliche.
    """

    def access_token(self) -> str: ...

    def id_token(self) -> str: ...


def user_agent(applicativo: ApplicativoFVG, postazione: PostazioneFVG) -> str:
    """`<ProdottoCME>/<VersioneCME> <S.Operativo>/<Versione S.O.> <CFTitolare>/<DeviceId>` (par. 3.1)."""
    parti = {
        "ProdottoCME": applicativo.prodotto_cme,
        "VersioneCME": applicativo.versione_cme,
        "S.Operativo": postazione.sistema_operativo,
        "Versione S.O.": postazione.versione_so,
        "CFTitolare": postazione.cf_titolare,
        "DeviceId": postazione.id_postazione,
    }
    for nome, valore in parti.items():
        if not (valore or "").strip():
            raise ConfigurazioneNonValida(f"User-Agent FVG: {nome} mancante")
        if "/" in valore or any(c in valore for c in "\r\n"):
            raise ConfigurazioneNonValida(f"User-Agent FVG: {nome} non può contenere '/' o a capo")
    for nome in ("ProdottoCME", "VersioneCME", "Versione S.O.", "CFTitolare", "DeviceId"):
        if " " in parti[nome]:
            # lo spazio separa le tre coppie; solo il nome del sistema operativo ne ha uno nell'esempio
            # ufficiale («WINDOWS NT/10.0»), e lì il formato diventa ambiguo (docs/SAR_FVG.md, sez. 7)
            raise ConfigurazioneNonValida(f"User-Agent FVG: {nome} non può contenere spazi")
    return (f"{applicativo.prodotto_cme}/{applicativo.versione_cme} "
            f"{postazione.sistema_operativo}/{postazione.versione_so} "
            f"{postazione.cf_titolare}/{postazione.id_postazione}")


def _locale(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


class CanaleFVG:
    """Canale SOAP verso il SAR FVG. `cf_medico`: il medico che invia, cioè il titolare della carta
    (o del token): il titolare della ricetta, oppure il sostituto quando prescrive lui."""

    def __init__(
        self,
        cf_medico: str,
        postazione: PostazioneFVG,
        *,
        adesione: AdesioneFVG | None = None,
        modalita: ModalitaFVG = ModalitaFVG.CNS,
        certificato_carta: bytes | None = None,
        token: TokenFVG | None = None,
        base_url: str | None = None,
        url: dict[ServizioFVG, str] | None = None,
        applicativo_di_prova: ApplicativoFVG | None = None,
        trasporto: Trasporto | None = None,
    ):
        """
        `certificato_carta`: certificato X.509 (DER) della CRS/CNS usato per la mutua autenticazione.
        Serve al controllo del par. 2.2 (CF della carta = CF del medico che invia); obbligatorio in
        modalità CNS verso la Regione. La chiave privata NON passa di qui: sta nel contesto TLS.
        `base_url`: solo verso localhost (server finto); i percorsi sono quelli del cap. 5.
        `url`: endpoint espliciti per servizio, per esempio quello della lista degli NRE utilizzati,
        che le specifiche non pubblicano. Valgono come gli altri: la guardia li controlla.
        """
        self._cf_medico = cf_medico.upper()
        self.postazione = postazione
        # una stringa («cns») sceglieva gli endpoint ma saltava i controlli fatti con `is ModalitaFVG.CNS`
        # (issue #7): si normalizza all'enum, e un valore sconosciuto si rifiuta qui
        try:
            modalita = ModalitaFVG(modalita)
        except ValueError:
            raise ConfigurazioneNonValida(f"modalità FVG sconosciuta: {modalita!r} (ammesse: cns, federata)") from None
        self.modalita = modalita
        self.token = token
        self.trasporto = trasporto or TrasportoHTTP()
        if base_url is not None:
            if not _locale(base_url):
                raise ConfigurazioneNonValida("base_url vale solo verso localhost: verso la Regione si usano "
                                              "gli endpoint del cap. 5 o `url` espliciti")
            base = base_url.rstrip("/")
            endpoint = {s: base + p for s, p in PERCORSI_LOCALI.items()}
        else:
            endpoint = dict(URL_COLLAUDO[modalita])
        endpoint.update(url or {})
        # Sola lettura: gli endpoint si decidono qui, dove si controllano adesione e carta. Cambiarli
        # dopo aggirava entrambi (revisione esterna giro 2, 4-sar-fvg N1); `chiama` li ricontrolla comunque.
        self._url = MappingProxyType(endpoint)
        verso_regione = any(not _locale(u) for u in self._url.values())
        if verso_regione:
            if adesione is None:
                raise ConfigurazioneNonValida(
                    "Verso il SAR FVG serve un'AdesioneFVG: il codice ProdottoCME e i certificati di collaudo "
                    "li rilascia Insiel con l'accreditamento"
                )
            if applicativo_di_prova is not None:
                raise ConfigurazioneNonValida("applicativo_di_prova vale solo verso localhost")
            applicativo = adesione.applicativo
        else:
            applicativo = adesione.applicativo if adesione else applicativo_di_prova
            if applicativo is None:
                raise ConfigurazioneNonValida("serve un'AdesioneFVG o, verso localhost, un applicativo_di_prova")
        self.adesione = adesione
        self.applicativo = applicativo
        self._carta_verificata = certificato_carta is not None
        self._cf_carta: str | None = None
        self._user_agent = user_agent(applicativo, postazione)  # controlla subito il formato
        if modalita is ModalitaFVG.FEDERATA:
            if token is None:
                raise ConfigurazioneNonValida("modalità federata: servono access token e ID token (TokenFVG)")
        elif token is not None:
            raise ConfigurazioneNonValida("i token valgono solo nella modalità federata")
        if certificato_carta is not None:
            cf_carta = cf_del_certificato(certificato_carta)
            self._cf_carta = cf_carta
            if cf_carta != self.cf_medico:
                # par. 2.2: inviare solo se il CF della carta nel lettore è quello del medico inviante
                raise ConfigurazioneNonValida(
                    f"la carta è di {cf_carta or 'un CF non leggibile'}, il medico che invia è {self.cf_medico}"
                )
        elif modalita is ModalitaFVG.CNS and verso_regione:
            raise ConfigurazioneNonValida(
                "modalità CNS: serve il certificato della carta (certificato_carta) per controllare che sia "
                "del medico che invia (par. 2.2)"
            )

    @property
    def cf_medico(self) -> str:
        """Il medico che invia. Sola lettura: il confronto con la carta (par. 2.2) si fa alla
        costruzione e di nuovo a ogni chiamata; per un altro medico serve un altro canale (issue #7)."""
        return self._cf_medico

    @cf_medico.setter
    def cf_medico(self, valore: str) -> None:
        raise AttributeError("cf_medico è di sola lettura: per un altro medico (o il sostituto) crea un altro "
                             "CanaleFVG, con la sua carta (par. 2.2)")

    @property
    def url(self) -> Mapping[ServizioFVG, str]:
        """Endpoint per servizio, in sola lettura: si passano al costruttore (`base_url`, `url`)."""
        return self._url

    def url_di(self, servizio: ServizioFVG) -> str:
        try:
            u = self._url[servizio]
        except KeyError:
            raise ConfigurazioneNonValida(
                f"nessun endpoint pubblicato per {servizio.value}: va indicato con url={{ServizioFVG.{servizio.name}: ...}}"
            ) from None
        self._verifica_destinazione(u)
        return u

    def _verifica_destinazione(self, u: str) -> None:
        """Le «due serrature» (docs/SAR_FVG.md) come invariante di OGNI chiamata, non solo del
        costruttore: verso la Regione servono l'AdesioneFVG, il suo applicativo e, in modalità CNS,
        la carta del medico controllata (par. 2.2). Revisione esterna giro 2, 4-sar-fvg N1.
        Se c'è una carta, il suo CF si confronta con `cf_medico` a OGNI chiamata, anche verso localhost:
        aggirando la proprietà (`vars(canale)`), la ricetta del sostituto partiva con la carta del
        titolare (issue #7)."""
        if self._carta_verificata and self._cf_carta != self._cf_medico:
            raise ConfigurazioneNonValida(
                f"la carta è di {self._cf_carta or 'un CF non leggibile'}, il medico che invia è {self._cf_medico} (par. 2.2)"
            )
        if self.modalita is not ModalitaFVG.CNS and self.modalita is not ModalitaFVG.FEDERATA:
            raise ConfigurazioneNonValida(f"modalità FVG non valida: {self.modalita!r}")
        if _locale(u):
            return
        if self.adesione is None or self.applicativo is not self.adesione.applicativo:
            raise ConfigurazioneNonValida(
                f"{u}: verso il SAR FVG serve un'AdesioneFVG (e il suo applicativo), decisa alla costruzione del canale"
            )
        if self.modalita is ModalitaFVG.CNS and not self._carta_verificata:
            raise ConfigurazioneNonValida(
                f"{u}: modalità CNS verso la Regione senza il certificato della carta (certificato_carta, par. 2.2)"
            )

    def _intestazioni(self) -> dict[str, str]:
        h = {
            "Content-Type": "text/xml;charset=UTF-8",
            "SOAPAction": '""',  # WSDL: soapAction=''
            "User-Agent": self._user_agent,
        }
        if self.modalita is ModalitaFVG.FEDERATA:
            h["Authorization"] = f"Bearer {self.token.access_token()}"
            h["X-JWT-ASSERTION"] = self.token.id_token()
        return h

    def chiama(self, servizio: ServizioFVG, corpo: ET.Element) -> tuple[ET.Element, RispostaGrezza]:
        richiesta = Richiesta(
            servizio=f"fvg.{servizio.value}",
            url=self.url_di(servizio),
            corpo=imbusta(corpo),
            intestazioni=self._intestazioni(),
        )
        risposta = consegna(self.trasporto, richiesta)  # guardia anche con un trasporto proprio
        grezza = RispostaGrezza(richiesta.corpo, risposta.corpo, risposta.stato_http, risposta.durata_s)
        return sbusta(risposta.corpo, risposta.stato_http), grezza
