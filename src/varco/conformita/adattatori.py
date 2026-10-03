# SPDX-License-Identifier: EUPL-1.2
"""Adattatori: come la suite ottiene l'implementazione da collaudare.

Un adattatore è una funzione  crea(credenziali, valida_localmente) -> ServizioRicetta.
Per collaudare un'altra implementazione (anche non Python, via un piccolo ponte)
basta scriverne uno e passarlo con  --adattatore modulo:funzione.
"""

from __future__ import annotations

import importlib
from typing import Callable

from ..credenziali import Credenziali
from ..ricetta.servizio import RicettaSAC, ServizioRicetta
from ..trasporto import CanaleSAC, TrasportoHTTP

Adattatore = Callable[[Credenziali, bool], ServizioRicetta]


def adattatore_sac(trasporto: TrasportoHTTP, base_url: str | None = None) -> Adattatore:
    """Implementazione di riferimento: questo kit verso il SAC di test."""

    def crea(credenziali: Credenziali, valida_localmente: bool) -> ServizioRicetta:
        return RicettaSAC(
            CanaleSAC(credenziali, base_url=base_url, trasporto=trasporto),
            valida_localmente=valida_localmente,
        )

    return crea


def carica(spec: str) -> Adattatore:
    modulo, _, nome = spec.partition(":")
    if not nome:
        raise ValueError("Formato adattatore: modulo:funzione")
    return getattr(importlib.import_module(modulo), nome)
