# SPDX-License-Identifier: EUPL-1.2
"""Controesempi del giro 2 di revisione esterna, area SAR FVG (kit-mmg-review/2026-10-02-giro2/4-sar-fvg.md).

Ogni test riproduce il controesempio del revisore: falliva sul codice precedente, passa ora.
Fixture e aiuti (server finto mTLS, certificati di prova, ricette) vengono da test_fvg.py.
Pagine citate: specifiche/fvg/IDOF-DEM-00001-AT-16-01_v1.0.pdf (lette con pdftotext).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from types import MappingProxyType

import pytest

from test_fvg import (  # noqa: F401 - server e tls sono fixture
    APPLICATIVO,
    POSTAZIONE,
    SERVER,
    TITOLARE,
    XSD_DIR,
    _ricetta_farm,
    _ricetta_spec,
    _servizio,
    server,
    tls,
)

from varco import ConfigurazioneNonValida, ErroreSOAP
from varco.errori import RicettaNonValida
from varco.ricetta import Assistito, CriteriNreUtilizzati, RicettaFVG, TipoPrescrizione
from varco.ricetta import xml_fvg
from varco.trasporto.fvg import ApplicativoFVG, CanaleFVG, ServizioFVG
from varco.trasporto.http import Risposta


# ------------------------------------------------------------------ N1 endpoint cambiato dopo il costruttore


class Cattura:
    """Il trasporto del revisore: ha il flag del collaudo e conta gli inoltri."""

    consenti_collaudo_regionale = True

    def __init__(self):
        self.inoltri = []

    def invia(self, r):
        self.inoltri.append(r.url)
        return Risposta(200, SERVER.ricevuta_annulla("0000", "0600A0000000001"), {}, 0)


def test_g2_n1_endpoint_cambiato_dopo_la_costruzione_non_aggira_adesione_e_carta():
    """N1: canale per localhost, poi url[ANNULLA] = collaudo regionale; niente AdesioneFVG né carta.
    Prima: «NO_ADESIONE_INOLTRATA https://demtest...» e annulla(...).ok True."""
    t = Cattura()
    can = CanaleFVG(TITOLARE, POSTAZIONE, base_url="http://localhost", applicativo_di_prova=APPLICATIVO, trasporto=t)
    regionale = "https://demtest.sanita.fvg.it/SARWs/annullaPrescrittoSecure"
    with pytest.raises(TypeError):
        can.url[ServizioFVG.ANNULLA] = regionale  # gli endpoint sono di sola lettura
    RicettaFVG(can, object()).annulla("0600A0000000001")
    assert regionale not in t.inoltri and len(t.inoltri) == 1 and can.adesione is None


def test_g2_n1_anche_sostituendo_la_mappa_degli_endpoint_la_richiesta_non_parte():
    """N1, l'invariante sta nella chiamata: anche sostituendo tutta la mappa, verso la Regione senza
    adesione e senza carta non si inoltra nulla."""
    t = Cattura()
    can = CanaleFVG(TITOLARE, POSTAZIONE, base_url="http://localhost", applicativo_di_prova=APPLICATIVO, trasporto=t)
    regionale = {ServizioFVG.ANNULLA: "https://demtest.sanita.fvg.it/SARWs/annullaPrescrittoSecure"}
    try:
        can.url = regionale
    except AttributeError:
        vars(can)["_url"] = MappingProxyType(regionale)  # aggirando anche la proprietà
    with pytest.raises(ConfigurazioneNonValida, match="AdesioneFVG"):
        RicettaFVG(can, object()).annulla("0600A0000000001")
    assert t.inoltri == []


def test_g2_n1_gruppo_di_controllo_localhost_passa():
    t = Cattura()
    can = CanaleFVG(TITOLARE, POSTAZIONE, base_url="http://localhost", applicativo_di_prova=APPLICATIVO, trasporto=t)
    assert RicettaFVG(can, object()).annulla("0600A0000000001").ok
    assert t.inoltri == ["http://localhost/SARWs/annullaPrescrittoSecure"] or len(t.inoltri) == 1


# ------------------------------------------------------------------ N2 campi specialistici sulla farmaceutica


@pytest.mark.parametrize("campo,valore", [("codice_catalogo", "2774"), ("tipo_accesso", "1"), ("numero_nota", "1")])
def test_g2_n2_campo_specialistico_su_farmaceutica_rifiutato_dal_client(server, tls, campo, valore):
    """N2: la farmaceutica dei test con UN campo specialistico (p. 21: «unicamente per prescrizioni
    specialistiche»). Prima: esito 0000, e con numero_nota una nota con tipo_ambulatorio 'AMB-PROVA'."""
    cli = _servizio(server, tls)
    r = _ricetta_farm()
    rr = dataclasses.replace(r, righe=(dataclasses.replace(r.righe[0], **{campo: valore}),))
    with pytest.raises(RicettaNonValida, match="p. 21"):
        cli.invia(rr)
    assert server.stato.ricette == {}


@pytest.mark.parametrize("campo,valore", [("codice_catalogo", "2774"), ("tipo_accesso", "1"), ("numero_nota", "1")])
def test_g2_n2_campo_specialistico_su_farmaceutica_rifiutato_dal_server(server, tls, campo, valore):
    """N2, lato server finto: stessa richiesta senza controlli locali. Prima: 0000."""
    cli = _servizio(server, tls)
    cli.valida_localmente = False
    r = _ricetta_farm()
    rr = dataclasses.replace(r, righe=(dataclasses.replace(r.righe[0], **{campo: valore}),))
    with pytest.raises(ErroreSOAP, match="p. 21"):
        cli.invia(rr)
    assert server.stato.ricette == {}


