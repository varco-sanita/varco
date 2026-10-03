# SPDX-License-Identifier: EUPL-1.2
"""La libreria EUPL resta separata dal codice AGPL-3.0 del Ministero (docs/TERZE_PARTI.md).

- ogni modulo Python di src/ è EUPL-1.2 e importa solo libreria standard e dipendenze dichiarate;
- nessun modulo carica classi Java o scrive percorsi di specifiche/: il validatore ufficiale
  si esegue solo come PROCESSO separato (strumenti/validatore-ufficiale/valida.sh). L'unica
  copia scaricata che la libreria legge sono gli schemi HL7 (non AGPL), nella cartella che le
  indica lo script di download (test_schemi_hl7.py);
- nessun file di src/ è copiato dai repository AGPL (controllo sugli hash, se le copie ci sono).
"""

import ast
import hashlib
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
SRC = RADICE / "src" / "varco"
PERMESSI = {"varco", "cryptography", "lxml", "saxonche", "pyhanko", "pypdf", "jsonschema", "referencing"}
REPO_AGPL = ["it-fse-gtw-validator", "it-fse-gtw-dispatcher"]


def _moduli():
    return sorted(SRC.rglob("*.py"))


def test_ogni_modulo_e_eupl():
    for p in _moduli():
        testa = "\n".join(p.read_text(encoding="utf-8").splitlines()[:3])
        assert "SPDX-License-Identifier: EUPL-1.2" in testa, p


def test_import_solo_stdlib_e_dipendenze_dichiarate():
    for p in _moduli():
        for nodo in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Import):
                nomi = [a.name for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0:
                nomi = [nodo.module or ""]
            else:
                continue
            for nome in nomi:
                radice = nome.split(".")[0]
                assert radice in sys.stdlib_module_names or radice in PERMESSI, f"{p}: import {nome}"


def test_nessun_aggancio_al_codice_agpl():
    vietati = ["specifiche/", "jpype", "py4j", ".jar", "it-fse-gtw-validator/target", "it-fse-gtw-dispatcher/target"]
    for p in _moduli():
        testo = p.read_text(encoding="utf-8")
        for v in vietati:
            assert v not in testo, f"{p}: {v}"
    # l'unico punto di contatto è un processo separato
    validazione = (SRC / "fse" / "validazione.py").read_text(encoding="utf-8")
    assert "subprocess.run" in validazione and "valida.sh" in validazione


def test_nessun_file_copiato_dai_repository_agpl():
    basi = [RADICE / "specifiche" / "fse" / r for r in REPO_AGPL]
    if not all((b / "LICENSE").exists() for b in basi):
        pytest.skip("copie dei repository AGPL non presenti (strumenti/scarica_specifiche.py)")
    for b in basi:
        assert "AFFERO" in (b / "LICENSE").read_text(encoding="utf-8")  # gruppo di controllo: sono davvero AGPL
    hash_agpl = {
        hashlib.sha256(f.read_bytes()).hexdigest()
        for b in basi
        for f in b.rglob("*")
        if f.is_file() and ".git" not in f.parts and "target" not in f.parts and f.stat().st_size > 0
    }
    nostri = [f for d in (SRC, RADICE / "conformita", RADICE / "strumenti") for f in d.rglob("*") if f.is_file() and "classi" not in f.parts]
    copiati = [str(f) for f in nostri if hashlib.sha256(f.read_bytes()).hexdigest() in hash_agpl]
    assert copiati == []
