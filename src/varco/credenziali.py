# SPDX-License-Identifier: EUPL-1.2
"""Credenziali del prescrittore: mai scritte nel codice, lette da env o da file.

Variabili d'ambiente:
    VARCO_UTENTE     id utenza Sistema TS (per il medico: il suo codice fiscale)
    VARCO_PASSWORD   password dell'utenza
    VARCO_PINCODE    pincode in chiaro (viene cifrato a ogni chiamata)
    VARCO_CF_MEDICO  opzionale, se diverso da VARCO_UTENTE
Le vecchie KITMMG_* valgono ancora per una versione, con avviso (varco.ambiente).

File (TOML, sezione [credenziali]) con le stesse chiavi in minuscolo senza prefisso:
    [credenziali]
    utente = "..."
    password = "..."
    pincode = "..."
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .ambiente import PREFISSO, leggi
from .errori import ConfigurazioneNonValida


@dataclass(frozen=True)
class Credenziali:
    utente: str
    password: str = field(repr=False)
    pincode: str = field(repr=False)
    cf_medico: str | None = None

    def __post_init__(self):
        mancanti = [n for n in ("utente", "password", "pincode") if not getattr(self, n)]
        if mancanti:
            raise ConfigurazioneNonValida(f"Credenziali incomplete: mancano {', '.join(mancanti)}")

    @property
    def cf(self) -> str:
        return self.cf_medico or self.utente

    @classmethod
    def da_env(cls, prefisso: str = PREFISSO, ambiente: dict[str, str] | None = None) -> "Credenziali":
        env = os.environ if ambiente is None else ambiente
        if prefisso == PREFISSO:  # col prefisso predefinito valgono anche gli alias KITMMG_* deprecati

            def val(nome: str) -> str | None:
                return leggi(prefisso + nome, None, env)
        else:

            def val(nome: str) -> str | None:
                return env.get(prefisso + nome)

        return cls(
            utente=val("UTENTE") or "",
            password=val("PASSWORD") or "",
            pincode=val("PINCODE") or "",
            cf_medico=val("CF_MEDICO") or None,
        )

    @classmethod
    def da_file(cls, percorso: str | Path) -> "Credenziali":
        with open(percorso, "rb") as f:
            dati = tomllib.load(f).get("credenziali", {})
        return cls(
            utente=str(dati.get("utente", "")),
            password=str(dati.get("password", "")),
            pincode=str(dati.get("pincode", "")),
            cf_medico=dati.get("cf_medico"),
        )
