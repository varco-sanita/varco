# SPDX-License-Identifier: EUPL-1.2
"""Variabili d'ambiente di Varco e alias deprecati del nome precedente (kit-mmg).

Fino al 03/10/2026 il progetto si chiamava kit-mmg e le variabili avevano il prefisso
`KITMMG_`. Ora il prefisso è `VARCO_`. Per UNA versione la vecchia variabile vale ancora,
se la nuova non è impostata, e ogni lettura emette `VariabileDeprecata` (una FutureWarning,
quindi visibile anche senza opzioni). Dalla versione successiva gli alias spariscono.

Se sono impostate tutte e due vince la nuova, senza avviso.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Mapping

PREFISSO = "VARCO_"
PREFISSO_DEPRECATO = "KITMMG_"

# Le sole variabili che esistevano col vecchio nome: nessun alias per quelle nate con Varco.
NOMI_CON_ALIAS = frozenset({
    "UTENTE", "PASSWORD", "PINCODE", "CF_MEDICO",  # credenziali
    "KIT_MEF", "CONFORMITA", "XSD_SIST", "XSD_FVG", "XSD_A2F", "VALIDATORE_UFFICIALE",
    "INTEGRAZIONE",  # solo test
})


class VariabileDeprecata(FutureWarning):
    """Una variabile `KITMMG_*` è stata letta al posto della sua `VARCO_*`."""


def leggi(nome: str, predefinito: str | None = None, ambiente: Mapping[str, str] | None = None) -> str | None:
    """Valore di `nome` (con prefisso VARCO_), oppure del vecchio alias KITMMG_ con avviso."""
    env = os.environ if ambiente is None else ambiente
    if not nome.startswith(PREFISSO):
        raise ValueError(f"{nome}: le variabili di Varco iniziano con {PREFISSO}")
    if nome in env:
        return env[nome]
    radice = nome[len(PREFISSO):]
    vecchio = PREFISSO_DEPRECATO + radice
    if radice in NOMI_CON_ALIAS and vecchio in env:
        warnings.warn(
            f"{vecchio} è deprecata e verrà tolta nella prossima versione: usa {nome}",
            VariabileDeprecata,
            stacklevel=2,
        )
        return env[vecchio]
    return predefinito
