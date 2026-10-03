# SPDX-License-Identifier: EUPL-1.2
"""Forma del codice fiscale delle persone fisiche (DM 23/12/1976, art. 7), senza anagrafe.

Serve a distinguere un CF ordinario da un codice STP/ENI: un CF può cominciare per «STP» o «ENI»
(dal cognome), e prima di applicare la regola STP/ENI va riconosciuto (issue #6). Si controllano la
struttura, con le sostituzioni dell'omocodia (L M N P Q R S T U V al posto delle cifre), e il
carattere di controllo. Non si controlla che il CF esista.
"""

from __future__ import annotations

import re

_FORMA = re.compile(r"[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]")
_DISPARI = {
    **dict(zip("0123456789", (1, 0, 5, 7, 9, 13, 15, 17, 19, 21))),
    **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
               (1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23))),
}
_PARI = {**{c: i for i, c in enumerate("0123456789")}, **{c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}}


def carattere_di_controllo(primi15: str) -> str:
    """Il sedicesimo carattere, calcolato sui primi quindici (posizioni dispari e pari, modulo 26)."""
    s = sum(_DISPARI[c] if i % 2 == 0 else _PARI[c] for i, c in enumerate(primi15.upper()))
    return chr(ord("A") + s % 26)


def e_codice_fiscale(codice: str | None) -> bool:
    """Vero per un CF di persona fisica ben formato: 16 caratteri, struttura e carattere di controllo."""
    c = (codice or "").upper()
    return bool(_FORMA.fullmatch(c)) and carattere_di_controllo(c[:15]) == c[15]
