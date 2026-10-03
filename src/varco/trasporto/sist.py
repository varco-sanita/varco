# SPDX-License-Identifier: EUPL-1.2
"""Canale verso il SIST della Regione Puglia (PDD ASL, componente CVP: ciclo di vita prescrittivo).

Aggiunge a un `Trasporto` generico ciò che è specifico del SIST (Specifiche di integrazione
SIST v4.03.27 del 16/09/2026):
  - URL della PDD ASL: collaudo `pddasl-preprod...:8181/aslba_test`, produzione
    `pdd-virtasl...:8181/asl<sigla>` (par. 6.1);
  - SOAP 1.1 con SOAPAction dal WSDL (`urn:sist:pddsasl:bindings:1.0:CVPPortType#<operazione>`);
  - header WS-Security con firma X.509 del Timestamp (par. 5.1.1, vedi `wssecurity.py`);
  - i dati di contesto che OGNI operazione porta nel corpo: `datiOperatore` (CF, struttura,
    ruolo istituzionale) e `datiApplicativo` (nome, produttore, versione, nonce, created,
    applDigest = base64(SHA-1(nonce + created + codice applicativo))).

Differenze dal SAC che stanno qui e non nel modello: niente Basic auth, niente pincode,
niente cifratura SanitelCF; al loro posto il certificato della CNS e il codice applicativo
rilasciato con l'adesione.

Il canale si rifiuta di partire verso un host non locale senza un'`AdesioneSIST`: il codice
applicativo e il riferimento dell'adesione li dà InnovaPuglia, non questo kit.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import re
import secrets
import string
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable
from urllib.parse import urlparse

from ..ambienti import HOST_SIST_COLLAUDO, HOST_SIST_PRODUZIONE
from ..errori import ConfigurazioneNonValida
from .http import Richiesta, Trasporto, TrasportoHTTP, consegna
from .sac import RispostaGrezza
from .soap import NS_SOAPENV, sbusta
from .wssecurity import ChiaveOperatore, intestazione_security

SIGLE_ASL = ("ba", "bt", "br", "le", "fg", "ta")
PORTA = 8181
# par. 6.1: "l'endpoint di collaudo prevede solo ASL aslba_test"
PERCORSO_COLLAUDO = "/aslba_test"
SOAP_ACTION = "urn:sist:pddsasl:bindings:1.0:CVPPortType#{}"
RUOLO_MMG = "RIS000043"  # par. 8.1: Medico di Medicina Generale (anche PLS, stesso codice nel testo)


class AmbienteSIST(str, Enum):
    COLLAUDO = "collaudo"
    PRODUZIONE = "produzione"


def url_base(ambiente: AmbienteSIST, sigla_asl: str = "ba") -> str:
    if ambiente is AmbienteSIST.COLLAUDO:
        return f"https://{HOST_SIST_COLLAUDO}:{PORTA}{PERCORSO_COLLAUDO}"
    if sigla_asl not in SIGLE_ASL:
        raise ConfigurazioneNonValida(f"sigla ASL non prevista: {sigla_asl!r} (ammesse: {', '.join(SIGLE_ASL)})")
    return f"https://{HOST_SIST_PRODUZIONE}:{PORTA}/asl{sigla_asl}"


@dataclass(frozen=True)
class OperatoreSIST:
    """`datiOperatore`: chi chiama. Struttura e ruolo vengono da getRuoliStruttureOperatore (AAA)."""

    codice_fiscale: str
    codice_struttura: str  # es. "160114" negli esempi ufficiali
    ruolo: str = RUOLO_MMG


@dataclass(frozen=True)
class ApplicativoSIST:
    """`datiApplicativo`: il software, come censito sul SIST. Il codice applicativo entra solo
    nell'applDigest e non viene mai scritto nella richiesta."""

    nome: str
    produttore: str
    versione: str
    codice_applicativo: str = field(repr=False)


@dataclass(frozen=True)
class AdesioneSIST:
    """Prova che il software ha un'adesione al SIST: senza, il canale non parte verso la Regione.

    `riferimento`: protocollo o identificativo dell'adesione concessa da InnovaPuglia.
    """

    riferimento: str
    applicativo: ApplicativoSIST

    def __post_init__(self):
        if not (self.riferimento or "").strip():
            raise ConfigurazioneNonValida("AdesioneSIST: serve il riferimento dell'adesione")
        if not (self.applicativo.codice_applicativo or "").strip():
            raise ConfigurazioneNonValida("AdesioneSIST: serve il codice applicativo rilasciato con l'adesione")


@dataclass(frozen=True)
class DatiChiamata:
    """I valori di datiOperatore e datiApplicativo di UNA chiamata (nonce e created nuovi ogni volta)."""

    operatore: OperatoreSIST
    nome: str
    produttore: str
    versione: str
    nonce: str
    created: str
    appl_digest: str


