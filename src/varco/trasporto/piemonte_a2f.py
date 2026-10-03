# SPDX-License-Identifier: EUPL-1.2
"""Id-Sessione di SIRPED in modalità «mail certificata»: CreateAuth, CheckToken, RevokeAuth.

REL-STC-01 V04 (02/03/2026), par. 4.2: «I WSDL e gli XSD dei servizi sono gli stessi previsti da
Sistema TS, con la personalizzazione regionale del contenuto di alcuni campi». Schemi: kit A2F del
Sistema TS ver. 20250902 (`sts-a2f-service.v0.1.xsd`, `sts-a2f-service-data-type.v0.1.xsd`).

Cosa è regionale (par. 4.2.1-4.2.3):
  - `userId` = utente RUPAR; `identificativo` tipo P, valore = pincode cifrato col certificato della
    Regione (lo stesso degli altri servizi); `cfUtente`; `codRegione` = 010; `codAslAo` = codice ASR;
  - `contesto` = RICETTA-DEM; `applicazione` = permessi separati da spazio (prescrizione,
    erogazione, presa_in_carico): solo in CreateAuth;
  - `infoAggiuntive` con chiave APP e valore `<codice gestionale>_<azienda>`;
  - nell'ambiente di TEST la risposta di CreateAuth porta nelle `comunicazioni` permessi, token,
    dataFineValidita e Working-mode=TEST (par. 4.2.1, V04). In produzione l'Id-Sessione arriva SOLO
    per mail: il kit non lo legge mai dalla risposta, a meno che il chiamante non lo chieda.

Esito: `codEsito` 0 positivo, 1 negativo; errori con tipo W (avviso), E (errore), F (fatale) (par. 4.2.4).

Scritto e verificato sulle specifiche: NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Iterable

from ..errori import ConfigurazioneNonValida
from .piemonte import (
    CODICE_REGIONE_PIEMONTE,
    PERMESSI,
    SOAP_ACTION_A2F,
    CanalePiemonte,
    ModalitaPiemonte,
    ServizioPiemonte,
    controlla_id_sessione,
)
from .sac import RispostaGrezza

NS_AUT = "http://authservice.xsd.wsdl.auth.a2f.sts.sanita.finanze.it"
NS_DAT = "http://datatype.xsd.wsdl.auth.a2f.sts.sanita.finanze.it"
ET.register_namespace("aut", NS_AUT)
ET.register_namespace("dat", NS_DAT)

CONTESTO = "RICETTA-DEM"
# `applicazioneType` dello XSD A2F: al massimo 30 caratteri. I tre permessi insieme ne fanno 39:
# la combinazione completa non si può mandare (docs/SAR_PIEMONTE.md, sez. 7).
LUNGHEZZA_MAX_APPLICAZIONE = 30
# `stringTypeMax256` di identificativo/valore (pincode cifrato in base64)
LUNGHEZZA_MAX_VALORE = 256

Cifra = Callable[[str], str]


@dataclass(frozen=True)
class UtenteA2F:
    """Chi chiede l'Id-Sessione: il medico, con i suoi dati regionali (REL-STC-01, par. 4.2.1)."""

    cf: str
    codice_asl: str  # codAslAo: codice ASR a 3 caratteri
    codice_ssa: str | None = None  # codSsa: «opzionale»; se c'è, 5-6 caratteri (codSsaType)

    def problemi(self) -> list[str]:
        p = []
        if len(self.cf or "") != 16:
            p.append("cfUtente: servono 16 caratteri")
        if len(self.codice_asl or "") != 3:
            p.append("codAslAo: servono 3 caratteri")
        if self.codice_ssa is not None and not 5 <= len(self.codice_ssa) <= 6:
            p.append("codSsa: 5 o 6 caratteri (codSsaType); se non c'è, si omette")
        return p


def _el(parent: ET.Element, ns: str, tag: str, testo: str | None = None) -> ET.Element:
    e = ET.SubElement(parent, f"{{{ns}}}{tag}")
    if testo is not None:
        e.text = testo
    return e


def _testata(radice: ET.Element, utente_rupar: str, pincode: str, cifra: Cifra, cf: str) -> None:
    _el(radice, NS_AUT, "userId", utente_rupar)
    ident = _el(radice, NS_AUT, "identificativo")
    _el(ident, NS_DAT, "tipo", "P")
    cifrato = cifra(pincode)  # cifrato verso il SAR, in chiaro solo verso una CIL
    if len(cifrato) > LUNGHEZZA_MAX_VALORE:
        # valore è stringTypeMax256: con RSA PKCS#1 v1.5 e base64 ci stanno chiavi fino a 1536 bit.
        # SanitelCF è a 1024 bit; la dimensione della chiave del certificato della Regione non è pubblicata.
        raise ConfigurazioneNonValida(
            f"pincode cifrato di {len(cifrato)} caratteri: lo XSD A2F (identificativo/valore) ne ammette "
            f"{LUNGHEZZA_MAX_VALORE}, cioè una chiave RSA di 1536 bit al massimo (docs/SAR_PIEMONTE.md, sez. 7)"
        )
    _el(ident, NS_DAT, "valore", cifrato)
    _el(radice, NS_AUT, "cfUtente", cf)


