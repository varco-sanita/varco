# SPDX-License-Identifier: EUPL-1.2
"""Il formato dei casi di conformità come specifica a sé (conformita/schema), indipendente dal Python."""

import copy
import dataclasses
import json

import pytest

from varco.conformita.esegui import main as esegui_suite
from varco.conformita.motore import Motore, carica_casi, cartella_conformita, verifica_documento
from varco.fse import modello as M
from varco.fse.validazione import ValidatoreLocale, dipendenze_locali_presenti
from varco.ricetta import modello as R

jsonschema = pytest.importorskip("jsonschema")
from referencing import Registry, Resource  # noqa: E402

CONF = cartella_conformita()
SCHEMI = CONF / "schema"
BASE_ID = "https://varco.invalid/conformita/schema/"


def _validatore(nome: str):
    registro = Registry().with_resources(
        (BASE_ID + p.name, Resource.from_contents(json.loads(p.read_text(encoding="utf-8")))) for p in SCHEMI.glob("*.json")
    )
    return jsonschema.Draft202012Validator(json.loads((SCHEMI / f"{nome}.schema.json").read_text(encoding="utf-8")), registry=registro)


def test_gli_schemi_sono_json_schema_validi():
    for p in SCHEMI.glob("*.schema.json"):
        jsonschema.Draft202012Validator.check_schema(json.loads(p.read_text(encoding="utf-8")))


@pytest.mark.parametrize("caso", sorted(p.name for p in (CONF / "casi").glob("*.json")))
def test_ogni_caso_rispetta_lo_schema(caso):
    dati = json.loads((CONF / "casi" / caso).read_text(encoding="utf-8"))
    errori = [f"{e.json_path}: {e.message}" for e in _validatore("caso").iter_errors(dati)]
    assert errori == []
    assert dati["id"] + ".json" == caso


def test_i_dati_pss_rispettano_lo_schema():
    v = _validatore("pss")
    for p in (CONF / "dati").glob("*.json"):
        assert [e.message for e in v.iter_errors(json.loads(p.read_text(encoding="utf-8")))] == [], p.name


@pytest.mark.parametrize(
    "guasto",
    [
        lambda c: c["passi"][0].update(operazione="cancella_tutto"),
        lambda c: c["passi"][0].pop("atteso"),
        lambda c: c["passi"][0]["atteso"].update(codice_magico=1),
        lambda c: c.update(famiglia="altro"),
        lambda c: c.pop("riferimento"),
    ],
)
def test_lo_schema_boccia_casi_guasti(guasto):
    """Gruppo di controllo: uno schema che accetta tutto non specifica niente."""
    caso = json.loads((CONF / "casi" / "OFF-013.json").read_text(encoding="utf-8"))
    guasto(caso)
    assert list(_validatore("caso").iter_errors(caso))


@pytest.mark.parametrize(
    "schema,percorso,classe",
    [
        ("ricetta", [], R.Ricetta),
        ("ricetta", ["$defs", "prescrittore"], R.Prescrittore),
        ("ricetta", ["$defs", "assistito"], R.Assistito),
        ("ricetta", ["$defs", "riga"], R.Riga),
        ("pss", [], M.ProfiloSanitarioSintetico),
        ("pss", ["$defs", "paziente"], M.Paziente),
        ("pss", ["$defs", "medico"], M.Medico),
        ("pss", ["$defs", "allergia"], M.Allergia),
        ("pss", ["$defs", "terapia"], M.Terapia),
        ("pss", ["$defs", "problema"], M.Problema),
        ("pss", ["$defs", "anamnesi"], M.AnamnesiFamiliare),
        ("pss", ["$defs", "esenzione"], M.Esenzione),
        ("pss", ["$defs", "indirizzo"], M.Indirizzo),
        ("pss", ["$defs", "custode"], M.Custode),
    ],
)
def test_schema_e_modello_allineati(schema, percorso, classe):
    """Se il modello cambia e lo schema no (o viceversa), i casi smettono di essere una specifica."""
    nodo = json.loads((SCHEMI / f"{schema}.schema.json").read_text(encoding="utf-8"))
    for chiave in percorso:
        nodo = nodo[chiave]
    assert set(nodo["properties"]) == {f.name for f in dataclasses.fields(classe)}


def test_i_file_citati_dai_casi_esistono():
    for caso in carica_casi("tutte"):
        for passo in caso["passi"] + caso.get("finale", []):
            if "risposta" in passo:
                assert (CONF / "risposte" / passo["risposta"]).exists(), caso["id"]
            for chiave in ("documento", "dati"):
                if isinstance(passo.get(chiave), str):
                    assert (CONF / passo[chiave]).exists(), caso["id"]


