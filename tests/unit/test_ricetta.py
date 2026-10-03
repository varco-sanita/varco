# SPDX-License-Identifier: EUPL-1.2
import dataclasses
import datetime as dt
import xml.etree.ElementTree as ET
from importlib import resources

import pytest

from varco import Credenziali, RicettaNonValida
from varco.ricetta import (
    Assistito,
    ClassePriorita,
    Messaggio,
    Prescrittore,
    Ricetta,
    RicettaSAC,
    Riga,
    TipoPrescrizione,
)
from varco.ricetta import xml_sac
from varco.ricetta.json import ricetta_a_dict, ricetta_da_dict
from varco.schemi import errori_xsd
from varco.trasporto import CanaleSAC, Risposta
from varco.trasporto.soap import sbusta

PR = Prescrittore("PROVAX00X00X000Y", "130", "201", "F")
ASS = Assistito(codice_fiscale="PNIMRA70A01H501P", provincia="AQ", asl="201")
RIGA_F = Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="LEVETIRACETAM 500MG 60 UNITA' USO ORALE")
RIGA_P = Riga(1, codice="99.97.2", descrizione="TRATTAMENTI PER APPLICAZIONE DI PROTESI RIMOVIBILE", codice_catalogo="A099972")
DATA = dt.datetime(2026, 9, 30, 16, 5, 7)


def farmaceutica(**kw):
    return Ricetta(PR, ASS, TipoPrescrizione.FARMACEUTICA, [RIGA_F], data_compilazione=DATA, **kw)


def specialistica(**kw):
    kw.setdefault("descrizione_diagnosi", "controllo")
    return Ricetta(PR, ASS, TipoPrescrizione.SPECIALISTICA, [RIGA_P], data_compilazione=DATA, **kw)


def finta_cifra(s: str) -> str:
    return f"CIFRATO({s})"


def risposta(nome: str) -> bytes:
    from varco.conformita.motore import cartella_conformita

    return cartella_conformita().joinpath("risposte", nome).read_bytes()


# ------------------------------------------------------------- modello ----


def test_ricette_valide_non_hanno_problemi():
    assert farmaceutica().problemi() == []
    assert specialistica(classe_priorita=ClassePriorita.URGENTE).problemi() == []


@pytest.mark.parametrize(
    "ricetta,frammento",
    [
        (Ricetta(PR, ASS, TipoPrescrizione.FARMACEUTICA, []), "almeno una riga"),
        (farmaceutica(nre="123"), "NRE"),
        (dataclasses.replace(specialistica(), descrizione_diagnosi=None), "diagnosi"),
        (Ricetta(PR, ASS, TipoPrescrizione.FARMACEUTICA, [Riga(12, codice="1", descrizione="x")]), "quantità"),
        (Ricetta(PR, ASS, TipoPrescrizione.FARMACEUTICA, [Riga(1)]), "farmaco senza"),
        (Ricetta(PR, ASS, TipoPrescrizione.FARMACEUTICA, [Riga(1, codice="1", descrizione="x", non_sostituibile=True)]), "motivazione"),
        (Ricetta(PR, ASS, TipoPrescrizione.SPECIALISTICA, [Riga(1, descrizione="x")], descrizione_diagnosi="d"), "senza codice"),
        (Ricetta(dataclasses.replace(PR, codice_regione="13"), ASS, TipoPrescrizione.FARMACEUTICA, [RIGA_F]), "regione"),
        (Ricetta(PR, Assistito(codice_fiscale="X", provincia="AQ"), TipoPrescrizione.FARMACEUTICA, [RIGA_F]), "insieme"),
        (Ricetta(PR, Assistito(), TipoPrescrizione.FARMACEUTICA, [RIGA_F]), "codice assistito"),
        (Ricetta(PR, Assistito(tipo_ricetta="ZZ"), TipoPrescrizione.FARMACEUTICA, [RIGA_F]), "tipo ricetta"),
    ],
)
def test_controlli_strutturali(ricetta, frammento):
    with pytest.raises(RicettaNonValida) as e:
        ricetta.valida()
    assert any(frammento in p for p in e.value.problemi), e.value.problemi


@pytest.mark.parametrize(
    "tipo,gravita",
    [("Bloccante", "E"), ("E", "E"), ("W", "W"), ("Non bloccante", "W"), ("Avviso", "W"), (None, None), ("", None), ("??", "E")],
)
def test_gravita_messaggi(tipo, gravita):
    assert Messaggio("1", tipo=tipo).gravita == gravita


# --------------------------------------------------------------- codec ----


