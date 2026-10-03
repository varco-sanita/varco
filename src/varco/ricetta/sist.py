# SPDX-License-Identifier: EUPL-1.2
"""`ServizioRicetta` verso il SIST della Regione Puglia (SAR): stesso contratto, altro canale.

Corrispondenza con il contratto (Specifiche di integrazione SIST v4.03.27, Appendice A,
"Scenari di integrazione per cartelle cliniche"):

  invia      -> chkPrescrizione (il SIST controlla e passa al SAC: NRE e codice di
                autenticazione) e poi setRegistraPrescrizione (CDA2 firmato, p7m).
  visualizza -> getPrescrizioneIdentificata: identificazione "forte", serve anche il CF
                dell'assistito (`cf_assistito`). È l'unico punto in cui il contratto si piega.
  annulla    -> setAnnullaPrescrizione.
  interroga_nre_utilizzati -> getPrescrizioniIdentificate (ricerca per periodo; niente NRE
                puntuale né lotto).

Il programma del medico usa `RicettaSIST` come usa `RicettaSAC`. Le differenze che non
stanno nel contratto sono parametri facoltativi di `invia` (oscuramento nel fascicolo,
maggior tutela, Piano Care Puglia) e il risultato più ricco (`EsitoInvioSAR`).

Scritto e verificato sulle specifiche: NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as _dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from ..errori import ErroreSOAP, ErroreTrasporto, RicettaNonValida
from ..trasporto.sac import RispostaGrezza
from ..trasporto.sist import CanaleSIST
from . import cda_sist, xml_sist
from .modello import (
    Assistito,
    CriteriNreUtilizzati,
    EsitoAnnullamento,
    EsitoInterrogazioneNre,
    EsitoInvioSAR,
    EsitoVisualizzazioneSAR,
    Messaggio,
    Ricetta,
    ora_italiana,
)

# SoapFaultException che il SIST usa per esiti APPLICATIVI (javadoc CVPPortType): diventano un
# Esito non riuscito con il messaggio, come i rifiuti del SAC. Gli altri Fault (sicurezza,
# applicativo non autorizzato, sistema) restano eccezioni: sono guasti, non risposte.
FAULT_APPLICATIVI = {
    "000004": "nessuna prescrizione corrisponde ai criteri",
    "000061": "manca il periodo di ricerca",
    "000279": "la prescrizione non è in stato di Prescritta",
    "000280": "prescrizione emessa da un altro medico",
    "910002": "prescrizione non identificata e SAC non disponibile",
}


def _fault_applicativo(e: ErroreSOAP) -> Messaggio | None:
    codice = xml_sist.codice_fault_sist(e)
    if codice not in FAULT_APPLICATIVI:
        return None
    return Messaggio(codice=codice, testo=e.faultstring, tipo="C")


class FirmatarioCAdES(Protocol):
    """Chi firma il CDA. In esercizio: la smart card o la firma remota del medico.

    Restituisce la busta CAdES (p7m, DER) con il CDA incluso. Il SIST controlla che il
    firmatario sia l'operatore che chiama (000271), che sia l'autore del CDA (000272) e che
    la firma sia dello stesso giorno del CDA e successiva (000303, 000304).
    """

    def firma_cades(self, dati: bytes) -> bytes: ...


@dataclass
class FirmatarioCAdESPKCS12:
    """CAdES-BES (attributo signing-certificate-v2) con chiave e certificato da un .p12, via pyHanko.
    Per le prove con un certificato di test, o per chi ha davvero la chiave in un file."""

    percorso_p12: str
    password: bytes | None = dataclasses.field(default=None, repr=False)

    def firma_cades(self, dati: bytes) -> bytes:
        import asyncio

        try:
            from pyhanko.sign import signers
        except ImportError as e:  # pragma: no cover
            raise ImportError("Serve pyHanko (pip install 'varco[firma]')") from e
        firmatario = signers.SimpleSigner.load_pkcs12(self.percorso_p12, passphrase=self.password)
        if firmatario is None:
            raise ValueError(f"impossibile leggere {self.percorso_p12}")
        busta = asyncio.run(firmatario.async_sign_general_data(dati, "sha256", detached=False, use_cades=True))
        return busta.dump()


@dataclass
class UltimoScambio:
    servizio: str
    grezza: RispostaGrezza


Anagrafica = Callable[[Assistito], object]  # -> varco.fse.modello.Paziente | None


class RicettaSIST:
    """Implementazione di `ServizioRicetta` sul SIST (PDD ASL, componente CVP)."""

    def __init__(
        self,
        canale: CanaleSIST,
        firmatario: FirmatarioCAdES,
        codici_regionali: Mapping[str, str] | Callable[[str], str],
        *,
        valida_localmente: bool = True,
        anagrafica: Anagrafica | None = None,
        medico=None,
        orologio: Callable[[], _dt.datetime] = ora_italiana,
    ):
        """
        `codici_regionali`: codice fiscale del medico -> codice regionale SIST (lo stesso che
        compare nell'author del CDA con OID 2.16.840.1.113883.2.9.2.160.4.2). Serve per il
        prescrittore e, in sostituzione, per il titolare.
        `anagrafica`: facoltativa, dall'Assistito della ricetta al `Paziente` (modulo FSE) con
        nome, sesso, nascita e residenza; per gli assistiti in anagrafe regionale basta il CF.
        `medico`: facoltativo, `fse.modello.Medico` per il nome nel CDA.
        """
        self.canale = canale
        self.firmatario = firmatario
        self._codici = codici_regionali
        self.valida_localmente = valida_localmente
        self.anagrafica = anagrafica
        self.medico = medico
        self._orologio = orologio
        self.ultimo: UltimoScambio | None = None

    # ------------------------------------------------------------------ supporto

    def codice_regionale(self, cf: str) -> str:
        if callable(self._codici):
            return self._codici(cf)
        return self._codici[cf]  # KeyError se manca: problemi_sist lo segnala prima

    def _chiama(self, operazione: str, corpo):
        el, grezza = self.canale.chiama(operazione, corpo)
        self.ultimo = UltimoScambio(operazione, grezza)
        return el

    def _verifica_operatore(self, ricetta: Ricetta) -> None:
        """Prescrive chi ha la CNS: il sostituto se c'è (in codMedicoPrescrittore), altrimenti il titolare."""
        sostituto = ricetta.prescrittore.codice_fiscale_sostituto
        operatore = self.canale.operatore.codice_fiscale
        atteso = sostituto or ricetta.prescrittore.codice_fiscale
        if operatore != atteso:
            raise RicettaNonValida(
                [f"l'operatore che firma e chiama ({operatore}) non è il medico che prescrive ({atteso}): "
                 "il SIST rifiuterebbe la firma (000271)"]
            )

    def _cf_operatore(self, cf_medico: str | None) -> str:
        cf = self.canale.operatore.codice_fiscale
        if cf_medico and cf_medico != cf:
            raise ValueError(f"nel SIST chiama sempre l'operatore della CNS ({cf}), non {cf_medico}")
        return cf

    # ------------------------------------------------------------------ contratto

    def invia(
        self,
        ricetta: Ricetta,
        *,
        oscurato: bool = False,
        maggior_tutela: bool = False,
        id_pcp: str | None = None,
    ) -> EsitoInvioSAR:
        """Controllo (chkPrescrizione) e registrazione del CDA firmato (setRegistraPrescrizione).

        `oscurato`: oscuramento della prescrizione nel fascicolo, per volontà dell'assistito.
        `maggior_tutela`: dati soggetti a maggior tutela dell'anonimato (confidentialityCode V).
        `id_pcp`: identificativo del Piano Care Puglia, se la prescrizione ne fa parte.

        Tutto ciò che si può rifiutare in locale si rifiuta PRIMA di chkPrescrizione: dopo, la
        ricetta esiste già al SAC. Da lì in poi nessun errore locale (CDA, firma, CNS tolta) esce
        come eccezione: torna un `EsitoInvioSAR` con `da_ripetere`, che porta con sé oscuramento,
        Piano Care e ricetta, e `ripeti_registrazione(esito)` lo completa (revisione esterna 02/10/2026).
        """
        problemi = cda_sist.problemi_righe_cda(ricetta)  # sempre: senza, il CDA non si scrive
        if self.valida_localmente:
            problemi = ricetta.problemi() + xml_sist.problemi_sist(ricetta, self.codice_regionale) + problemi
        if problemi:
            raise RicettaNonValida(problemi)
        if self.valida_localmente:
            self._verifica_operatore(ricetta)
        paziente = self.anagrafica(ricetta.assistito) if self.anagrafica else None
        self._codici_cda(ricetta)  # KeyError qui, non dopo il controllo
        corpo = xml_sist.richiesta_chk(ricetta, self.canale.dati_chiamata(), self.codice_regionale, paziente)
        esito = xml_sist.leggi_chk(self._chiama("chkPrescrizione", corpo))
        if not esito.ok:
            return esito
        esito = dataclasses.replace(esito, oscurato=oscurato, id_pcp=id_pcp, maggior_tutela=maggior_tutela,
                                    ricetta=ricetta, paziente=paziente)
        # Da qui la ricetta esiste: qualunque errore torna come esito recuperabile, mai come eccezione,
        # altrimenti chi chiama rifà invia() e nasce una seconda ricetta (revisione esterna giro 3, residuo
        # del bug 2 del giro 1: l'orologio del canale che fallisce preparando setRegistraPrescrizione).
        try:
            return self.ripeti_registrazione(esito)
        except Exception as e:  # noqa: BLE001
            return dataclasses.replace(esito, registrato=False,
                                       errore_registrazione=f"registrazione non riuscita: {type(e).__name__}: {e}")

    def _codici_cda(self, ricetta: Ricetta) -> tuple[str, str | None]:
        sostituto = ricetta.prescrittore.codice_fiscale_sostituto
        prescrittore = self.codice_regionale(sostituto or ricetta.prescrittore.codice_fiscale)
        sostituito = self.codice_regionale(ricetta.prescrittore.codice_fiscale) if sostituto else None
        return prescrittore, sostituito

    def _firma(self, esito: EsitoInvioSAR) -> EsitoInvioSAR:
        """CDA e firma CAdES. Il CDA nasce adesso: la firma deve essere dello stesso giorno e
        successiva (000303, 000304), quindi un CDA rifatto ha la data di oggi."""
        ricetta = esito.ricetta
        prescrittore, sostituito = self._codici_cda(ricetta)
        # L'anagrafica è quella CONTROLLATA prima di chkPrescrizione, conservata nell'esito: una
        # seconda lettura del callback poteva dare un altro paziente e il CDA mescolava il CF della
        # ricetta con nome e nascita di un altro (revisione esterna giro 2, 3-sar-puglia N1). Se
        # l'esito non la porta, la nuova lettura passa lo stesso controllo di richiesta_chk.
        paziente = esito.paziente
        if paziente is None and self.anagrafica:
            paziente = self.anagrafica(ricetta.assistito)
        if paziente is not None and paziente.assistito != ricetta.assistito:
            raise RicettaNonValida(["il Paziente passato non contiene l'Assistito della ricetta"])
        cda = cda_sist.genera_xml(
            ricetta,
            esito.nre,
            esito.codice_autenticazione,
            codice_regionale_prescrittore=prescrittore,
            codice_regionale_sostituito=sostituito,
            paziente=paziente,
            medico=self.medico,
            maggior_tutela=esito.maggior_tutela,
            creato=self._orologio(),
        )
        return dataclasses.replace(esito, cda=cda, cda_firmato=self.firmatario.firma_cades(cda))

    def ripeti_registrazione(
        self, esito: EsitoInvioSAR, *, oscurato: bool | None = None, id_pcp: str | None = None
    ) -> EsitoInvioSAR:
        """setRegistraPrescrizione con il CDA firmato. Da ripetere finché il SIST non risponde
        (Appendice A: "La chiamata va schedulata in caso di timeout o indisponibilità").

        Oscuramento e Piano Care stanno nell'esito: non vanno ripassati. Passarli diversi da quelli
        dell'invio è un errore (la volontà dell'assistito non cambia con un timeout). Se manca la
        firma (la CNS era stata tolta), CDA e firma si rifanno qui, dalla ricetta nell'esito.
        """
        for nome, dato in (("oscurato", oscurato), ("id_pcp", id_pcp)):
            if dato is not None and dato != getattr(esito, nome):
                raise ValueError(f"{nome}={dato!r} diverso da quello dell'invio ({getattr(esito, nome)!r})")
        if not esito.ok or not esito.nre:
            raise ValueError("niente da registrare: il controllo non è riuscito")
        if not esito.cda_firmato:
            if esito.ricetta is None:
                raise ValueError("niente CDA firmato da registrare e nessuna ricetta da cui rifarlo")
            try:
                esito = self._firma(esito)
            except Exception as e:  # noqa: BLE001 - la ricetta esiste già al SAC: l'esito deve tornare
                return dataclasses.replace(esito, registrato=False,
                                           errore_registrazione=f"CDA o firma non riusciti: {type(e).__name__}: {e}")
        try:
            # anche la preparazione della richiesta (orologio e dati del canale, busta) sta qui dentro:
            # un suo errore lascia la ricetta da registrare, non perde l'esito (giro 3, residuo bug 2)
            prescrizione = base64.b64encode(esito.cda_firmato).decode("ascii")
            corpo = xml_sist.richiesta_registra(self.canale.dati_chiamata(), prescrizione, esito.oscurato, esito.id_pcp)
        except Exception as e:  # noqa: BLE001
            return dataclasses.replace(esito, registrato=False, errore_registrazione=(
                f"preparazione della richiesta di registrazione non riuscita: {type(e).__name__}: {e}"))
        try:
            ok = xml_sist.leggi_registra(self._chiama("setRegistraPrescrizione", corpo))
            errore = None if ok else "il SIST ha risposto esito FALSE"
        except ErroreSOAP as e:
            ok, errore = False, f"SOAP Fault {xml_sist.codice_fault_sist(e) or ''} {e.faultstring}".strip()
        except ErroreTrasporto as e:
            ok, errore = False, f"errore di trasporto: {e}"
        except (ValueError, ET.ParseError) as e:  # risposta illeggibile: la ricetta resta da registrare
            ok, errore = False, f"risposta non leggibile: {e}"
        except Exception as e:  # noqa: BLE001 - es. CNS tolta durante la firma WS-Security della richiesta
            # (revisione esterna giro 2, 3-sar-puglia, residuo del bug 2): la ricetta esiste già al SAC,
            # l'esito con il CDA firmato deve tornare, altrimenti si rifà l'invio e nasce un secondo NRE.
            ok, errore = False, f"richiesta di registrazione non riuscita: {type(e).__name__}: {e}"
        return dataclasses.replace(esito, registrato=ok, errore_registrazione=errore)

    def visualizza(
        self, nre: str, cf_medico: str | None = None, *, cf_assistito: str | None = None
    ) -> EsitoVisualizzazioneSAR:
        self._cf_operatore(cf_medico)
        if not cf_assistito:
            raise ValueError(
                "il SIST identifica la prescrizione con NRE e codice fiscale dell'assistito "
                "(getPrescrizioneIdentificata): passare cf_assistito"
            )
        corpo = xml_sist.richiesta_identificata(self.canale.dati_chiamata(), nre, cf_assistito)
        try:
            return xml_sist.leggi_identificata(self._chiama("getPrescrizioneIdentificata", corpo), nre)
        except ErroreSOAP as e:
            m = _fault_applicativo(e)
            if m is None:
                raise
            return EsitoVisualizzazioneSAR(codice="9999", messaggi=(m,), nre=nre)

    def annulla(self, nre: str, cf_medico: str | None = None) -> EsitoAnnullamento:
        self._cf_operatore(cf_medico)
        corpo = xml_sist.richiesta_annulla(self.canale.dati_chiamata(), nre)
        try:
            return xml_sist.leggi_annulla(self._chiama("setAnnullaPrescrizione", corpo), nre)
        except ErroreSOAP as e:
            m = _fault_applicativo(e)
            if m is None:
                raise
            return EsitoAnnullamento(codice="9999", messaggi=(m,), nre=nre)

    def interroga_nre_utilizzati(
        self, criteri: CriteriNreUtilizzati, cf_medico: str | None = None
    ) -> EsitoInterrogazioneNre:
        cf = self._cf_operatore(cf_medico)
        corpo = xml_sist.richiesta_ricerca(self.canale.dati_chiamata(), criteri, self.codice_regionale(cf))
        try:
            return xml_sist.leggi_ricerca(self._chiama("getPrescrizioniIdentificate", corpo))
        except ErroreSOAP as e:
            m = _fault_applicativo(e)
            if m is None:
                raise
            return EsitoInterrogazioneNre(codice="9999", messaggi=(m,))
