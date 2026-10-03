# SPDX-License-Identifier: EUPL-1.2
from .modello import (
    STATI_PROCESSO,
    Assistito,
    ClassePriorita,
    Comunicazione,
    CriteriNreUtilizzati,
    Esito,
    EsitoAnnullamento,
    EsitoInterrogazioneNre,
    EsitoInvio,
    EsitoVisualizzazione,
    Messaggio,
    NotaPrestazione,
    NreUtilizzato,
    Prescrittore,
    Ricetta,
    Riga,
    TipoPrescrizione,
    TipoVisita,
)
from .servizio import RicettaSAC, ServizioRicetta
from .fvg import RicettaFVG, VerificaSostituto, richiede_downgrade_mir
from .piemonte import RicettaPiemonte
from .umbria import RicettaUmbria
from .json_umbria import EsitoLottoNRE, LottoNRE

__all__ = [
    "STATI_PROCESSO",
    "Assistito",
    "ClassePriorita",
    "Comunicazione",
    "CriteriNreUtilizzati",
    "Esito",
    "EsitoAnnullamento",
    "EsitoInterrogazioneNre",
    "EsitoInvio",
    "EsitoVisualizzazione",
    "Messaggio",
    "NotaPrestazione",
    "NreUtilizzato",
    "Prescrittore",
    "Ricetta",
    "Riga",
    "TipoPrescrizione",
    "TipoVisita",
    "RicettaSAC",
    "ServizioRicetta",
    "RicettaFVG",
    "VerificaSostituto",
    "richiede_downgrade_mir",
    "RicettaPiemonte",
    "RicettaUmbria",
    "EsitoLottoNRE",
    "LottoNRE",
]
