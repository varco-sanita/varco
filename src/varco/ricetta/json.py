# SPDX-License-Identifier: EUPL-1.2
"""Ricetta <-> dizionario JSON: formato portabile, indipendente dal canale.

Serve a due cose: esportare i dati del medico in un formato leggibile da chiunque,
e scrivere i casi della suite di conformità.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
from typing import Any

from .modello import (
    Assistito,
    ClassePriorita,
    CriteriNreUtilizzati,
    Prescrittore,
    Ricetta,
    Riga,
    TipoPrescrizione,
    TipoVisita,
)


def _filtra(cls, dati: dict[str, Any]) -> dict[str, Any]:
    nomi = {f.name for f in dataclasses.fields(cls)}
    sconosciuti = set(dati) - nomi
    if sconosciuti:
        raise ValueError(f"{cls.__name__}: campi sconosciuti {sorted(sconosciuti)}")
    return dati


def ricetta_da_dict(d: dict[str, Any]) -> Ricetta:
    d = dict(d)
    pr = Prescrittore(**_filtra(Prescrittore, d.pop("prescrittore")))
    ass = Assistito(**_filtra(Assistito, d.pop("assistito")))
    righe = tuple(Riga(**_filtra(Riga, r)) for r in d.pop("righe"))
    tipo = TipoPrescrizione(d.pop("tipo"))
    if "tipo_visita" in d:
        d["tipo_visita"] = TipoVisita(d["tipo_visita"])
    if d.get("classe_priorita"):
        d["classe_priorita"] = ClassePriorita(d["classe_priorita"])
    if d.get("data_compilazione"):
        d["data_compilazione"] = _dt.datetime.fromisoformat(d["data_compilazione"])
    else:
        d.pop("data_compilazione", None)
    _filtra(Ricetta, d)
    return Ricetta(prescrittore=pr, assistito=ass, tipo=tipo, righe=righe, **d)


def _pulisci(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _pulisci(x) for k, x in v.items() if x not in (None, False, (), [])}
    if isinstance(v, (list, tuple)):
        return [_pulisci(x) for x in v]
    if isinstance(v, (TipoPrescrizione, TipoVisita, ClassePriorita)):
        return v.value
    if isinstance(v, _dt.datetime):
        return v.isoformat(sep=" ")
    return v


def ricetta_a_dict(r: Ricetta) -> dict[str, Any]:
    d = dataclasses.asdict(r)
    d["non_esente"] = r.non_esente  # booleano significativo anche se False
    out = _pulisci(d)
    out.setdefault("non_esente", r.non_esente)
    return out


def criteri_da_dict(d: dict[str, Any]) -> CriteriNreUtilizzati:
    """Criteri di InterrogaNreUtilizzati. Date "AAAA-MM-GG hh:mm:ss" (o ISO 8601)."""
    d = dict(_filtra(CriteriNreUtilizzati, d))
    if d.get("tipo"):
        d["tipo"] = TipoPrescrizione(d["tipo"])
    for k in ("dal", "al"):
        if d.get(k):
            d[k] = _dt.datetime.fromisoformat(d[k])
    return CriteriNreUtilizzati(**d)
