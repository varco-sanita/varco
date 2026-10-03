# SPDX-License-Identifier: EUPL-1.2
"""Gli schemi HL7 (XSD del CDA R2, schematron del PSS) non stanno nel repository: si scaricano
dalla fonte ufficiale (gruppo cda-xsd di strumenti/fonti_specifiche.json) e la libreria li
cerca lì o nelle cartelle di $VARCO_CDA_XSD e $VARCO_SCHEMATRON (docs/TERZE_PARTI.md)."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from varco.fse import validazione as v

RADICE = Path(__file__).resolve().parents[2]
MANIFESTO = json.loads((RADICE / "strumenti" / "fonti_specifiche.json").read_text(encoding="utf-8"))
COMMIT = "141be7f0a1c75f4a0a07f979da48a83471d07ef4"
SCHEMATRON = "schematron_PSS_v4.0.sch"
NOMI = set(v.XSD_CDA) | {SCHEMATRON}


def test_nessuna_copia_degli_schemi_hl7_nel_repository():
    cartelle = [RADICE / d for d in ("src", "tests", "conformita", "strumenti", "docs")]
    copie = [str(f) for d in cartelle for f in d.rglob("*") if f.is_file() and f.name in NOMI]
    assert copie == []
    pyproject = (RADICE / "pyproject.toml").read_text(encoding="utf-8")
    assert ".xsd" not in pyproject.split('"varco.fse"', 1)[1].split("\n", 1)[0]
    assert ".sch" not in pyproject.split('"varco.fse"', 1)[1].split("\n", 1)[0]


def test_manifesto_ha_tutti_gli_schemi_dalla_fonte_a_un_commit_fissato():
    voci = {Path(f["destinazione"]).name: f for f in MANIFESTO["file"] if f["gruppo"] == "cda-xsd"}
    assert set(voci) == NOMI
    for nome, voce in voci.items():
        assert voce["url"].startswith(f"https://raw.githubusercontent.com/ministero-salute/it-fse-catalogs/{COMMIT}/"), nome
        assert len(voce["sha256"]) == 64, nome
    assert voci[SCHEMATRON]["url"].endswith(f"/schematron/{SCHEMATRON}")
    assert voci[SCHEMATRON]["destinazione"] == f"fse/schematron/{SCHEMATRON}"
    assert all(voci[n]["destinazione"] == f"fse/cda-xsd/{n}" for n in v.XSD_CDA)


def test_cda_xsd_tra_i_gruppi_di_default():
    spec = importlib.util.spec_from_file_location("scarica_specifiche", RADICE / "strumenti" / "scarica_specifiche.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "cda-xsd" in mod.GRUPPI_DEFAULT
    # la libreria trova la cartella di default leggendo questa costante dallo script
    assert v._cartella_scaricata("cda-xsd") == RADICE / mod.CARTELLA_PREDEFINITA / "fse" / "cda-xsd"


@pytest.mark.senza_schemi_hl7
def test_schemi_mancanti_messaggio_dice_come_scaricarli(tmp_path, monkeypatch):
    monkeypatch.setenv("VARCO_CDA_XSD", str(tmp_path / "vuota"))
    with pytest.raises(v.SchemiNonTrovati) as e:
        v._schema_cda()
    testo = str(e.value)
    assert "scarica_specifiche.py --gruppi cda-xsd" in testo and "$VARCO_CDA_XSD" in testo and "CDA.xsd" in testo
    monkeypatch.setenv("VARCO_SCHEMATRON", str(tmp_path / "vuota"))
    with pytest.raises(v.SchemiNonTrovati, match="VARCO_SCHEMATRON"):
        v._schematron(SCHEMATRON)
    assert v.schemi_locali_presenti() is False


@pytest.mark.senza_schemi_hl7
def test_senza_schemi_il_motore_non_sceglie_il_validatore_locale(tmp_path, monkeypatch):
    from varco.conformita.motore import validatore_fse_predefinito

    if not v.dipendenze_locali_presenti():
        pytest.skip("servono lxml e saxonche")
    monkeypatch.setenv("VARCO_CDA_XSD", str(tmp_path))
    assert validatore_fse_predefinito("locale") is None


@pytest.mark.senza_schemi_hl7
@pytest.mark.schemi_hl7
def test_un_solo_xsd_mancante_basta_a_fermare(tmp_path, monkeypatch):
    sorgente = v.cartella_xsd_cda()
    if not v.schemi_locali_presenti():
        pytest.skip("schemi HL7 non scaricati")
    for n in v.XSD_CDA:
        if n != "voc.xsd":
            shutil.copy(sorgente / n, tmp_path / n)
    monkeypatch.setenv("VARCO_CDA_XSD", str(tmp_path))
    with pytest.raises(v.SchemiNonTrovati, match=r": voc\.xsd\."):
        v._schema_cda()


@pytest.mark.schemi_hl7
def test_variabile_d_ambiente_su_una_copia_altrove(tmp_path, monkeypatch):
    """Gruppo di controllo: con gli schemi in una cartella qualunque, la validazione funziona."""
    if not v.dipendenze_locali_presenti():
        pytest.skip("servono lxml e saxonche")
    if not v.schemi_locali_presenti():
        pytest.skip("schemi HL7 non scaricati")
    xsd, sch = v.cartella_xsd_cda(), v.cartella_schematron()
    shutil.copytree(xsd, tmp_path / "x")
    shutil.copytree(sch, tmp_path / "s")
    monkeypatch.setenv("VARCO_CDA_XSD", str(tmp_path / "x"))
    monkeypatch.setenv("VARCO_SCHEMATRON", str(tmp_path / "s"))
    from varco.fse.esempi import pss_completo
    from varco.fse.cda_pss import genera_xml

    e = v.ValidatoreLocale().valida(genera_xml(pss_completo()))
    assert e.valido, e.errori
