# SPDX-License-Identifier: EUPL-1.2
"""`ServizioRicetta` verso il SAR della Regione Umbria (PuntoZero): stesso contratto, altro canale.

Corrispondenza con il contratto (wiki «Prescrittori» di punto-zero/umbria-sar-support):

  invia      -> POST /v1/servizi-prescrittore/dem-invio-prescritto. L'NRE è OBBLIGATORIO e lo mette
                il medico (`Ricetta.nre`, un campo che il modello ha già per i lotti del SAC), da un
                lotto chiesto con `richiedi_lotto_nre`. Il CF dell'assistito va in chiaro.
  visualizza -> dem-visualizza-prescritto (NRE e CF del medico; il CF dell'assistito serve al JWT)
  annulla    -> dem-annulla-prescritto (idem: `cf_assistito` per il claim person_id)
  interroga_nre_utilizzati -> dem-nre-utilizzati

In più, solo Umbria: `richiedi_lotto_nre` e `dichiara_sostituzione` (obbligatoria «entro il secondo
trimestre 2026», wiki). Dopo un invio incerto (502, 504, nessuna risposta) il kit solleva
`InvioIncertoUmbria`: la specifica chiede di annullare con lo stesso NRE (`annulla_invio_incerto`) e di
rifare l'invio con un NRE diverso, scelto da chi integra.

Non implementato: le ricette rosse (`dpcm-*`): l'OpenAPI non descrive la loro risposta.

Scritto e verificato sulle specifiche: NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from ..errori import ConfigurazioneNonValida, RicettaNonValida
from ..trasporto.sac import RispostaGrezza
from ..trasporto.umbria import CanaleUmbria, InvioIncertoUmbria, ServizioUmbria
from . import json_umbria
from .json_umbria import EsitoLottoNRE, RispostaNonConforme
from .modello import (
    CriteriNreUtilizzati,
    Esito,
    EsitoAnnullamento,
    EsitoInterrogazioneNre,
    EsitoInvio,
    EsitoVisualizzazione,
    Ricetta,
)


@dataclass
class UltimoScambio:
    servizio: str
    grezza: RispostaGrezza


class RicettaUmbria:
    """Implementazione di `ServizioRicetta` sul SAR Umbria."""

    def __init__(self, canale: CanaleUmbria, *, valida_localmente: bool = True):
        self.canale = canale
        self.valida_localmente = valida_localmente
        self.ultimo: UltimoScambio | None = None

    def _chiama(self, servizio: ServizioUmbria, corpo: dict, cf_assistito: str | None = None) -> dict:
        r = self.canale.chiama(servizio, corpo, cf_assistito=cf_assistito)
        self.ultimo = UltimoScambio(servizio.value, r.grezza)
        return r.dati

    def _cf(self, cf_medico: str | None) -> str:
        return (cf_medico or self.canale.cf_medico).upper()

    def _verifica_medico(self, ricetta: Ricetta) -> None:
        """Il JWT è del medico che invia: il sostituto se c'è (cfMedico2), altrimenti il titolare."""
        atteso = ricetta.prescrittore.codice_fiscale_sostituto or ricetta.prescrittore.codice_fiscale
        if atteso.upper() != self.canale.cf_medico:
            raise RicettaNonValida(
                [f"il medico del token ({self.canale.cf_medico}) non è quello che prescrive ({atteso}): "
                 "con il sostituto invia il sostituto, con un canale suo"])

    @staticmethod
    def _cf_assistito(cf: str | None, operazione: str) -> str:
        c = (cf or "").strip().upper()
        if len(c) != 16:
            raise RicettaNonValida([f"{operazione}: in Umbria serve il CF dell'assistito (claim person_id del JWT)"])
        return c

    @staticmethod
    def _leggi(funzione, dati: dict):
        try:
            return funzione(dati)
        except RispostaNonConforme as e:
            raise ConfigurazioneNonValida(f"risposta del SAR Umbria fuori dall'OpenAPI: {e}") from e

    # ------------------------------------------------------------------ contratto

    def invia(self, ricetta: Ricetta) -> EsitoInvio:
        # i controlli Umbria valgono SEMPRE: senza NRE o senza CF il servizio non si può nemmeno chiamare
        problemi = json_umbria.problemi_umbria(ricetta)
        if self.valida_localmente:
            problemi = ricetta.problemi() + problemi
        if problemi:
            raise RicettaNonValida(problemi)
        self._verifica_medico(ricetta)
        corpo = json_umbria.richiesta_invio(ricetta)
        dati = self._chiama(ServizioUmbria.INVIO, corpo, ricetta.assistito.codice_fiscale)
        return self._leggi(json_umbria.leggi_ricevuta_invio, dati)

    def visualizza(
        self, nre: str, cf_medico: str | None = None, *, cf_assistito: str | None = None
    ) -> EsitoVisualizzazione:
        cf_ass = self._cf_assistito(cf_assistito, "visualizza")
        dati = self._chiama(ServizioUmbria.VISUALIZZA, json_umbria.richiesta_visualizza(nre, self._cf(cf_medico)), cf_ass)
        return self._leggi(json_umbria.leggi_ricevuta_visualizza, dati)

    def annulla(self, nre: str, cf_medico: str | None = None, *, cf_assistito: str | None = None) -> EsitoAnnullamento:
        cf_ass = self._cf_assistito(cf_assistito, "annulla")
        dati = self._chiama(ServizioUmbria.ANNULLA, json_umbria.richiesta_annulla(nre, self._cf(cf_medico)), cf_ass)
        return self._leggi(json_umbria.leggi_ricevuta_annulla, dati)

    def interroga_nre_utilizzati(
        self, criteri: CriteriNreUtilizzati, cf_medico: str | None = None
    ) -> EsitoInterrogazioneNre:
        if self.valida_localmente:
            problemi = criteri.problemi(tipo_obbligatorio=False)
            if problemi:
                raise RicettaNonValida(problemi)
        dati = self._chiama(ServizioUmbria.NRE_UTILIZZATI, json_umbria.richiesta_interroga_nre(criteri, self._cf(cf_medico)))
        return self._leggi(json_umbria.leggi_ricevuta_interroga_nre, dati)

    # ------------------------------------------------------------------ solo Umbria

    def richiedi_lotto_nre(self, identificativo_lotto: str = "1", cf_medico: str | None = None) -> EsitoLottoNRE:
        """Un lotto di NRE per il medico: «1» = 1000 NRE (consigliato a MMG e PLS), «0» = 100."""
        corpo = json_umbria.richiesta_lotto(self._cf(cf_medico), identificativo_lotto)
        return self._leggi(json_umbria.leggi_ricevuta_lotto, self._chiama(ServizioUmbria.LOTTO_NRE, corpo))

    def dichiara_sostituzione(self, cf_sostituto: str, dal: _dt.date, al: _dt.date, *, codice_asl: str,
                              codice_specializzazione: str, codice_struttura: str | None = None,
                              nota: str | None = None) -> Esito:
        """Il titolare (il medico del canale) dichiara in anticipo un periodo di sostituzione."""
        try:
            corpo = json_umbria.richiesta_sostituzione(self.canale.cf_medico, cf_sostituto, codice_asl,
                                                       codice_specializzazione, dal, al,
                                                       codice_struttura=codice_struttura, nota=nota)
        except ValueError as e:
            raise RicettaNonValida([str(e)]) from e
        return self._leggi(json_umbria.leggi_ricevuta_sostituzione, self._chiama(ServizioUmbria.SOSTITUZIONE, corpo))

    def annulla_invio_incerto(self, errore: InvioIncertoUmbria) -> EsitoAnnullamento:
        """Prima metà della procedura della specifica dopo un 502/504: annullare con lo STESSO NRE. Poi si
        rifà l'invio con un NRE diverso (lo sceglie chi integra: questo NRE non si riusa)."""
        if not errore.nre:
            raise RicettaNonValida(["invio incerto senza NRE: niente da annullare"])
        return self.annulla(errore.nre, errore.cf_medico, cf_assistito=errore.cf_assistito)
