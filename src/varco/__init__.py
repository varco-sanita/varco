# SPDX-License-Identifier: EUPL-1.2
"""varco: kit aperto per far parlare i software dei medici con i servizi pubblici.

Modulo 1: ricetta dematerializzata via SAC (Sistema TS / MEF), lato prescrittore.
Lo stesso contratto verso i SAR della Regione Puglia (SIST, `RicettaSIST`) e della Regione
Friuli-Venezia Giulia (Insiel, `RicettaFVG`) e della Regione Piemonte (SIRPED, CSI Piemonte,
`RicettaPiemonte`): scritti e verificati sulle specifiche, NON collaudati sui sistemi regionali.

Uso minimo (ambiente di TEST):

    from varco import Credenziali, CanaleSAC, RicettaSAC
    servizio = RicettaSAC(CanaleSAC(Credenziali.da_env()))
    esito = servizio.invia(ricetta)
"""

from .ambienti import Ambiente
from .cifratura import CifratoreSanitel
from .credenziali import Credenziali
from .errori import (
    AmbienteBloccato,
    ConfigurazioneNonValida,
    ErroreKit,
    ErroreSOAP,
    ErroreTrasporto,
    RicettaNonValida,
)
from .ricetta import RicettaFVG, RicettaPiemonte, RicettaSAC, RicettaSIST, ServizioRicetta
from .trasporto import CanaleFVG, CanalePiemonte, CanaleSAC, CanaleSIST, RegistratoreFile, TrasportoHTTP

__version__ = "0.1.0"

__all__ = [
    "Ambiente",
    "CifratoreSanitel",
    "Credenziali",
    "AmbienteBloccato",
    "ConfigurazioneNonValida",
    "ErroreKit",
    "ErroreSOAP",
    "ErroreTrasporto",
    "RicettaNonValida",
    "RicettaSAC",
    "RicettaSIST",
    "RicettaFVG",
    "RicettaPiemonte",
    "ServizioRicetta",
    "CanaleSAC",
    "CanaleSIST",
    "CanaleFVG",
    "CanalePiemonte",
    "RegistratoreFile",
    "TrasportoHTTP",
]