def test_g2_n2_gruppo_di_controllo_specialistica_con_nota(server, tls):
    """Controllo: sulla specialistica i tre campi restano ammessi, e la nota porta il codice prestazione."""
    cli = _servizio(server, tls)
    r = _ricetta_spec()
    rr = dataclasses.replace(r, righe=(dataclasses.replace(r.righe[0], numero_nota="1"),))
    e = cli.invia(rr)
    assert e.ok and e.note and e.note[0].codice_prestazione == "88.39.9"


# ------------------------------------------------------------------ N3 STP malformato


def test_g2_n3_stp_troncato_al_prefisso_rifiutato(server, tls):
    """N3: Assistito("STP", tipo_ricetta="ST"). Prima: problemi=[], XSD True, esito 0000."""
    cli = _servizio(server, tls)
    r = _ricetta_farm(assistito=Assistito("STP", tipo_ricetta="ST"))
    assert xml_fvg.problemi_fvg(r)
    with pytest.raises(RicettaNonValida, match="STP"):
        cli.invia(r)
    cli.valida_localmente = False
    e = cli.invia(r)
    assert not e.ok and e.errori[0].codice == "1001"
    assert server.stato.ricette == {}


@pytest.mark.parametrize("codice", ["STP0601010000001", "ENI0601010000001"])
def test_g2_n3_gruppo_di_controllo_stp_eni_ben_formati(server, tls, codice):
    e = _servizio(server, tls).invia(_ricetta_farm(assistito=Assistito(codice, tipo_ricetta="ST")))
    assert e.ok and server.stato.ricette[e.nre].cf_assistito == codice


# ------------------------------------------------------------------ N4 tipoPrescr facoltativo nel FVG


def test_g2_n4_ricerca_nre_senza_tipo_ammessa_nel_fvg(server, tls):
    """N4: CriteriNreUtilizzati("060", dal=1/10, al=2/10) senza tipo. Lo XSD FVG (InterrogaNreUtilRichiesta.xsd)
    lo lascia facoltativo. Prima: RicettaNonValida «il SAC lo esige: errore 1153»."""
    cli = _servizio(server, tls)
    e1 = cli.invia(_ricetta_farm(data_compilazione=dt.datetime(2026, 10, 1, 11, 0)))
    e2 = cli.invia(_ricetta_spec(data_compilazione=dt.datetime(2026, 10, 1, 12, 0)))
    c = CriteriNreUtilizzati("060", dal=dt.datetime(2026, 10, 1), al=dt.datetime(2026, 10, 2))
    r = cli.interroga_nre_utilizzati(c)
    assert r.ok and {x.nre for x in r.ricette} == {e1.nre, e2.nre}


def test_g2_n4_gruppo_di_controllo_il_sac_resta_com_era():
    """Controllo: il modello comune (SAC) continua a chiedere il tipo."""
    c = CriteriNreUtilizzati("060", dal=dt.datetime(2026, 10, 1), al=dt.datetime(2026, 10, 2))
    assert any("1153" in p for p in c.problemi())


# ------------------------------------------------------------------ N5 regione sbagliata


def test_g2_n5_regione_diversa_da_060_rifiutata(server, tls):
    """N5: prescrittore con codice_regione '010' (p. 16: codRegione «060»). Prima: 0000 e visualizza 060."""
    cli = _servizio(server, tls)
    r0 = _ricetta_farm()
    r = dataclasses.replace(r0, prescrittore=dataclasses.replace(r0.prescrittore, codice_regione="010"))
    with pytest.raises(RicettaNonValida, match="060"):
        cli.invia(r)
    cli.valida_localmente = False
    with pytest.raises(ErroreSOAP, match="060"):
        cli.invia(r)
    assert server.stato.ricette == {}


def test_g2_n5_visualizza_restituisce_la_regione_inviata(server, tls):
    """N5: la rilettura restituisce il codRegione conservato, non una costante."""
    cli = _servizio(server, tls)
    e = cli.invia(_ricetta_farm())
    assert server.stato.ricette[e.nre].cod_regione == "060"
    server.stato.ricette[e.nre].cod_regione = "XYZ"  # se la visualizzazione inventasse, direbbe 060
    assert cli.visualizza(e.nre).testata.get("codRegione") == "XYZ"


# ------------------------------------------------------------------ N6 versioneCR con cifre Unicode


def test_g2_n6_versione_cr_con_cifre_arabe_rifiutata_in_locale(server, tls):
    """N6: ApplicativoFVG(..., versione_cr="١.٤.٤"). Prima: problemi=[] e poi Fault del server."""
    app = dataclasses.replace(APPLICATIVO, versione_cr="١.٤.٤")
    assert xml_fvg.problemi_versione_cr(_ricetta_spec(), app.versione_cr)
    cli = _servizio(server, tls, applicativo=app)
    with pytest.raises(RicettaNonValida, match="versioneCR"):
        cli.invia(_ricetta_spec())
    assert server.stato.richieste == []


def test_g2_n6_gruppo_di_controllo_versione_ascii():
    assert xml_fvg.problemi_versione_cr(_ricetta_spec(), "1.4.4") == []