def _app(radice: ET.Element, valore_app: str) -> None:
    info = _el(radice, NS_AUT, "infoAggiuntive")
    opz = _el(info, NS_DAT, "opzione")
    _el(opz, NS_DAT, "chiave", "APP")
    _el(opz, NS_DAT, "valore", valore_app)


def applicazione(permessi: Iterable[str]) -> str:
    """Il campo `applicazione` di CreateAuth: permessi separati da spazio, nell'ordine dati, senza doppioni."""
    elenco = list(dict.fromkeys(permessi))
    ignoti = [p for p in elenco if p not in PERMESSI]
    if not elenco or ignoti:
        raise ConfigurazioneNonValida(f"permessi ammessi: {', '.join(PERMESSI)} (ricevuti: {elenco})")
    valore = " ".join(elenco)
    if len(valore) > LUNGHEZZA_MAX_APPLICAZIONE:
        raise ConfigurazioneNonValida(
            f"'{valore}' supera i {LUNGHEZZA_MAX_APPLICAZIONE} caratteri di applicazioneType (XSD A2F): "
            "la specifica regionale ammette i tre permessi insieme, lo schema no. Chiederne al massimo due."
        )
    return valore


def richiesta_crea(utente_rupar: str, pincode: str, cifra: Cifra, utente: UtenteA2F, valore_app: str,
                   permessi: Iterable[str] = ("prescrizione",)) -> ET.Element:
    """<CreateAuthReq>, nell'ordine dello XSD. codiceStruttura e opzioni «non valorizzare»: omessi."""
    problemi = utente.problemi()
    if problemi:
        raise ConfigurazioneNonValida("; ".join(problemi))
    radice = ET.Element(f"{{{NS_AUT}}}CreateAuthReq")
    _testata(radice, utente_rupar, pincode, cifra, utente.cf)
    _el(radice, NS_AUT, "codRegione", CODICE_REGIONE_PIEMONTE)
    _el(radice, NS_AUT, "codAslAo", utente.codice_asl)
    if utente.codice_ssa is not None:
        _el(radice, NS_AUT, "codSsa", utente.codice_ssa)
    _el(radice, NS_AUT, "contesto", CONTESTO)
    _el(radice, NS_AUT, "applicazione", applicazione(permessi))
    _app(radice, valore_app)
    return radice


def richiesta_verifica(utente_rupar: str, pincode: str, cifra: Cifra, cf: str, id_sessione: str,
                       valore_app: str) -> ET.Element:
    """<CheckTokenReq>: `applicazione` «non valorizzare», quindi omessa."""
    radice = ET.Element(f"{{{NS_AUT}}}CheckTokenReq")
    _testata(radice, utente_rupar, pincode, cifra, cf)
    _el(radice, NS_AUT, "token", controlla_id_sessione(id_sessione))
    _el(radice, NS_AUT, "contesto", CONTESTO)
    _app(radice, valore_app)
    return radice


def richiesta_revoca(utente_rupar: str, pincode: str, cifra: Cifra, cf: str, id_sessione: str,
                     valore_app: str) -> ET.Element:
    """<RevokeAuthReq>: `applicazione` N.A. e `opzioni` facoltative: omesse."""
    radice = ET.Element(f"{{{NS_AUT}}}RevokeAuthReq")
    _testata(radice, utente_rupar, pincode, cifra, cf)
    _el(radice, NS_AUT, "token", controlla_id_sessione(id_sessione))
    _el(radice, NS_AUT, "contesto", CONTESTO)
    _app(radice, valore_app)
    return radice


# ------------------------------------------------------------------ lettura


@dataclass(frozen=True)
class ErroreA2F:
    codice: str
    descrizione: str | None = None
    tipo: str | None = None  # W avviso, E errore, F fatale (par. 4.2.4)

    @property
    def bloccante(self) -> bool:
        return (self.tipo or "E").strip().upper() != "W"


@dataclass(frozen=True)
class StatoToken:
    """infoToken di CheckToken: 0 valido, 1 revocato, 2 scaduto (par. 4.2.2)."""

    stato: int
    descrizione: str | None
    inizio_validita: str | None
    fine_validita: str | None

    @property
    def valido(self) -> bool:
        return self.stato == 0


@dataclass(frozen=True)
class EsitoIdSessione:
    """Risposta di CreateAuth, CheckToken o RevokeAuth."""

    codice: str  # 0 positivo, 1 negativo
    errori: tuple[ErroreA2F, ...] = ()
    info: tuple[tuple[str, str], ...] = ()
    comunicazioni: tuple[tuple[str, str], ...] = ()
    stato_token: StatoToken | None = None

    @property
    def ok(self) -> bool:
        return self.codice == "0" and not any(e.bloccante for e in self.errori)

    def info_di(self, chiave: str) -> str | None:
        return next((v for k, v in self.info if k == chiave), None)

    def comunicazione(self, codice: str) -> str | None:
        return next((m for c, m in self.comunicazioni if c.lower() == codice.lower()), None)

    @property
    def ambiente_test(self) -> bool:
        return (self.comunicazione("Working-mode") or "").strip().upper() == "TEST"

    @property
    def id_sessione_di_test(self) -> str | None:
        """L'Id-Sessione restituito nella risposta: SOLO nell'ambiente di test (par. 4.2.1, V04).
        In produzione arriva solo per mail; se una risposta di produzione lo portasse, il kit non lo usa."""
        return self.comunicazione("token") if self.ambiente_test else None

    @property
    def permessi(self) -> tuple[str, ...]:
        return tuple((self.comunicazione("permessi") or "").split())

    @property
    def fine_validita(self) -> str | None:
        return self.comunicazione("dataFineValidita")