def test_codifica_farmaceutica_valida_xsd_e_cifra():
    el = xml_sac.richiesta_invio(farmaceutica(), "1234567890", finta_cifra)
    assert errori_xsd(el) == []
    v = {e.tag.rsplit("}", 1)[1]: e.text for e in el}
    assert v["pinCode"] == "CIFRATO(1234567890)"
    assert v["codiceAss"] == "CIFRATO(PNIMRA70A01H501P)"
    assert v["dataCompilazione"] == "2026-09-30 16:05:07"  # 24 ore, formato aaaa-mm-gg HH:MM:SS
    assert v["nonEsente"] == "1" and v["reddito"] is None and v["nre"] is None
    assert "statoEstero" not in v  # blocco esteri solo se valorizzato


def test_ordine_tag_segue_xsd():
    """L'ordine si legge dallo XSD del kit MEF, non da xml_sac._ORDINE_TESTATA (che è ciò che si
    collauda: confrontarlo con sé stesso passava anche con la costante rovesciata)."""
    xs = "{http://www.w3.org/2001/XMLSchema}"

    def _sequenza_xsd(radice: ET.Element, nome_tipo: str | None = None, nome_elemento: str | None = None):
        """(nome, obbligatorio) degli elementi della xs:sequence, letti dallo XSD UFFICIALE (non dal produttore)."""
        if nome_elemento:
            nodo = next(e for e in radice.iter(f"{xs}element") if e.get("name") == nome_elemento)
        else:
            nodo = next(t for t in radice.iter(f"{xs}complexType") if t.get("name") == nome_tipo)
        sequenza = next(nodo.iter(f"{xs}sequence"))
        return [(e.get("name"), e.get("minOccurs", "1") != "0") for e in sequenza.findall(f"{xs}element")]

    schemi = resources.files("varco.schemi")
    richiesta = ET.fromstring(schemi.joinpath("InvioPrescrittoRichiesta.xsd").read_bytes())
    tipi = ET.fromstring(schemi.joinpath("TipiDati.xsd").read_bytes())
    testata = _sequenza_xsd(richiesta, nome_elemento="InvioPrescrittoRichiesta")
    nomi_testata = [n for n, _ in testata]
    assert nomi_testata[:2] == ["pinCode", "cfMedico1"] and nomi_testata[-1] == "ElencoDettagliPrescrizioni"  # XSD letto davvero
    tipo_dettaglio = next(e.get("type").split(":")[-1] for e in tipi.iter(f"{xs}element")
                          if e.get("name") == "DettaglioPrescrizione")
    riga = _sequenza_xsd(tipi, nome_tipo=tipo_dettaglio)

    def controlla(sequenza: list[tuple[str, bool]], prodotti: list[str]):
        nomi = [n for n, _ in sequenza]
        assert set(prodotti) <= set(nomi), set(prodotti) - set(nomi)
        assert prodotti == [n for n in nomi if n in prodotti]  # stesso ordine dello XSD
        assert [n for n, obbligatorio in sequenza if obbligatorio and n not in prodotti] == []

    ass_estero = Assistito(tipo_ricetta="UE", stato_estero="LU", istituzione_competente="0018-CNS", num_ident_personale="1",
                           num_ident_tessera="2", data_nascita_estero="1983-06-12", data_scadenza_tessera="2030-01-04")
    for ricetta in (farmaceutica(), Ricetta(PR, ass_estero, TipoPrescrizione.SPECIALISTICA, [RIGA_P], data_compilazione=DATA,
                                            descrizione_diagnosi="d", classe_priorita=ClassePriorita.BREVE)):
        el = xml_sac.richiesta_invio(ricetta, "1", finta_cifra)
        controlla(testata, [e.tag.rsplit("}", 1)[1] for e in el])
        for dettaglio in el[-1]:
            controlla(riga, [e.tag.rsplit("}", 1)[1] for e in dettaglio])


def test_codifica_specialistica_e_estero():
    ass = Assistito(tipo_ricetta="UE", stato_estero="LU", istituzione_competente="0018-CNS", num_ident_personale="1",
                    num_ident_tessera="2", data_nascita_estero="1983-06-12", data_scadenza_tessera="2030-01-04")
    r = Ricetta(PR, ass, TipoPrescrizione.SPECIALISTICA, [RIGA_P], data_compilazione=DATA, descrizione_diagnosi="d",
                classe_priorita=ClassePriorita.BREVE)
    r.valida()
    el = xml_sac.richiesta_invio(r, "1", finta_cifra)
    assert errori_xsd(el) == []
    v = {e.tag.rsplit("}", 1)[1]: e.text for e in el}
    assert v["codiceAss"] is None and v["tipoRic"] == "UE" and v["statoEstero"] == "LU" and v["classePriorita"] == "B"


