# SPDX-License-Identifier: EUPL-1.2
"""Issue #6, #7, #8 (limiti noti della 0.1.0, SAR FVG): controesempi delle issue, rossi prima e verdi
dopo. Fixture e aiuti da test_fvg.py (server finto mTLS, certificati di prova, ricette)."""

from __future__ import annotations

import dataclasses as dc

import pytest

from test_fvg import (  # noqa: F401 - server e tls sono fixture
    APPLICATIVO, POSTAZIONE, SERVER, SOSTITUTO, TITOLARE, _ricetta_farm, _ricetta_spec, _servizio, server, tls,
)
from test_revisione_giro2_sar_fvg import Cattura

from varco import ConfigurazioneNonValida, ErroreSOAP
from varco.errori import RicettaNonValida
from varco.ricetta import Assistito, Prescrittore, RicettaFVG, xml_fvg
from varco.ricetta.codice_fiscale import carattere_di_controllo, e_codice_fiscale
from varco.trasporto.fvg import AdesioneFVG, CanaleFVG

CF_STP = "STPMRA70A01H501O"  # CF sintetico ben formato che comincia per STP (dal cognome)


# ------------------------------------------------------------------ #6 CF che comincia per STP


def test_issue6_codice_fiscale_riconosciuto():
    assert carattere_di_controllo("RSSMRA80A01H501") == "U"  # l'esempio classico
    assert e_codice_fiscale(CF_STP) and e_codice_fiscale("pnimra70a01h501p")
    assert not e_codice_fiscale("STPMRA70A01H501A")  # carattere di controllo sbagliato
    assert not e_codice_fiscale("STP0601010000001") and not e_codice_fiscale("STP")


def test_issue6_cf_ordinario_che_comincia_per_stp_accettato_dal_client(server, tls):
    r = _ricetta_farm(assistito=Assistito(CF_STP))
    assert xml_fvg.problemi_fvg(r) == []
    assert _servizio(server, tls).invia(r).ok


def test_issue6_cf_ordinario_che_comincia_per_stp_accettato_dal_server(server, tls):
    cli = _servizio(server, tls)
    cli.valida_localmente = False
    assert cli.invia(_ricetta_farm(assistito=Assistito(CF_STP))).ok


def test_issue6_gruppo_di_controllo_stp_malformato_resta_rifiutato(server, tls):
    """Col carattere di controllo sbagliato non è un CF: torna a valere la regola STP (13 cifre)."""
    r = _ricetta_farm(assistito=Assistito("STPMRA70A01H501A"))
    assert any("STP" in p for p in xml_fvg.problemi_fvg(r))
    cli = _servizio(server, tls)
    cli.valida_localmente = False
    e = cli.invia(r)
    assert not e.ok and e.errori[0].codice == "1001"


# ------------------------------------------------------------------ #7 carta e cf_medico


def test_issue7_cambiare_cf_medico_dopo_la_costruzione_non_fa_partire_il_sostituto(server, tls):
    cli = _servizio(server, tls)  # carta del titolare
    r = _ricetta_farm(prescrittore=Prescrittore(TITOLARE, "060", "101", "F", codice_fiscale_sostituto=SOSTITUTO))
    with pytest.raises(RicettaNonValida):
        cli.invia(r)
    with pytest.raises(AttributeError):
        cli.canale.cf_medico = SOSTITUTO
    vars(cli.canale)["_cf_medico"] = SOSTITUTO  # anche aggirando la proprietà
    with pytest.raises(ConfigurazioneNonValida, match="par. 2.2"):
        cli.invia(r)
    assert server.stato.ricette == {}


def test_issue7_modalita_stringa_normalizzata_e_controllata(tls):
    t = Cattura()
    ades = AdesioneFVG("ACCR-PROVA", APPLICATIVO)
    with pytest.raises(ConfigurazioneNonValida, match="certificato della carta"):
        CanaleFVG(TITOLARE, POSTAZIONE, adesione=ades, modalita="cns", trasporto=t)
    with pytest.raises(ConfigurazioneNonValida, match="sconosciuta"):
        CanaleFVG(TITOLARE, POSTAZIONE, adesione=ades, modalita="CNS ", trasporto=t)
    can = CanaleFVG(TITOLARE, POSTAZIONE, adesione=ades, modalita="cns", trasporto=t,
                    certificato_carta=tls.certificato_client_der(TITOLARE))
    assert can.modalita.name == "CNS"
    assert t.inoltri == []


# ------------------------------------------------------------------ #8 campi solo farmaceutici


SOLO_FARMACEUTICA = [("nota_aifa", "001"), ("note", "NOTA FARMACEUTICA"), ("codice_motivazione_non_sost", "1"),
                     ("non_sostituibile", True)]
SOLO_SPECIALISTICA = [("condizione_erogabilita", "H"), ("appropriatezza", "A"), ("patologia", "0A02")]


def _con(r, **campo):
    return dc.replace(r, righe=(dc.replace(r.righe[0], **campo),))


@pytest.mark.parametrize("campo,valore", SOLO_FARMACEUTICA)
def test_issue8_campo_farmaceutico_su_specialistica_rifiutato(server, tls, campo, valore):
    r = _con(_ricetta_spec(), **{campo: valore})
    assert any("solo per la farmaceutica" in p for p in xml_fvg.problemi_fvg(r))
    with pytest.raises(RicettaNonValida, match="pp. 20-21"):
        _servizio(server, tls).invia(r)
    cli = _servizio(server, tls)
    cli.valida_localmente = False
    if campo == "non_sostituibile":  # da solo il modello lo vieta prima del tracciato (serve il motivo)
        r = _con(_ricetta_spec(), non_sostituibile=True, codice_motivazione_non_sost="1")
    with pytest.raises(ErroreSOAP, match="solo farmaceutica"):
        cli.invia(r)
    assert server.stato.ricette == {}


@pytest.mark.parametrize("campo,valore", SOLO_SPECIALISTICA)
def test_issue8_campo_dm2015_su_farmaceutica_rifiutato(server, tls, campo, valore):
    r = _con(_ricetta_farm(), **{campo: valore})
    assert any("solo per la specialistica" in p for p in xml_fvg.problemi_fvg(r))
    cli = _servizio(server, tls)
    cli.valida_localmente = False
    with pytest.raises(ErroreSOAP, match="p. 21"):
        cli.invia(r)
    assert server.stato.ricette == {}


def test_issue8_gruppo_di_controllo_farmaceutica_con_nota_aifa_e_note(server, tls):
    r = _con(_ricetta_farm(), nota_aifa="001", note="NOTA FARMACEUTICA")
    assert xml_fvg.problemi_fvg(r) == [] and _servizio(server, tls).invia(r).ok
