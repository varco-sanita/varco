# SPDX-License-Identifier: EUPL-1.2
"""Rinomina kit-mmg -> Varco: le variabili VARCO_* sono quelle buone; le KITMMG_* valgono ancora
per una versione, con avviso di deprecazione (varco.ambiente)."""

import warnings

import pytest

from varco.ambiente import NOMI_CON_ALIAS, VariabileDeprecata, leggi
from varco.credenziali import Credenziali


def test_nuova_variabile_senza_avviso():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert leggi("VARCO_KIT_MEF", ambiente={"VARCO_KIT_MEF": "/nuovo"}) == "/nuovo"


@pytest.mark.parametrize("radice", sorted(NOMI_CON_ALIAS))
def test_vecchia_variabile_vale_con_avviso(radice):
    with pytest.warns(VariabileDeprecata, match=f"KITMMG_{radice} è deprecata.*usa VARCO_{radice}"):
        assert leggi(f"VARCO_{radice}", ambiente={f"KITMMG_{radice}": "vecchio"}) == "vecchio"


def test_vince_la_nuova_e_niente_avviso():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        env = {"VARCO_XSD_FVG": "nuovo", "KITMMG_XSD_FVG": "vecchio"}
        assert leggi("VARCO_XSD_FVG", ambiente=env) == "nuovo"


def test_avviso_visibile_senza_opzioni():
    # FutureWarning: Python la mostra anche fuori da __main__, a differenza di DeprecationWarning
    assert issubclass(VariabileDeprecata, FutureWarning)


def test_nessun_alias_per_le_variabili_nate_con_varco():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert leggi("VARCO_CDA_XSD", "d", ambiente={"KITMMG_CDA_XSD": "x"}) == "d"


def test_credenziali_da_env_col_vecchio_prefisso():
    vecchie = {"KITMMG_UTENTE": "U", "KITMMG_PASSWORD": "P", "KITMMG_PINCODE": "1", "KITMMG_CF_MEDICO": "CF"}
    with pytest.warns(VariabileDeprecata):
        c = Credenziali.da_env(ambiente=vecchie)
    assert (c.utente, c.password, c.pincode, c.cf_medico) == ("U", "P", "1", "CF")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        c = Credenziali.da_env(ambiente={"VARCO_UTENTE": "N", "VARCO_PASSWORD": "P", "VARCO_PINCODE": "2"})
    assert c.utente == "N" and c.cf_medico is None


def test_kit_mef_e_conformita_leggono_l_alias(monkeypatch, tmp_path):
    from varco.conformita.motore import cartella_conformita
    from varco.errori import ConfigurazioneNonValida
    from varco.kit_mef import cartella_kit

    monkeypatch.delenv("VARCO_CONFORMITA", raising=False)
    monkeypatch.setenv("KITMMG_CONFORMITA", str(tmp_path))
    with pytest.warns(VariabileDeprecata):
        assert cartella_conformita() == tmp_path
    monkeypatch.delenv("VARCO_KIT_MEF", raising=False)
    monkeypatch.setenv("KITMMG_KIT_MEF", str(tmp_path / "manca"))
    with pytest.warns(VariabileDeprecata), pytest.raises(ConfigurazioneNonValida):
        cartella_kit()