def test_famiglie_e_numero_casi():
    casi = carica_casi("tutte")
    famiglie = {c["famiglia"] for c in casi}
    assert famiglie == {"offline", "online", "fse", "sist", "fvg", "piemonte"}
    assert len(carica_casi("fse")) >= 10 and len(carica_casi("online")) >= 15 and len(carica_casi("offline")) >= 20
    assert len(carica_casi("sist")) >= 23
    assert len(carica_casi("fvg")) >= 29
    assert len(carica_casi("piemonte")) >= 44


# ------------------------------------------------------------------ esecutore Python: famiglia fse


@pytest.mark.skipif(not dipendenze_locali_presenti(), reason="servono lxml e saxonche")
@pytest.mark.schemi_hl7
def test_suite_fse_con_validatore_locale_nessun_rosso(capsys):
    """Col validatore locale (senza vocabolari) niente deve risultare FALLITO: i casi che dipendono
    dai vocabolari devono venire SALTATO, non verde e non rosso."""
    assert esegui_suite(["--famiglia", "fse", "--validatore-fse", "locale"]) == 0
    out = capsys.readouterr().out
    assert "falliti 0" in out and "errori 0" in out
    for saltato in ("FSE-003", "FSE-004", "FSE-104"):
        assert f"SALTATO   {saltato}" in out


@pytest.mark.skipif(not dipendenze_locali_presenti(), reason="servono lxml e saxonche")
@pytest.mark.parametrize(
    "atteso",
    [{"esito": "OK"}, {"valido": True}, {"errori_contengono": ["ERRORE-999|"]}, {"senza_errori": True}, {"esito": ["OK", "SYNTAX_ERROR"]}],
)
@pytest.mark.schemi_hl7
def test_il_motore_fse_boccia_aspettative_sbagliate(atteso):
    caso = {"id": "FSE-900", "titolo": "t", "famiglia": "fse", "riferimento": "r",
            "passi": [{"operazione": "valida_documento", "documento": "documenti/pss_05_ko_senza_allergie.xml", "atteso": atteso}]}
    assert Motore(validatore_fse=ValidatoreLocale()).esegui(caso).stato == "FALLITO"


def test_generatore_che_rifiuta_i_dati():
    caso = json.loads((CONF / "casi" / "FSE-103.json").read_text(encoding="utf-8"))

    class Validatore:  # non deve nemmeno essere chiamato
        def valida(self, xml):
            raise AssertionError("chiamato")

    assert Motore(validatore_fse=Validatore()).esegui(caso).stato == "SUPERATO"
    caso2 = copy.deepcopy(caso)
    caso2["passi"][0]["atteso"] = {"esito": "OK"}
    assert Motore(validatore_fse=Validatore()).esegui(caso2).stato == "FALLITO"


@pytest.mark.schemi_hl7
def test_generatore_esterno_collaudato():
    """Un'altra implementazione si collauda passando il suo generatore: qui uno che sbaglia il CF."""
    from varco.conformita.motore import genera_pss_kit

    def generatore_difettoso(dati):
        return genera_pss_kit(dati).replace(b"PNIMRA70A01H501P", b"PNIMRA70A01H501")

    if not dipendenze_locali_presenti():
        pytest.skip("servono lxml e saxonche")
    caso = json.loads((CONF / "casi" / "FSE-102.json").read_text(encoding="utf-8"))
    # Col validatore locale (senza vocabolari) l'esito OK atteso non si può confermare: SALTATO, non verde
    # (prima era SUPERATO: un verde su un controllo dei vocabolari mai fatto).
    assert Motore(validatore_fse=ValidatoreLocale()).esegui(caso).stato == "SALTATO"
    # Il difetto del generatore invece si vede già allo schema/schematron: rosso certo, vocabolari o no.
    assert Motore(validatore_fse=ValidatoreLocale(), generatore_pss=generatore_difettoso).esegui(caso).stato == "FALLITO"


def test_verifica_documento_regole():
    oss = {"esito": "SEMANTIC_ERROR", "errori": ["ERRORE-b1| x"], "avvisi": []}
    assert verifica_documento({"esito": "SEMANTIC_ERROR", "errori_contengono": ["ERRORE-b1|"], "valido": False}, oss) == []
    assert verifica_documento({"valido": True}, oss)
    assert verifica_documento({"valido": False}, {"rifiuto_locale": "no"}) == []
    assert verifica_documento({"esito": "OK"}, {"rifiuto_locale": "no"})