def appl_digest(nonce: str, created: str, codice_applicativo: str) -> str:
    """base64(SHA-1(nonce + created + codiceapplicativo)): javadoc condivisi.vo.DatiApplicativo."""
    return base64.b64encode(hashlib.sha1((nonce + created + codice_applicativo).encode("utf-8")).digest()).decode()  # noqa: S324


def _nonce() -> str:
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(20))  # "Stringa casuale di 20 caratteri alfanumerici"


def _created(adesso: _dt.datetime) -> str:
    # javadoc: "aaaa-mm-ggThh:mm:ssZ (es. 2009-08-16T12:07:00+0100)": ora locale col suo scarto
    try:
        from zoneinfo import ZoneInfo

        locale = adesso.astimezone(ZoneInfo("Europe/Rome"))
    except Exception:
        locale = adesso.astimezone()
    return locale.strftime("%Y-%m-%dT%H:%M:%S%z")


_CF = re.compile(r"[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]")


def cf_del_certificato(der: bytes) -> str | None:
    """Il codice fiscale scritto nel soggetto del certificato (CN della CNS "CF/numero.hash",
    oppure serialNumber "TINIT-CF"). None se non c'è."""
    from cryptography import x509

    cert = x509.load_der_x509_certificate(der)
    trovati = {m for attr in cert.subject for m in _CF.findall(str(attr.value).upper())}
    return trovati.pop() if len(trovati) == 1 else None


def _locale(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


class CanaleSIST:
    def __init__(
        self,
        operatore: OperatoreSIST,
        chiave: ChiaveOperatore,
        *,
        adesione: AdesioneSIST | None = None,
        ambiente: AmbienteSIST = AmbienteSIST.COLLAUDO,
        sigla_asl: str = "ba",
        base_url: str | None = None,
        applicativo_di_prova: ApplicativoSIST | None = None,
        trasporto: Trasporto | None = None,
        orologio: Callable[[], _dt.datetime] | None = None,
    ):
        self.operatore = operatore
        self.chiave = chiave
        self.ambiente = ambiente
        self.base_url = (base_url or url_base(ambiente, sigla_asl)).rstrip("/")
        if _locale(self.base_url):
            # server finto sulla propria macchina: basta un applicativo di prova
            applicativo = adesione.applicativo if adesione else applicativo_di_prova
            if applicativo is None:
                raise ConfigurazioneNonValida("serve un'AdesioneSIST o, verso localhost, un applicativo_di_prova")
        else:
            if adesione is None:
                raise ConfigurazioneNonValida(
                    "Verso il SIST (collaudo o produzione) serve un'AdesioneSIST: codice applicativo e "
                    "riferimento dell'adesione li rilascia InnovaPuglia"
                )
            if applicativo_di_prova is not None:
                raise ConfigurazioneNonValida("applicativo_di_prova vale solo verso localhost")
            applicativo = adesione.applicativo
        self.adesione = adesione
        self.applicativo = applicativo
        self.trasporto = trasporto or TrasportoHTTP()
        self._orologio = orologio or (lambda: _dt.datetime.now(_dt.timezone.utc))
        cf_cert = cf_del_certificato(chiave.certificato_der())
        if cf_cert is not None and cf_cert != operatore.codice_fiscale.upper():
            # il SIST risponderebbe 000231 "Il codice fiscale della smart card non corrisponde a quello dell'operatore"
            raise ConfigurazioneNonValida(
                f"il certificato è di {cf_cert}, l'operatore dichiarato è {operatore.codice_fiscale}"
            )

    def dati_chiamata(self) -> DatiChiamata:
        nonce, created = _nonce(), _created(self._orologio())
        a = self.applicativo
        return DatiChiamata(self.operatore, a.nome, a.produttore, a.versione, nonce, created,
                            appl_digest(nonce, created, a.codice_applicativo))

    def busta(self, corpo: ET.Element) -> bytes:
        security = intestazione_security(self.chiave, prefisso_soap="soapenv", adesso=self._orologio())
        body = ET.tostring(corpo, encoding="unicode")
        return (
            "<?xml version='1.0' encoding='utf-8'?>\n"
            f'<soapenv:Envelope xmlns:soapenv="{NS_SOAPENV}"><soapenv:Header>{security}</soapenv:Header>'
            f"<soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>"
        ).encode("utf-8")

    def chiama(self, operazione: str, corpo: ET.Element, servizio: str = "CVPService") -> tuple[ET.Element, RispostaGrezza]:
        richiesta = Richiesta(
            servizio=f"sist.{operazione}",
            url=f"{self.base_url}/{servizio}",
            corpo=self.busta(corpo),
            intestazioni={
                "Content-Type": "text/xml;charset=UTF-8",
                "SOAPAction": f'"{SOAP_ACTION.format(operazione)}"',
                "User-Agent": "varco/0.1 (+EUPL-1.2)",
            },
        )
        risposta = consegna(self.trasporto, richiesta)  # guardia anche con un trasporto proprio
        grezza = RispostaGrezza(richiesta.corpo, risposta.corpo, risposta.stato_http, risposta.durata_s)
        return sbusta(risposta.corpo, risposta.stato_http), grezza
