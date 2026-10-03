# SPDX-License-Identifier: EUPL-1.2
"""Test di integrazione: chiamate REALI all'ambiente di TEST del MEF (demservicetest).

Si attivano con  VARCO_INTEGRAZIONE=1  e usano solo utenze/assistiti del kit pubblico.
Ogni scambio XML viene salvato in prove/<data-ora>-pytest/.
Frequenza: al massimo 1 richiesta al secondo (limitatore del trasporto).
"""

import datetime as dt
from pathlib import Path

import pytest

from varco import CanaleSAC, Credenziali, ErroreSOAP, RegistratoreFile, RicettaSAC, TrasportoHTTP, kit_mef
from varco.ricetta import Assistito, ClassePriorita, Ricetta, Riga, TipoPrescrizione

pytestmark = pytest.mark.integrazione

RADICE = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def trasporto(kit):
    cartella = RADICE / "prove" / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-pytest")
    reg = RegistratoreFile(cartella, identita_di_test=kit_mef.identita_di_test(kit))
    return TrasportoHTTP(registratore=reg, intervallo_minimo_s=1.0)


@pytest.fixture(scope="module")
def servizio(kit, trasporto):
    return RicettaSAC(CanaleSAC(kit_mef.credenziali_medico(kit), trasporto=trasporto))


@pytest.fixture(scope="module")
def prescrittore(kit):
    pos = next(p for p in kit_mef.posizioni_medico(kit)
               if (p.codice_regione, p.codice_asl, p.codice_specializzazione, p.codice_struttura) == ("130", "201", "F", None))
    return pos.prescrittore()


@pytest.fixture(scope="module")
def assistito(kit):
    return Assistito(codice_fiscale=kit_mef.assistiti_test(kit)["ABRUZZO"][1], provincia="AQ", asl="201")


def test_ciclo_farmaceutica(servizio, prescrittore, assistito):
    r = Ricetta(prescrittore, assistito, TipoPrescrizione.FARMACEUTICA,
                [Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="LEVETIRACETAM 500MG 60 UNITA' USO ORALE")])
    e = servizio.invia(r)
    assert e.ok, e.messaggi
    assert len(e.nre) == 15 and e.codice_autenticazione and e.pdf_promemoria.startswith(b"%PDF")
    v = servizio.visualizza(e.nre)
    assert v.ok and v.stato_processo == "3" and v.codice_autenticazione == e.codice_autenticazione
    a = servizio.annulla(e.nre)
    assert a.ok
    assert servizio.visualizza(e.nre).stato_processo == "4"
    a2 = servizio.annulla(e.nre)
    assert not a2.ok and a2.errori[0].codice == "1120"


def test_specialistica(servizio, prescrittore, assistito):
    r = Ricetta(prescrittore, assistito, TipoPrescrizione.SPECIALISTICA,
                [Riga(1, codice="99.97.2", descrizione="TRATTAMENTI PER APPLICAZIONE DI PROTESI RIMOVIBILE", codice_catalogo="A099972")],
                classe_priorita=ClassePriorita.PROGRAMMATA, descrizione_diagnosi="CONTROLLO (dato di test)")
    e = servizio.invia(r)
    assert e.ok, e.messaggi
    assert servizio.annulla(e.nre).ok


def test_nre_inesistente(servizio):
    v = servizio.visualizza("1300A4099999999")
    assert not v.ok and v.errori[0].codice == "5005"


def test_utenza_inesistente(trasporto):
    s = RicettaSAC(CanaleSAC(Credenziali("UTENTEINESISTENT", "x", "0000000000"), trasporto=trasporto))
    with pytest.raises(ErroreSOAP) as e:
        s.visualizza("1300A4099999999")
    assert e.value.credenziali_rifiutate


# ------------------------------------------------------------------ medico sostituto (par. 4.2.1)


@pytest.fixture(scope="module")
def servizio_sostituto(kit, trasporto):
    return RicettaSAC(CanaleSAC(kit_mef.credenziali_sostituto(kit), trasporto=trasporto))


def test_sostituto_ciclo(servizio, servizio_sostituto, prescrittore, assistito):
    import dataclasses

    sost = servizio_sostituto.canale.credenziali.cf
    r = Ricetta(dataclasses.replace(prescrittore, codice_fiscale_sostituto=sost), assistito, TipoPrescrizione.FARMACEUTICA,
                [Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="LEVETIRACETAM 500MG 60 UNITA' USO ORALE")])
    e = servizio_sostituto.invia(r)
    assert e.ok, e.messaggi
    try:
        v = servizio.visualizza(e.nre, cf_medico=prescrittore.codice_fiscale)  # anche il titolare la vede
        assert v.ok and v.testata.get("cfMedico2") == sost
        rifiuto = servizio.annulla(e.nre, cf_medico=prescrittore.codice_fiscale)  # ma non la annulla
        assert not rifiuto.ok and rifiuto.errori[0].codice == "1125"
    finally:
        a = servizio_sostituto.annulla(e.nre, cf_medico=sost)
    assert a.ok, a.messaggi


# ------------------------------------------------------------------ InterrogaNreUtilizzati (par. 4.2.4)


def test_interroga_nre(servizio, prescrittore, assistito):
    from varco.ricetta import CriteriNreUtilizzati
    from varco.ricetta.modello import ora_italiana

    r = Ricetta(prescrittore, assistito, TipoPrescrizione.FARMACEUTICA,
                [Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="LEVETIRACETAM 500MG 60 UNITA' USO ORALE")])
    e = servizio.invia(r)
    assert e.ok, e.messaggi
    try:
        puntuale = servizio.interroga_nre_utilizzati(CriteriNreUtilizzati(prescrittore.codice_regione, nre=e.nre))
        assert puntuale.ok and [x.nre for x in puntuale.ricette] == [e.nre]
        oggi = ora_italiana()
        intervallo = servizio.interroga_nre_utilizzati(CriteriNreUtilizzati(
            prescrittore.codice_regione, dal=oggi.replace(hour=0, minute=0, second=0), al=oggi.replace(hour=23, minute=59, second=59),
            tipo=TipoPrescrizione.FARMACEUTICA))
        assert intervallo.ok and e.nre in [x.nre for x in intervallo.ricette]
    finally:
        assert servizio.annulla(e.nre).ok