def test_quantita_a_due_cifre_viola_xsd():
    r = Ricetta(PR, ASS, TipoPrescrizione.FARMACEUTICA, [dataclasses.replace(RIGA_F, quantita=12)])
    assert errori_xsd(xml_sac.richiesta_invio(r, "1", finta_cifra))


def test_richieste_visualizza_annulla_valide_xsd():
    assert errori_xsd(xml_sac.richiesta_visualizza("1300A4019294833", "CF", "1", finta_cifra)) == []
    assert errori_xsd(xml_sac.richiesta_annulla("1300A4019294833", "CF", "1", finta_cifra)) == []


# ------------------------------------------- lettura risposte REALI ----


def test_lettura_invio_reale():
    e = xml_sac.leggi_ricevuta_invio(sbusta(risposta("invio_ok.xml")))
    assert e.ok and e.nre == "1300A4019294833" and e.codice_autenticazione == "300920261644168700000050622782"
    assert (e.cognome_medico, e.nome_medico) == ("PRO", "VA")
    assert e.pdf_promemoria.startswith(b"%PDF") and e.errori == ()
    assert "pdf_promemoria" not in repr(e)


def test_lettura_visualizza_reale():
    e = xml_sac.leggi_ricevuta_visualizza(sbusta(risposta("visualizza_stato3.xml")))
    assert e.ok and e.stato_processo == "3" and e.testata["codRegione"] == "130"
    assert e.righe[0]["codGruppoEquival"] == "G3B"
    assert "codiceAss" not in e.testata  # il SAC non restituisce il CF (tag vuoto)


def test_lettura_rifiuto_reale():
    e = xml_sac.leggi_ricevuta_annulla(sbusta(risposta("annulla_rifiuto_1120.xml")))
    assert not e.ok and e.codice == "9999"
    assert [m.codice for m in e.errori] == ["1120"] and e.errori[0].bloccante


def test_radice_inattesa():
    with pytest.raises(ValueError):
        xml_sac.leggi_ricevuta_invio(sbusta(risposta("annulla_ok.xml")))


# ------------------------------------------------------------- json ----


def test_json_andata_e_ritorno():
    r = specialistica(classe_priorita=ClassePriorita.PROGRAMMATA)
    d = ricetta_a_dict(r)
    assert d["tipo"] == "P" and d["classe_priorita"] == "P" and d["non_esente"] is True
    assert ricetta_da_dict(d) == r


def test_json_campi_sconosciuti():
    d = ricetta_a_dict(farmaceutica())
    d["righe"][0]["dose"] = "x"
    with pytest.raises(ValueError):
        ricetta_da_dict(d)


# ------------------------------------ servizio con trasporto finto ----


class TrasportoFinto:
    def __init__(self, *corpi: bytes):
        self.corpi, self.richieste = list(corpi), []

    def invia(self, r):
        self.richieste.append(r)
        return Risposta(200, self.corpi.pop(0), {}, 0.0)


def servizio(t):
    return RicettaSAC(CanaleSAC(Credenziali("PROVAX00X00X000Y", "pw", "1234567890"), trasporto=t))


def test_servizio_invia_con_trasporto_finto():
    t = TrasportoFinto(risposta("invio_ok.xml"))
    e = servizio(t).invia(farmaceutica())
    assert e.nre == "1300A4019294833"
    r = t.richieste[0]
    assert r.url.endswith("/DemRicettaPrescrittoServicesWeb/services/demInvioPrescritto")
    corpo = sbusta(r.corpo)
    assert errori_xsd(corpo) == []
    testo = r.corpo.decode()
    assert "PNIMRA70A01H501P" not in testo and "1234567890" not in testo  # mai in chiaro


def test_servizio_non_chiama_con_ricetta_non_valida():
    t = TrasportoFinto()
    with pytest.raises(RicettaNonValida):
        servizio(t).invia(Ricetta(PR, ASS, TipoPrescrizione.FARMACEUTICA, []))
    assert t.richieste == []


@pytest.mark.parametrize("nre", [None, "", "123"])
def test_servizio_non_chiama_con_nre_non_valido(nre):
    t = TrasportoFinto()
    with pytest.raises(ValueError):
        servizio(t).annulla(nre)
    with pytest.raises(ValueError):
        servizio(t).visualizza(nre)
    assert t.richieste == []


def test_servizio_visualizza_annulla_usa_cf_delle_credenziali():
    t = TrasportoFinto(risposta("visualizza_stato3.xml"), risposta("annulla_ok.xml"))
    s = servizio(t)
    assert s.visualizza("1300A4019294833").stato_processo == "3"
    assert s.annulla("1300A4019294833").ok
    for r in t.richieste:
        assert ET.fromstring(r.corpo).find(".//{*}cfMedico").text == "PROVAX00X00X000Y"
