# SPDX-License-Identifier: EUPL-1.2
"""`ServizioRicetta` verso SIRPED, il SAR della Regione Piemonte (CSI Piemonte): stesso contratto, altro canale.

Il SAR piemontese espone i servizi del SAC «in analogia al SAC» (RE-SRS-SAR Cartelle cliniche
MMG/PLS V05, par. 4.4): InvioPrescritto, AnnullaPrescritto, VisualizzaPrescritto,
InterrogaNreUtilizzati, con il tracciato e i namespace del MEF (REL-STC-01 V04, esempio del par.
4.3.6). Per questo il codec è quello del SAC (`xml_sac`), senza un tag cambiato. Cambiano:

  - la **cifratura** di pincode e CF dell'assistito: RSA PKCS#1 v1.5 e base64 come per il SAC, ma con
    il certificato «emesso dalla CA Infocert» che la Regione dà ai fornitori (RE-SRS-SAR, par. 6.4;
    nell'esempio `REL_PROD_PUBKEY.cer`). Non è pubblicato: il cifratore si passa esplicito;
  - il **pincode** è quello RUPAR, e nella modalità OAuth2 il tag `pinCode` va **vuoto** (REL-STC-01,
    par. 4.3.6). Il CF dell'assistito resta cifrato in entrambe le modalità;
  - il canale (credenziali RUPAR, Id-Sessione o JWT, codice del gestionale): trasporto/piemonte.py.

Cosa resta al programma del medico (RE-SRS-SAR, par. 4.2-4.4, non nel tracciato):
  - i **lotti di NRE**: il SAR dà lotti da 1000 NRE e la cartella numera da sé (par. 3.2 e 4.2). Il
    kit manda l'NRE che trova nella ricetta; il servizio «Richiesta Lotto» non c'è;
  - la **ricetta rossa** (DPCM 26/03/2008) su fault o timeout, con annullamento della
    dematerializzata e nuovo NRE (par. 4.4.1, casi 4 e 5): il canale DPCM non è nel kit;
  - il **promemoria** con la riga regionale «codice regionale (7) – nominativo» (par. 4.4.3).

Scritto e verificato sulle specifiche: NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ..errori import RicettaNonValida
from ..trasporto.piemonte import CanalePiemonte, ModalitaPiemonte, ServizioPiemonte
from ..trasporto.sac import RispostaGrezza
from . import xml_sac
from .modello import (
    CriteriNreUtilizzati,
    EsitoAnnullamento,
    EsitoInterrogazioneNre,
    EsitoInvio,
    EsitoVisualizzazione,
    Ricetta,
)

# Valore qualunque da far cifrare al codec del SAC quando il pincode deve restare vuoto (OAuth2):
# il codec lo cifra, poi `_svuota_pincode` toglie il risultato. Non arriva mai sulla rete.
_PINCODE_SEGNAPOSTO = "0"


def _svuota_pincode(radice: ET.Element) -> ET.Element:
    """REL-STC-01, par. 4.3.6: con il JWT «Il campo pinCode ... deve essere impostato a vuoto»."""
    figli = [c for c in radice if c.tag.rsplit("}", 1)[-1] == "pinCode"]
    if len(figli) != 1:
        raise ValueError("richiesta senza un unico <pinCode>")
    figli[0].text = None
    return radice


@dataclass
class UltimoScambio:
    servizio: str
    grezza: RispostaGrezza


class RicettaPiemonte:
    """Implementazione di `ServizioRicetta` su SIRPED."""

    def __init__(self, canale: CanalePiemonte, cifratore, *, valida_localmente: bool = True):
        """
        `cifratore`: cifra CF dell'assistito e pincode (`CifratoreSanitel(certificato_regione)`, stesso
        algoritmo). OBBLIGATORIO e senza default: il certificato della Regione si riceve con
        l'autocertificazione (piano dei test RE-TES-01, par. 2.1, punto 5), non è pubblico, e
        SanitelCF qui sarebbe sbagliato.
        """
        if cifratore is None:
            raise ValueError("serve il cifratore: certificato della Regione Piemonte (RE-SRS-SAR, par. 6.4)")
        self.canale = canale
        self.cifratore = cifratore
        self.valida_localmente = valida_localmente
        self.ultimo: UltimoScambio | None = None

    # ------------------------------------------------------------------ supporto

    @property
    def _oauth2(self) -> bool:
        return self.canale.modalita is ModalitaPiemonte.OAUTH2

    @property
    def _pincode(self) -> str:
        return _PINCODE_SEGNAPOSTO if self._oauth2 else self.canale.credenziali.pincode

    def _corpo(self, radice: ET.Element) -> ET.Element:
        return _svuota_pincode(radice) if self._oauth2 else radice

    def _chiama(self, servizio: ServizioPiemonte, corpo: ET.Element) -> ET.Element:
        el, grezza = self.canale.chiama(servizio, self._corpo(corpo))
        self.ultimo = UltimoScambio(servizio.value, grezza)
        return el

    def _cf(self, cf_medico: str | None) -> str:
        return cf_medico or self.canale.cf_medico

    @staticmethod
    def _nre(nre: str | None) -> str:
        if not nre or len(nre) != 15:
            raise ValueError(f"NRE non valido (servono 15 caratteri): {nre!r}")
        return nre

    def _verifica_medico(self, ricetta: Ricetta) -> None:
        """Regola del SAC sui sostituti (prescrive il sostituto, con le sue credenziali) e controllo di
        SIRPED in produzione: il CF del prescrittore deve essere quello dell'utente autenticato
        (piano dei test SIRPED-TES-01 V02, par. 3.2). In test SIRPED non lo controlla: il kit sì."""
        atteso = ricetta.prescrittore.codice_fiscale_sostituto or ricetta.prescrittore.codice_fiscale
        if not isinstance(atteso, str) or not atteso.strip():
            raise RicettaNonValida(["codice fiscale del prescrittore mancante"])
        if atteso.upper() != self.canale.cf_medico:
            raise RicettaNonValida(
                [f"il medico autenticato ({self.canale.cf_medico}) non è quello che prescrive ({atteso}): "
                 "con il sostituto invia il sostituto, con le sue credenziali o il suo token"]
            )

    # ------------------------------------------------------------------ contratto

    def invia(self, ricetta: Ricetta) -> EsitoInvio:
        # il controllo d'identità vale sempre, anche con la validazione del tracciato spenta
        self._verifica_medico(ricetta)
        if self.valida_localmente:
            ricetta.valida()
        corpo = xml_sac.richiesta_invio(ricetta, self._pincode, self.cifratore.cifra)
        return xml_sac.leggi_ricevuta_invio(self._chiama(ServizioPiemonte.INVIO, corpo))

    def visualizza(
        self, nre: str, cf_medico: str | None = None, *, cf_assistito: str | None = None
    ) -> EsitoVisualizzazione:
        # cf_assistito: ignorato, come nel SAC il SAR identifica la ricetta con NRE e CF del medico
        corpo = xml_sac.richiesta_visualizza(self._nre(nre), self._cf(cf_medico), self._pincode, self.cifratore.cifra)
        return xml_sac.leggi_ricevuta_visualizza(self._chiama(ServizioPiemonte.VISUALIZZA, corpo))

    def annulla(self, nre: str, cf_medico: str | None = None) -> EsitoAnnullamento:
        corpo = xml_sac.richiesta_annulla(self._nre(nre), self._cf(cf_medico), self._pincode, self.cifratore.cifra)
        return xml_sac.leggi_ricevuta_annulla(self._chiama(ServizioPiemonte.ANNULLA, corpo))

    def interroga_nre_utilizzati(
        self, criteri: CriteriNreUtilizzati, cf_medico: str | None = None
    ) -> EsitoInterrogazioneNre:
        if self.valida_localmente:
            problemi = criteri.problemi()
            if problemi:
                raise RicettaNonValida(problemi)
        corpo = xml_sac.richiesta_interroga_nre(criteri, self._cf(cf_medico), self._pincode, self.cifratore.cifra)
        return xml_sac.leggi_ricevuta_interroga_nre(self._chiama(ServizioPiemonte.INTERROGA_NRE, corpo))


def richiesta(servizio: str, modalita: ModalitaPiemonte, cifra, *, ricetta: Ricetta | None = None,
              nre: str | None = None, cf_medico: str | None = None, criteri: CriteriNreUtilizzati | None = None,
              pincode: str = "0000000000") -> ET.Element:
    """La richiesta che `RicettaPiemonte` manderebbe, SENZA mandarla: per la suite di conformità e i test."""
    pin = _PINCODE_SEGNAPOSTO if modalita is ModalitaPiemonte.OAUTH2 else pincode
    if servizio == "invio":
        el = xml_sac.richiesta_invio(ricetta, pin, cifra)
    elif servizio == "visualizza":
        el = xml_sac.richiesta_visualizza(nre, cf_medico, pin, cifra)
    elif servizio == "annulla":
        el = xml_sac.richiesta_annulla(nre, cf_medico, pin, cifra)
    elif servizio == "interroga_nre":
        el = xml_sac.richiesta_interroga_nre(criteri, cf_medico, pin, cifra)
    else:
        raise ValueError(f"servizio sconosciuto: {servizio}")
    return _svuota_pincode(el) if modalita is ModalitaPiemonte.OAUTH2 else el
