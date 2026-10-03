# SPDX-License-Identifier: EUPL-1.2
"""varco: kit aperto per far parlare i software dei medici con i servizi pubblici.

Modulo 1: ricetta dematerializzata via SAC (Sistema TS / MEF), lato prescrittore.
Lo stesso contratto verso i SAR della Regione Friuli-Venezia Giulia (Insiel, `RicettaFVG`), della
Regione Piemonte (SIRPED, CSI Piemonte, `RicettaPiemonte`) e della Regione Umbria (PuntoZero,
`RicettaUmbria`): scritti e verificati sulle specifiche, NON collaudati sui sistemi regionali.
Il modulo della Regione Puglia (SIST) è sospeso dal 03/10/2026: vedi CHANGELOG, versione 0.1.1.

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
from .ricetta import RicettaFVG, RicettaPiemonte, RicettaSAC, ServizioRicetta
from .trasporto import CanaleFVG, CanalePiemonte, CanaleSAC, RegistratoreFile, TrasportoHTTP

__version__ = "0.1.1"

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
    "RicettaFVG",
    "RicettaPiemonte",
    "ServizioRicetta",
    "CanaleSAC",
    "CanaleFVG",
    "CanalePiemonte",
    "RegistratoreFile",
    "TrasportoHTTP",
]