def test_caso_online_col_sostituto_senza_credenziali_saltato():
    class Finto:
        def invia(self, r):
            raise AssertionError("non doveva partire")

    caso = json.loads((CONF / "casi" / "SAC-010.json").read_text(encoding="utf-8"))
    from varco.conformita.esegui import contesto_offline

    m = Motore(adattatore=lambda c, v: Finto(), credenziali=object(), contesto=contesto_offline())
    r = m.esegui(caso)
    assert r.stato == "SALTATO" and "sostituto" in r.motivo


# ------------------------------------------------------------------ esecutore Python: famiglia sist (SIST Puglia)

XSD_SIST = CONF.parent / "specifiche" / "sist" / "specifiche SIST 4.02.27" / "wsdl-pddasl" / "CVPService.xsd"


@pytest.mark.parametrize(
    "guasto",
    [
        lambda c: c["passi"][0].update(risposta="chk_ok.xml"),  # le risposte SIST stanno in risposte/sist/
        lambda c: c["passi"][0]["atteso"].update(tag={"x": "y"}),  # aspettativa di codifica su una lettura
        lambda c: c.update(id="SIST-001"),
    ],
)
def test_lo_schema_boccia_casi_sist_guasti(guasto):
    caso = json.loads((CONF / "casi" / "SIS-001.json").read_text(encoding="utf-8"))
    guasto(caso)
    assert list(_validatore("caso").iter_errors(caso))


@pytest.mark.schemi_hl7
def test_suite_sist_senza_xsd_salta_e_non_boccia(capsys, monkeypatch):
    """Senza CVPService.xsd la parte XSD è SALTATO (lo schema della Regione non sta nel repository)."""
    pytest.importorskip("lxml")
    monkeypatch.delenv("VARCO_XSD_SIST", raising=False)
    assert esegui_suite(["--famiglia", "sist"]) == 0
    out = capsys.readouterr().out
    assert "falliti 0" in out and "errori 0" in out
    assert "SALTATO   SIS-101" in out and "SUPERATO  SIS-106" in out and "SUPERATO  SIS-001" in out


@pytest.mark.skipif(not XSD_SIST.exists(), reason="specifiche SIST non scaricate (strumenti/scarica_specifiche.py --gruppi sist)")
@pytest.mark.schemi_hl7
def test_suite_sist_con_xsd_tutto_verde(capsys):
    assert esegui_suite(["--famiglia", "sist", "--xsd-sist", str(XSD_SIST)]) == 0
    out = capsys.readouterr().out
    assert f"superati {len(carica_casi('sist'))}" in out and "saltati 0" in out


@pytest.mark.parametrize(
    "caso_id,cambia",
    [
        ("SIS-001", lambda p: p["atteso"].update(nre="1600A0000000002")),
        ("SIS-004", lambda p: p["atteso"].update(campi={"solo_ricetta_rossa": False})),
        ("SIS-007", lambda p: p.update(risposta="sist/annulla_ok.xml")),  # atteso un Fault, arriva un esito
        ("SIS-104", lambda p: p.update(codici_regionali={"PROVAX00X00X000Y": "000001"}, ricetta={**p["ricetta"], "assistito": {**p["ricetta"]["assistito"], "codice_regione": "160"}})),
        ("SIS-106", lambda p: p["atteso"]["attributi"].update({"code@code": "29305-0"})),
        ("SIS-108", lambda p: p.update(maggior_tutela=False)),
    ],
)
@pytest.mark.schemi_hl7
def test_il_motore_sist_boccia_aspettative_sbagliate(caso_id, cambia):
    """Gruppo di controllo: ogni caso SIST, con un dettaglio cambiato, deve risultare FALLITO."""
    pytest.importorskip("lxml")
    caso = json.loads((CONF / "casi" / f"{caso_id}.json").read_text(encoding="utf-8"))
    passo = caso["passi"][-1] if caso_id == "SIS-007" else caso["passi"][0]
    cambia(passo)
    assert Motore(xsd_sist=XSD_SIST if XSD_SIST.exists() else None).esegui(caso).stato == "FALLITO"


# ------------------------------------------------------------------ esecutore Python: famiglia fvg (SAR Friuli-Venezia Giulia)

XSD_FVG = CONF.parent / "specifiche" / "fvg" / "wsdl" / "sar"


