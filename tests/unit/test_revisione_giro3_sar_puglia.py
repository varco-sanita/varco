# SPDX-License-Identifier: EUPL-1.2
"""Giro 3 di revisione esterna (03/10/2026), SAR Puglia: residuo ALTO del bug 2 del giro 1
(rapporto kit-mmg-review/2026-10-02-giro3/3-sar-puglia.md). Dopo un chkPrescrizione riuscito la
ricetta esiste già: qualunque errore deve tornare come `EsitoInvioSAR` recuperabile, mai come
eccezione, altrimenti un nuovo `invia()` crea una seconda ricetta.

Fixture e aiuti da test_sist.py (server finto locale, certificati di prova).
"""

from __future__ import annotations

import datetime as dt

import pytest

from test_sist import _azioni, _ricetta_farm, _servizio, p12, server  # noqa: F401 - fixture

from varco.ricetta import xml_sist

pytest.importorskip("lxml.etree")


def _orologio_che_si_rompe(alla: int, errore: BaseException):
    chiamate = []

    def clock():
        chiamate.append(1)
        if len(chiamate) == alla:
            raise errore
        return dt.datetime.now(dt.timezone.utc)

    return clock, chiamate


@pytest.mark.schemi_hl7
def test_g3_residuo_bug2_orologio_dopo_il_controllo_da_un_esito(server, p12):
    """Residuo bug 2 (giro 3): «ECCEZIONE RuntimeError orologio applicativo indisponibile / clock 3 richieste
    ['chkPrescrizione'] prescrizioni 1»."""
    s = _servizio(server, p12)
    clock, chiamate = _orologio_che_si_rompe(3, RuntimeError("orologio applicativo indisponibile"))
    s.canale._orologio = clock
    e = s.invia(_ricetta_farm())
    assert len(chiamate) == 3 and _azioni(server) == ["chkPrescrizione"] and len(server.stato.prescrizioni) == 1
    assert e.ok and e.nre and e.da_ripetere and e.registrato is False
    assert "orologio applicativo indisponibile" in e.errore_registrazione
    # orologio tornato: si ripete la sola registrazione, nessuna seconda ricetta
    s.canale._orologio = lambda: dt.datetime.now(dt.timezone.utc)
    e2 = s.ripeti_registrazione(e)
    assert e2.registrato is True and e2.nre == e.nre
    assert _azioni(server) == ["chkPrescrizione", "setRegistraPrescrizione"] and len(server.stato.prescrizioni) == 1


@pytest.mark.schemi_hl7
@pytest.mark.parametrize("errore", [RuntimeError("x"), OSError("disco"), ValueError("valore"), KeyError("chiave"),
                                    OverflowError("data"), TypeError("tipo"), AttributeError("attributo")],
                         ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("dove", ["orologio", "dati_chiamata", "richiesta_registra", "cda"])
def test_g3_residuo_bug2_famiglia_ogni_errore_dopo_il_controllo(server, p12, monkeypatch, errore, dove):
    """Famiglia: qualunque eccezione, in qualunque punto tra il controllo riuscito e la registrazione
    (orologio, dati del canale, costruzione della busta, CDA), dà un esito recuperabile; e anche
    `ripeti_registrazione` con lo stesso guasto restituisce l'esito invece di sollevare."""
    s = _servizio(server, p12)
    guasto = {"attivo": False}

    def rompi(originale):
        def f(*a, **k):
            if guasto["attivo"]:
                raise errore
            return originale(*a, **k)
        return f

    if dove == "orologio":
        clock, _ = _orologio_che_si_rompe(3, errore)
        s.canale._orologio = clock
    elif dove == "dati_chiamata":
        originale = s.canale.dati_chiamata
        conta = []

        def dati(*a, **k):
            conta.append(1)
            if len(conta) >= 2:
                raise errore
            return originale(*a, **k)

        s.canale.dati_chiamata = dati
    elif dove == "richiesta_registra":
        monkeypatch.setattr(xml_sist, "richiesta_registra", rompi(xml_sist.richiesta_registra))
        guasto["attivo"] = True
    else:
        from varco.ricetta import cda_sist

        monkeypatch.setattr(cda_sist, "genera_xml", rompi(cda_sist.genera_xml))
        guasto["attivo"] = True
    e = s.invia(_ricetta_farm())
    assert e.ok and e.nre and e.da_ripetere, e
    assert _azioni(server) == ["chkPrescrizione"] and len(server.stato.prescrizioni) == 1
    # stesso guasto ancora presente: ripeti_registrazione non solleva
    if dove == "orologio":
        s.canale._orologio = _orologio_che_si_rompe(1, errore)[0]
    e2 = s.ripeti_registrazione(e)
    assert e2.da_ripetere and e2.nre == e.nre and len(server.stato.prescrizioni) == 1
