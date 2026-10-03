# SPDX-License-Identifier: EUPL-1.2
"""ProfiloSanitarioSintetico <-> dizionario JSON: formato portabile, indipendente dal codice.

È il formato dei "dati" nei casi di conformità FSE (schema in
conformita/schema/pss.schema.json). Le date sono ISO 8601 ("AAAA-MM-GG";
"AAAA-MM-GGThh:mm:ss" per la data del documento). Paziente e medico riusano i
formati di Assistito e Prescrittore della ricetta (varco.ricetta.json).
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
from typing import Any

from ..ricetta.modello import Assistito, Prescrittore
from .modello import (
    Allergia,
    AnamnesiFamiliare,
    Custode,
    Esenzione,
    Indirizzo,
    Medico,
    Paziente,
    Problema,
    ProfiloSanitarioSintetico,
    Stato,
    Terapia,
)


def _campi(cls, d: dict[str, Any]) -> dict[str, Any]:
    nomi = {f.name for f in dataclasses.fields(cls)}
    ignoti = set(d) - nomi
    if ignoti:
        raise ValueError(f"{cls.__name__}: campi sconosciuti {sorted(ignoti)}")
    return dict(d)


def _data(v: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(v) if v else None


def _voce(cls, d: dict[str, Any]):
    d = _campi(cls, d)
    for k in ("inizio", "fine"):
        if k in d:
            d[k] = _data(d[k])
    if "stato" in d:
        d["stato"] = Stato(d["stato"])
    return cls(**d)


def _indirizzo(d: dict[str, Any] | None) -> Indirizzo | None:
    return Indirizzo(**_campi(Indirizzo, d)) if d else None


def pss_da_dict(d: dict[str, Any]) -> ProfiloSanitarioSintetico:
    d = _campi(ProfiloSanitarioSintetico, d)
    pz = _campi(Paziente, d.pop("paziente"))
    paziente = Paziente(
        assistito=Assistito(**_campi(Assistito, pz.pop("assistito"))),
        data_nascita=_data(pz.pop("data_nascita", None)),
        luogo_nascita=_indirizzo(pz.pop("luogo_nascita", None)),
        residenza=_indirizzo(pz.pop("residenza", None)),
        **pz,
    )
    au = _campi(Medico, d.pop("autore"))
    autore = Medico(prescrittore=Prescrittore(**_campi(Prescrittore, au.pop("prescrittore"))), **au)
    cu = _campi(Custode, d.pop("custode"))
    custode = Custode(indirizzo=_indirizzo(cu.pop("indirizzo", None)), **cu)
    return ProfiloSanitarioSintetico(
        paziente=paziente,
        autore=autore,
        custode=custode,
        data=_dt.datetime.fromisoformat(d.pop("data")),
        allergie=tuple(_voce(Allergia, x) for x in d.pop("allergie", [])),
        terapie=tuple(_voce(Terapia, x) for x in d.pop("terapie", [])),
        problemi=tuple(_voce(Problema, x) for x in d.pop("problemi", [])),
        anamnesi_familiare=tuple(AnamnesiFamiliare(**_campi(AnamnesiFamiliare, x)) for x in d.pop("anamnesi_familiare", [])),
        esenzioni=tuple(_voce(Esenzione, x) for x in d.pop("esenzioni", [])),
        **d,
    )


def _pulisci(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _pulisci(x) for k, x in v.items() if x not in (None, (), [])}
    if isinstance(v, (list, tuple)):
        return [_pulisci(x) for x in v]
    if isinstance(v, Stato):
        return v.value
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    return v


def pss_a_dict(p: ProfiloSanitarioSintetico) -> dict[str, Any]:
    d = _pulisci(dataclasses.asdict(p))
    # i booleani False dell'assistito della ricetta non servono al PSS
    d["paziente"]["assistito"] = {k: v for k, v in d["paziente"]["assistito"].items() if v is not False}
    return d