def _nome(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _testo(el: ET.Element, nome: str) -> str | None:
    for c in el:
        if _nome(c.tag) == nome:
            return (c.text or "").strip()
    return None


def leggi_esito(el: ET.Element) -> EsitoIdSessione:
    """Lettore tollerante ai prefissi e alla forma: trova `errore`, `info`, `comunicazione` e `infoToken`
    ovunque sotto la radice (l'esempio del par. 4.2.4 mostra `errore` senza il contenitore `errori`
    che lo XSD prevede)."""
    if _nome(el.tag) not in ("CreateAuthRes", "CheckTokenRes", "RevokeAuthRes"):
        raise ValueError(f"risposta A2F inattesa: <{_nome(el.tag)}>")
    codice = _testo(el, "codEsito")
    if codice is None:
        raise ValueError("risposta A2F senza codEsito")
    errori, info, com, stato = [], [], [], None
    for x in el.iter():
        n = _nome(x.tag)
        if n == "errore":
            errori.append(ErroreA2F(_testo(x, "codEsito") or "", _testo(x, "descrEsito"), _testo(x, "tipoErrore")))
        elif n == "info" and _testo(x, "chiave") is not None:
            info.append((_testo(x, "chiave") or "", _testo(x, "valore") or ""))
        elif n == "comunicazione":
            com.append((_testo(x, "codice") or "", _testo(x, "messaggio") or ""))
        elif n == "infoToken":
            s = _testo(x, "stato")
            stato = StatoToken(int(s) if s and s.lstrip("-").isdigit() else -1, _testo(x, "descrizione"),
                               _testo(x, "dataInizioValidita"), _testo(x, "dataFineValidita"))
    return EsitoIdSessione(codice, tuple(errori), tuple(info), tuple(com), stato)


# ------------------------------------------------------------------ servizio


@dataclass
class UltimoScambioA2F:
    operazione: str
    grezza: RispostaGrezza


class ServizioIdSessione:
    """Gestione dell'Id-Sessione via mail certificata (modalità MAIL del canale).

    `cifratore`: cifra il pincode, con «lo stesso certificato già in uso per gli altri servizi della
    ricetta dematerializzata» (par. 4.2.1), cioè il certificato della Regione. Obbligatorio.
    """

    def __init__(self, canale: CanalePiemonte, cifratore, utente: UtenteA2F):
        if canale.modalita is not ModalitaPiemonte.MAIL:
            raise ConfigurazioneNonValida("l'Id-Sessione via mail vale solo nella modalità MAIL")
        if cifratore is None:
            raise ConfigurazioneNonValida("serve il cifratore del pincode (certificato della Regione Piemonte)")
        if utente.cf.upper() != canale.cf_medico:
            raise ConfigurazioneNonValida(f"cfUtente {utente.cf} diverso dal medico delle credenziali {canale.cf_medico}")
        self.canale = canale
        self.cifratore = cifratore
        self.utente = utente
        self.ultimo: UltimoScambioA2F | None = None

    def _chiama(self, operazione: str, corpo: ET.Element) -> EsitoIdSessione:
        el, grezza = self.canale.chiama(ServizioPiemonte.ID_SESSIONE, corpo, SOAP_ACTION_A2F[operazione])
        self.ultimo = UltimoScambioA2F(operazione, grezza)
        return leggi_esito(el)

    def _rupar(self) -> tuple[str, str]:
        c = self.canale.credenziali
        return c.utente, c.pincode

    def crea(self, permessi: Iterable[str] = ("prescrizione",)) -> EsitoIdSessione:
        """CreateAuth: chiede un nuovo Id-Sessione; quello precedente smette di valere (cap. 3)."""
        utente, pin = self._rupar()
        return self._chiama("create", richiesta_crea(utente, pin, self.cifratore.cifra, self.utente,
                                                     self.canale.gestionale.valore, permessi))

    def verifica(self, id_sessione: str) -> EsitoIdSessione:
        utente, pin = self._rupar()
        return self._chiama("checkToken", richiesta_verifica(utente, pin, self.cifratore.cifra, self.utente.cf,
                                                             id_sessione, self.canale.gestionale.valore))

    def revoca(self, id_sessione: str) -> EsitoIdSessione:
        utente, pin = self._rupar()
        return self._chiama("revoke", richiesta_revoca(utente, pin, self.cifratore.cifra, self.utente.cf,
                                                       id_sessione, self.canale.gestionale.valore))
