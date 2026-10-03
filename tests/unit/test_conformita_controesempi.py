# SPDX-License-Identifier: EUPL-1.2
"""I controesempi della revisione esterna del 02/10/2026 (6-conformita-test-docs.md punti 1, 2, 6;
5-sar-piemonte.md punto 2), riprodotti esattamente. Ognuno era un verde (o un exit 0) falso.

Regola: il motore confronta OGNI aspettativa dichiarata; un'aspettativa che non sa verificare,
o che contraddice un'altra, è FALLITO; uno schema che manca è SALTATO, mai SUPERATO; una suite
che non esegue niente non è verde.
"""

import copy
import json

import pytest

from varco.conformita import motore as mod_motore
from varco.conformita.esegui import contesto_offline
from varco.conformita.esegui import main as esegui_suite
from varco.conformita.motore import Motore as _Motore
from varco.conformita.motore import cartella_conformita

CONF = cartella_conformita()
SPEC = CONF.parent / "specifiche"
XSD_FVG = SPEC / "fvg" / "wsdl" / "sar"
XSD_A2F = SPEC / "piemonte" / "a2f" / "Kit per lo sviluppo - A2F SistemaTS - ver. 20250902" / "wsdl"


def Motore(**kw) -> _Motore:  # noqa: N802 - i casi usano i segnaposto dell'esecutore (${prescrittore}...)
    return _Motore(contesto=contesto_offline(), **kw)


