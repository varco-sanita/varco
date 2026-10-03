# SPDX-License-Identifier: EUPL-1.2
"""Interfaccia pubblica del modulo ricetta e sua implementazione verso il SAC.

`ServizioRicetta` è il contratto: chi scrive un gestionale programma contro
questo, non contro il SAC. `RicettaFVG` (ricetta/fvg.py) lo soddisfa verso il SAR del
Friuli-Venezia Giulia, `RicettaPiemonte` (ricetta/piemonte.py) verso SIRPED del Piemonte,
`RicettaUmbria` (ricetta/umbria.py) verso il SAR dell'Umbria, ciascuno con il suo canale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..ambienti import Endpoint
from ..cifratura import CifratoreSanitel
from ..trasporto.sac import CanaleSAC, RispostaGrezza
from . import xml_sac
from ..errori import RicettaNonValida
from .modello import (
    CriteriNreUtilizzati,
    EsitoAnnullamento,
    EsitoInterrogazioneNre,
    EsitoInvio,
    EsitoVisualizzazione,
    Ricetta,
)


class ServizioRicetta(Protocol):
    def invia(self, ricetta: Ricetta) -> EsitoInvio: ...

    def visualizza(
        self, nre: str, cf_medico: str | None = None, *, cf_assistito: str | None = None
    ) -> EsitoVisualizzazione:
        """`cf_assistito`: il SAC non lo usa; un SAR può esigerlo (in Umbria serve al claim
        person_id del JWT)."""
        ...

    def annulla(self, nre: str, cf_medico: str | None = None) -> EsitoAnnullamento: ...

    def interroga_nre_utilizzati(
        self, criteri: CriteriNreUtilizzati, cf_medico: str | None = None
    ) -> EsitoInterrogazioneNre: ...


@dataclass
class UltimoScambio:
    servizio: str
    grezza: RispostaGrezza


class RicettaSAC:
    """Implementazione di `ServizioRicetta` sul Sistema di Accoglienza Centrale."""

    def __init__(self, canale: CanaleSAC, cifratore: CifratoreSanitel | None = None, *, valida_localmente: bool = True):
        self.canale = canale
        self.cifratore = cifratore or CifratoreSanitel.incluso()
        self.valida_localmente = valida_localmente
        self.ultimo: UltimoScambio | None = None

    @property
    def _pincode(self) -> str:
        return self.canale.credenziali.pincode

    def _cf(self, cf_medico: str | None) -> str:
        return cf_medico or self.canale.credenziali.cf

    @staticmethod
    def _nre(nre: str | None) -> str:
        # Mai mandare al SAC una chiamata destinata a sicuro rifiuto.
        if not nre or len(nre) != 15:
            raise ValueError(f"NRE non valido (servono 15 caratteri): {nre!r}")
        return nre

    def invia(self, ricetta: Ricetta) -> EsitoInvio:
        if self.valida_localmente:
            ricetta.valida()
            self._verifica_sostituto(ricetta)
        corpo = xml_sac.richiesta_invio(ricetta, self._pincode, self.cifratore.cifra)
        el, grezza = self.canale.chiama("sac.invioPrescritto", Endpoint.INVIO, xml_sac.SOAP_ACTION_INVIO, corpo)
        self.ultimo = UltimoScambio("invio", grezza)
        return xml_sac.leggi_ricevuta_invio(el)

    def _verifica_sostituto(self, ricetta: Ricetta) -> None:
        """Specifica par. 4.2.1 (sostituti), CASO 2: se c'è cfMedico2 prescrive il sostituto, quindi
        credenziali e pincode devono essere i suoi (salvo SAR, che questo kit non implementa)."""
        sostituto = ricetta.prescrittore.codice_fiscale_sostituto
        cf_credenziali = self.canale.credenziali.cf
        if sostituto and cf_credenziali != sostituto:
            raise RicettaNonValida(
                [f"con il sostituto ({sostituto}) in cfMedico2 servono le sue credenziali, non quelle di {cf_credenziali}"]
            )
        if not sostituto and cf_credenziali != ricetta.prescrittore.codice_fiscale:
            raise RicettaNonValida(
                [f"le credenziali ({cf_credenziali}) non sono del titolare {ricetta.prescrittore.codice_fiscale}: "
                 "se prescrive un sostituto va indicato in codice_fiscale_sostituto"]
            )

    def visualizza(
        self, nre: str, cf_medico: str | None = None, *, cf_assistito: str | None = None
    ) -> EsitoVisualizzazione:
        # cf_assistito: ignorato, il SAC identifica la ricetta col solo NRE (par. 4.2.2)
        corpo = xml_sac.richiesta_visualizza(self._nre(nre), self._cf(cf_medico), self._pincode, self.cifratore.cifra)
        el, grezza = self.canale.chiama(
            "sac.visualizzaPrescritto", Endpoint.VISUALIZZA, xml_sac.SOAP_ACTION_VISUALIZZA, corpo
        )
        self.ultimo = UltimoScambio("visualizza", grezza)
        return xml_sac.leggi_ricevuta_visualizza(el)

    def annulla(self, nre: str, cf_medico: str | None = None) -> EsitoAnnullamento:
        corpo = xml_sac.richiesta_annulla(self._nre(nre), self._cf(cf_medico), self._pincode, self.cifratore.cifra)
        el, grezza = self.canale.chiama(
            "sac.annullaPrescritto", Endpoint.ANNULLA, xml_sac.SOAP_ACTION_ANNULLA, corpo
        )
        self.ultimo = UltimoScambio("annulla", grezza)
        return xml_sac.leggi_ricevuta_annulla(el)

    def interroga_nre_utilizzati(
        self, criteri: CriteriNreUtilizzati, cf_medico: str | None = None
    ) -> EsitoInterrogazioneNre:
        """Lista degli NRE utilizzati (servizio demInterrogaNreUtilizzati, par. 4.2.4)."""
        if self.valida_localmente:
            problemi = criteri.problemi()
            if problemi:
                raise RicettaNonValida(problemi)
        corpo = xml_sac.richiesta_interroga_nre(criteri, self._cf(cf_medico), self._pincode, self.cifratore.cifra)
        el, grezza = self.canale.chiama(
            "sac.interrogaNreUtilizzati", Endpoint.INTERROGA_NRE, xml_sac.SOAP_ACTION_INTERROGA_NRE, corpo
        )
        self.ultimo = UltimoScambio("interroga_nre", grezza)
        return xml_sac.leggi_ricevuta_interroga_nre(el)