@pytest.mark.parametrize(
    "guasto",
    [
        lambda c: c["passi"][0].update(risposta="invio_ok.xml"),  # le risposte FVG stanno in risposte/fvg/
        lambda c: c["passi"][0].update(servizio="lotti"),  # servizio non previsto
        lambda c: c["passi"][0].pop("servizio"),
        lambda c: c.update(id="FVG-01"),
    ],
)
def test_lo_schema_boccia_casi_fvg_guasti(guasto):
    caso = json.loads((CONF / "casi" / "FVG-001.json").read_text(encoding="utf-8"))
    guasto(caso)
    assert list(_validatore("caso").iter_errors(caso))


def test_lo_schema_vuole_i_dati_giusti_per_il_servizio_fvg():
    caso = json.loads((CONF / "casi" / "FVG-107.json").read_text(encoding="utf-8"))
    caso["passi"][0].pop("nre")
    assert list(_validatore("caso").iter_errors(caso))


def test_suite_fvg_senza_xsd_salta_e_non_boccia(capsys, monkeypatch):
    """Senza gli XSD di Insiel la parte XSD è SALTATO (gli schemi non stanno nel repository)."""
    pytest.importorskip("lxml")
    monkeypatch.delenv("VARCO_XSD_FVG", raising=False)
    assert esegui_suite(["--famiglia", "fvg"]) == 0
    out = capsys.readouterr().out
    assert "falliti 0" in out and "errori 0" in out
    assert "SALTATO   FVG-101" in out and "SUPERATO  FVG-104" in out and "SUPERATO  FVG-001" in out


@pytest.mark.skipif(not XSD_FVG.exists(), reason="specifiche FVG non scaricate (strumenti/scarica_specifiche.py --gruppi fvg)")
def test_suite_fvg_con_xsd_tutto_verde(capsys):
    assert esegui_suite(["--famiglia", "fvg", "--xsd-fvg", str(XSD_FVG)]) == 0
    out = capsys.readouterr().out
    assert f"superati {len(carica_casi('fvg'))}" in out and "saltati 0" in out


@pytest.mark.parametrize(
    "caso_id,cambia",
    [
        ("FVG-001", lambda p: p["atteso"].update(nre="0600A0000000002")),
        ("FVG-005", lambda p: p["atteso"].update(campi={"downgrade_mir": False})),
        ("FVG-006", lambda p: p.update(risposta="fvg/invio_rifiuto_1020.xml")),  # niente downgrade
        ("FVG-012", lambda p: p["atteso"].update(campi={"abilitato": False})),
        ("FVG-101", lambda p: p["atteso"]["attributi"].update({"@prodottoCme": "ALTRO"})),
        ("FVG-101", lambda p: p["atteso"].update(tag_assenti=["pinCode"])),
        ("FVG-104", lambda p: p["ricetta"]["righe"][0].pop("num_sedute")),  # niente più rifiuto locale
        ("FVG-110", lambda p: p["atteso"]["tag"].update(codLotto="0600A01234567")),
    ],
)
def test_il_motore_fvg_boccia_aspettative_sbagliate(caso_id, cambia):
    """Gruppo di controllo: ogni caso FVG, con un dettaglio cambiato, deve risultare FALLITO."""
    pytest.importorskip("lxml")
    caso = json.loads((CONF / "casi" / f"{caso_id}.json").read_text(encoding="utf-8"))
    cambia(caso["passi"][0])
    assert Motore(xsd_fvg=XSD_FVG if XSD_FVG.exists() else None).esegui(caso).stato == "FALLITO"


@pytest.mark.skipif(not XSD_FVG.exists(), reason="specifiche FVG non scaricate (strumenti/scarica_specifiche.py --gruppi fvg)")
def test_il_motore_fvg_boccia_una_codifica_non_valida(monkeypatch):
    """Gruppo di controllo sull'XSD: un codec che manda i facoltativi vuoti (come verso il SAC) deve essere bocciato."""
    from varco.ricetta import xml_fvg

    originale = xml_fvg.richiesta_invio

    def difettoso(*a, **k):
        el = originale(*a, **k)
        import xml.etree.ElementTree as ET

        el.insert(5, ET.Element(f"{{{xml_fvg.NS_INVIO_RICH}}}tipoRic"))
        return el

    monkeypatch.setattr(xml_fvg, "richiesta_invio", difettoso)
    caso = json.loads((CONF / "casi" / "FVG-101.json").read_text(encoding="utf-8"))
    assert Motore(xsd_fvg=XSD_FVG).esegui(caso).stato == "FALLITO"
