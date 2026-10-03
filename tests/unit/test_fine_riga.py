# SPDX-License-Identifier: EUPL-1.2
"""CI Windows 03/10/2026: senza .gitattributes il checkout con core.autocrlf convertiva i PDF di prove/
(LF -> CRLF) e ne spostava gli offset: pyhanko non trovava più la xref. Ogni file versionato deve
uscire dal checkout byte per byte."""
import shutil
import subprocess
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("git") is None or not (RADICE / ".git").exists(), reason="serve un checkout git")
def test_nessun_file_versionato_subisce_conversioni_di_fine_riga():
    file = subprocess.run(["git", "ls-files", "-z"], cwd=RADICE, capture_output=True, check=True).stdout
    nomi = [n for n in file.decode("utf-8").split("\0") if n]
    assert nomi
    pdf = [n for n in nomi if n.endswith(".pdf")]
    assert pdf, "il controllo deve coprire almeno i PDF di prove/"
    attr = subprocess.run(["git", "check-attr", "--stdin", "-z", "text", "eol"], cwd=RADICE,
                          input="\0".join(nomi).encode("utf-8"), capture_output=True, check=True).stdout
    campi = attr.decode("utf-8").split("\0")
    valori = {}
    for i in range(0, len(campi) - 2, 3):
        valori.setdefault(campi[i], {})[campi[i + 1]] = campi[i + 2]
    convertiti = [n for n, a in valori.items() if a.get("text") != "unset" or a.get("eol") != "unspecified"]
    assert not convertiti, f"file soggetti a conversione di fine riga: {convertiti[:10]}"