def _caso(caso_id: str) -> dict:
    return json.loads((CONF / "casi" / f"{caso_id}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ punto 1: ogni aspettativa si confronta


def test_fse103_rifiuto_locale_non_chiude_il_confronto():
    """Controesempio esatto: FSE-103 con atteso incoerente era SUPERATO perché il rifiuto locale
    con valido:false chiudeva il confronto."""
    caso = _caso("FSE-103")
    caso["passi"][0]["atteso"] = {"valido": False, "esito": "OK", "errori_contengono": ["IMPOSSIBILE"], "senza_errori": True}

    class Validatore:  # FSE-103 rifiuta prima di generare: il validatore non si chiama
        def valida(self, xml):
            raise AssertionError("chiamato")

    r = Motore(validatore_fse=Validatore()).esegui(caso)
    assert r.stato == "FALLITO", r


def test_fse103_rifiuto_locale_con_esito_atteso_e_fallito():
    """Coerente ma non verificabile: con un rifiuto locale non c'è nessun esito del validatore da confrontare."""
    caso = _caso("FSE-103")
    caso["passi"][0]["atteso"] = {"valido": False, "esito": "SEMANTIC_ERROR"}

    class Validatore:
        def valida(self, xml):
            raise AssertionError("chiamato")

    assert Motore(validatore_fse=Validatore()).esegui(caso).stato == "FALLITO"
    # gruppo di controllo: il caso vero resta verde
    assert Motore(validatore_fse=Validatore()).esegui(_caso("FSE-103")).stato == "SUPERATO"


def test_off001_rifiuto_locale_atteso_su_risposta_riuscita_e_fallito():
    """Controesempio esatto: OFF-001 con atteso={"rifiuto_locale": true} era SUPERATO."""
    caso = _caso("OFF-001")
    caso["passi"][0]["atteso"] = {"rifiuto_locale": True}
    assert Motore().esegui(caso).stato == "FALLITO"


def test_off011_xsd_valido_false_su_richiesta_valida_e_fallito():
    """Controesempio esatto: OFF-011 con atteso={"xsd_valido": false} era SUPERATO
    (false voleva dire «salta il controllo», non «mi aspetto che NON validi»)."""
    pytest.importorskip("lxml")
    caso = _caso("OFF-011")
    caso["passi"][0]["atteso"] = {"xsd_valido": False}
    assert Motore().esegui(caso).stato == "FALLITO"


def test_xsd_valido_false_su_richiesta_davvero_non_valida_e_superato(monkeypatch):
    """Gruppo di controllo: xsd_valido:false si soddisfa con una richiesta che NON valida."""
    pytest.importorskip("lxml")
    from varco.ricetta import xml_sac

    originale = xml_sac.richiesta_invio

    def senza_pincode(*a, **k):
        el = originale(*a, **k)
        el.remove(el[0])  # pinCode è obbligatorio
        return el

    monkeypatch.setattr(xml_sac, "richiesta_invio", senza_pincode)
    caso = _caso("OFF-011")
    caso["passi"][0]["atteso"] = {"xsd_valido": False}
    assert Motore().esegui(caso).stato == "SUPERATO"


def test_pie101_xsd_valido_false_e_fallito():
    """5-sar-piemonte.md punto 2, controesempio esatto su PIE-101: richiesta valida, xsd_valido:false → SUPERATO."""
    pytest.importorskip("lxml")
    caso = _caso("PIE-101")
    caso["passi"][0]["atteso"]["xsd_valido"] = False
    assert Motore().esegui(caso).stato == "FALLITO"


def test_pie110_senza_xsd_a2f_e_saltato_non_superato(monkeypatch):
    """5-sar-piemonte.md punto 2: PIE-110 con xsd_valido:false e senza XSD A2F era SUPERATO. Deve essere SALTATO."""
    pytest.importorskip("lxml")
    monkeypatch.delenv("VARCO_XSD_A2F", raising=False)
    caso = _caso("PIE-110")
    caso["passi"][0]["atteso"]["xsd_valido"] = False
    assert Motore().esegui(caso).stato == "SALTATO"


@pytest.mark.skipif(not XSD_A2F.exists(), reason="kit A2F non scaricato (strumenti/scarica_specifiche.py --gruppi piemonte)")
def test_pie110_con_xsd_a2f_xsd_valido_false_e_fallito():
    caso = _caso("PIE-110")
    caso["passi"][0]["atteso"]["xsd_valido"] = False
    assert Motore(xsd_a2f=XSD_A2F).esegui(caso).stato == "FALLITO"


@pytest.mark.parametrize("caso_id,cartella", [("FVG-101", "xsd_fvg")])
def test_regionali_senza_xsd_e_xsd_valido_false_saltato(caso_id, cartella, monkeypatch):
    """Schema regionale assente → SALTATO, qualunque sia xsd_valido."""
    pytest.importorskip("lxml")
    monkeypatch.delenv("VARCO_XSD_FVG", raising=False)
    caso = _caso(caso_id)
    caso["passi"][0]["atteso"]["xsd_valido"] = False
    assert Motore().esegui(caso).stato == "SALTATO"


@pytest.mark.parametrize(
    "caso_id,chiave,xsd",
    [("FVG-101", "xsd_fvg", XSD_FVG)],
)
@pytest.mark.schemi_hl7
def test_regionali_con_xsd_e_xsd_valido_false_fallito(caso_id, chiave, xsd):
    pytest.importorskip("lxml")
    if xsd is not None and not xsd.exists():
        pytest.skip("specifiche regionali non scaricate")
    caso = _caso(caso_id)
    caso["passi"][0]["atteso"]["xsd_valido"] = False
    assert Motore(**({chiave: xsd} if chiave else {})).esegui(caso).stato == "FALLITO"


@pytest.mark.parametrize(
    "caso_id,atteso",
    [
        ("OFF-001", {"codice": ["0000"], "codic": ["0000"]}),  # chiave sconosciuta (refuso): non si verifica, quindi FALLITO
        ("OFF-001", {"codice": ["0000"], "rifiuto_locale": True}),  # rifiuto e risposta insieme: incoerente
        ("OFF-001", {"fault_contiene": "x", "codice": ["0000"]}),  # fault e codice insieme: incoerente
        ("OFF-001", {"errore_bloccante": True, "nessun_bloccante": True}),
        ("OFF-011", {"tag": {"nre": ""}, "testi": {"nre": "X"}}),  # 'testi' prima era ignorato fuori dal Piemonte
        ("OFF-011", {"attributi": {"@inesistente": "x"}}),  # 'attributi' prima era ignorato su codifica_invio
        ("OFF-011", {"tag": {"nre": ""}, "tag_assenti": ["nre"]}),
        ("PIE-301", {"assenti": ["X-Qualcosa"]}),  # header su una lettura A2F: non applicabile
        ("PIE-301", {"ok": True, "errore_bloccante": True}),
        ("FSE-001", {"valido": True, "esito": "SYNTAX_ERROR"}),
        ("FSE-001", {"esito": "OK", "senza_errori": True, "errori_contengono": ["x"]}),
    ],
)
def test_aspettative_sconosciute_o_incoerenti_fallito(caso_id, atteso):
    caso = _caso(caso_id)
    caso["passi"][0]["atteso"] = atteso

    class Validatore:
        def valida(self, xml):
            raise AssertionError("un caso incoerente non si esegue nemmeno")

    assert Motore(validatore_fse=Validatore()).esegui(caso).stato == "FALLITO"


def test_rifiuto_locale_piemonte_con_altre_aspettative_fallito():
    """Un rifiuto locale non chiude il confronto nemmeno nel Piemonte."""
    rifiuti = [c for c in mod_motore.carica_casi("piemonte") if c["passi"][0]["atteso"].get("rifiuto_locale")]
    assert rifiuti
    for caso in rifiuti:
        assert Motore().esegui(caso).stato == "SUPERATO", caso["id"]  # gruppo di controllo
        guasto = copy.deepcopy(caso)
        guasto["passi"][0]["atteso"]["campi"] = {"code_challenge": "x"}
        assert Motore().esegui(guasto).stato == "FALLITO", caso["id"]


def test_chiavi_note_del_motore_uguali_allo_schema():
    """Il motore conosce esattamente le aspettative dello schema: né una in meno (verde finto),
    né una in più (dialetto del Python)."""
    schema = json.loads((CONF / "schema" / "caso.schema.json").read_text(encoding="utf-8"))
    for nome, chiavi in mod_motore.CHIAVI_ATTESO.items():
        assert chiavi == set(schema["$defs"][nome]["properties"]), nome


# ------------------------------------------------------------------ punto 2: una suite vuota non è verde


def test_cartella_inesistente_non_e_verde(capsys):
    """Controesempio esatto: Totale 0, exit 0."""
    assert esegui_suite(["--famiglia", "offline", "--casi", "/cartella-che-non-esiste"]) != 0
    assert "Totale 0" not in capsys.readouterr().out


def test_id_sconosciuto_non_e_verde(capsys):
    """Controesempio esatto: --solo OFF-999 → Totale 0, exit 0."""
    assert esegui_suite(["--famiglia", "offline", "--solo", "OFF-999"]) != 0
    io = capsys.readouterr()
    assert "OFF-999" in io.err


def test_id_di_un_altra_famiglia_non_e_verde():
    assert esegui_suite(["--famiglia", "offline", "--solo", "OFF-001", "FSE-001"]) != 0


def test_suite_senza_casi_non_e_verde(tmp_path):
    (tmp_path / "casi").mkdir()
    assert esegui_suite(["--famiglia", "offline", "--casi", str(tmp_path)]) != 0
    # una cartella con casi solo di un'altra famiglia: 0 casi eseguiti
    (tmp_path / "casi" / "FSE-001.json").write_text((CONF / "casi" / "FSE-001.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert esegui_suite(["--famiglia", "offline", "--casi", str(tmp_path)]) != 0


def test_suite_tutta_saltata_non_e_verde(tmp_path, monkeypatch):
    """0 casi giudicati (tutti SALTATO) non è un verde: non è stato verificato niente."""
    pytest.importorskip("lxml")
    monkeypatch.delenv("VARCO_XSD_FVG", raising=False)
    (tmp_path / "casi").mkdir()
    (tmp_path / "casi" / "FVG-101.json").write_text((CONF / "casi" / "FVG-101.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert esegui_suite(["--famiglia", "fvg", "--casi", str(tmp_path)]) != 0


def test_solo_un_caso_esistente_resta_verde():
    """Gruppo di controllo."""
    assert esegui_suite(["--famiglia", "offline", "--solo", "OFF-001"]) == 0


# ------------------------------------------------------------------ punto 6: --adattatore non si ignora in silenzio


def test_adattatore_con_famiglia_offline_rifiutato(capsys):
    """Controesempio esatto: 21 verdi con un adattatore inesistente."""
    with pytest.raises(SystemExit) as e:
        esegui_suite(["--famiglia", "offline", "--adattatore", "modulo_inesistente:fallisce"])
    assert e.value.code != 0
    io = capsys.readouterr()
    assert "--adattatore" in io.err and "superati" not in io.out


@pytest.mark.parametrize("famiglia", ["fse", "fvg", "piemonte", "umbria", "tutte"])
def test_adattatore_con_altre_famiglie_rifiutato(famiglia):
    with pytest.raises(SystemExit) as e:
        esegui_suite(["--famiglia", famiglia, "--adattatore", "modulo_inesistente:fallisce"])
    assert e.value.code != 0


@pytest.mark.parametrize("famiglia", ["offline", "fvg", "piemonte", "umbria", "online", "tutte"])
def test_adattatore_fse_fuori_dalla_famiglia_fse_rifiutato(famiglia):
    with pytest.raises(SystemExit) as e:
        esegui_suite(["--famiglia", famiglia, "--adattatore-fse", "modulo_inesistente:fallisce"])
    assert e.value.code != 0


# ------------------------------------------------------------------ punto 9: test sull'ordine XSD non autoreferenziale


@pytest.mark.parametrize("costante", ["_ORDINE_TESTATA", "_ORDINE_RIGA", "_ORDINE_ESTERI"])
def test_ordine_xsd_boccia_la_costante_rovesciata(costante, monkeypatch):
    """Controesempio esatto: con la costante del produttore rovesciata (patch.object) il vecchio
    test_ricetta.py::test_ordine_tag_segue_xsd passava, perché confrontava la costante con sé stessa."""
    pytest.importorskip("lxml")
    import importlib.util

    from varco.ricetta import xml_sac

    spec = importlib.util.spec_from_file_location("test_ricetta_isolato", CONF.parent / "tests" / "unit" / "test_ricetta.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    modulo.test_ordine_tag_segue_xsd()  # gruppo di controllo: con il codec vero passa
    monkeypatch.setattr(xml_sac, costante, tuple(reversed(getattr(xml_sac, costante))))
    with pytest.raises(AssertionError):
        modulo.test_ordine_tag_segue_xsd()
