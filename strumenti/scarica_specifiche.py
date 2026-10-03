#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
"""Scarica il materiale di terzi dalle fonti UFFICIALI e ne verifica l'integrità.

Il repository del kit non redistribuisce né le specifiche e il kit di sviluppo del MEF
(che contiene le utenze di test) né il codice AGPL-3.0 del gateway FSE: li prende da
qui, alla versione esatta con cui il kit è stato scritto e collaudato.

- file: sha256 fissato in strumenti/fonti_specifiche.json. Se la fonte è cambiata, il
  file scaricato resta accanto con suffisso `.non-verificato`, lo script esce con 1 e
  non sovrascrive niente: va controllato a mano e il manifesto aggiornato.
- archivi scompattati (`scompatta_in`): con --solo-verifica ogni file estratto si confronta,
  byte per byte via sha256, con il suo membro nello ZIP (che a sua volta ha l'hash fissato).
  Test e strumenti leggono gli estratti, non lo ZIP: un estratto alterato o mancante non è
  verificato.
- repository git: clone superficiale del commit fissato, poi controllo che HEAD sia
  proprio quel commit.

Uso:
    python strumenti/scarica_specifiche.py                     # gruppi mef, fse-validatore e cda-xsd
    python strumenti/scarica_specifiche.py --gruppi cda-xsd    # solo gli schemi HL7 (XSD CDA e schematron PSS) per i test
    python strumenti/scarica_specifiche.py --gruppi mef
    python strumenti/scarica_specifiche.py --gruppi sist       # specifiche SIST della Regione Puglia
    python strumenti/scarica_specifiche.py --gruppi fvg        # specifiche SAR della Regione FVG (Insiel)
    python strumenti/scarica_specifiche.py --gruppi piemonte   # SIRPED della Regione Piemonte (CSI) e kit A2F del Sistema TS
    python strumenti/scarica_specifiche.py --tutto             # anche fse-riferimento, sist, fvg e piemonte
    python strumenti/scarica_specifiche.py --solo-verifica     # nessuna rete: controlla cosa c'è
    python strumenti/scarica_specifiche.py --destinazione /tmp/spec

Solo libreria standard, più il comando `git` per i repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
MANIFESTO = Path(__file__).resolve().parent / "fonti_specifiche.json"
# Cartella di destinazione predefinita, sotto la radice del repository. La libreria legge questa riga
# (varco.fse.validazione) per trovare gli schemi HL7 scaricati: va lasciata in questa forma.
CARTELLA_PREDEFINITA = "specifiche"
GRUPPI_DEFAULT = ("mef", "fse-validatore", "cda-xsd")
HOST_UFFICIALI = ("sistemats1.sanita.finanze.it", "raw.githubusercontent.com", "github.com", "sist.sanita.puglia.it",
                  "medicinrete.insiel.it", "servizi.regione.piemonte.it", "www.csipiemonte.it")


def sha256(percorso: Path) -> str:
    h = hashlib.sha256()
    with percorso.open("rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def _sha256_membro(z: zipfile.ZipFile, nome: str) -> str:
    h = hashlib.sha256()
    with z.open(nome) as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def _host_ufficiale(url: str) -> bool:
    from urllib.parse import urlparse

    p = urlparse(url)
    return p.scheme == "https" and (p.hostname or "") in HOST_UFFICIALI


class _SoloRedirectUfficiali(urllib.request.HTTPRedirectHandler):
    """Un redirect si segue solo se porta ancora a un host ufficiale in HTTPS
    (raw.githubusercontent.com e il portale MEF ne fanno; altrove no)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _host_ufficiale(newurl):
            raise SystemExit(f"Redirect {code} verso un URL non ufficiale o non HTTPS, rifiutato: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _scarica(url: str, dest: Path) -> None:
    if not _host_ufficiale(url):
        raise SystemExit(f"URL non ufficiale o non HTTPS, rifiutato: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "varco/scarica_specifiche"})
    apri = urllib.request.build_opener(_SoloRedirectUfficiali()).open
    with apri(req, timeout=120) as r, dest.open("wb") as out:
        if not _host_ufficiale(r.geturl()):
            raise SystemExit(f"Contenuto servito da un URL non ufficiale, rifiutato: {r.geturl()}")
        shutil.copyfileobj(r, out)


def _sotto(base: Path, relativo: str) -> Path:
    p = (base / relativo).resolve()
    if base.resolve() not in p.parents and p != base.resolve():
        raise SystemExit(f"Destinazione fuori dalla cartella: {relativo}")
    return p


def tratta_file(voce: dict, base: Path, solo_verifica: bool) -> str:
    dest = _sotto(base, voce["destinazione"])
    atteso = voce["sha256"]
    if dest.exists() and sha256(dest) == atteso:
        esito = "ok (già presente)"
    elif solo_verifica:
        return "MANCA" if not dest.exists() else "HASH DIVERSO"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _scarica(voce["url"], tmp_path)
            trovato = sha256(tmp_path)
            if trovato != atteso:
                parcheggio = dest.with_name(dest.name + ".non-verificato")
                tmp_path.replace(parcheggio)
                return f"HASH DIVERSO: atteso {atteso}, scaricato {trovato} (lasciato in {parcheggio.name})"
            tmp_path.replace(dest)
            esito = "ok (scaricato e verificato)"
        finally:
            tmp_path.unlink(missing_ok=True)
    cartella = voce.get("scompatta_in")
    if cartella:
        target = _sotto(base, cartella)
        with zipfile.ZipFile(dest) as z:
            for nome in z.namelist():
                _sotto(target, nome)  # niente percorsi che escono dalla cartella
            if solo_verifica:
                membri = [n for n in z.namelist() if not n.endswith("/")]
                mancanti = [n for n in membri if not (target / n).is_file()]
                if mancanti:
                    return f"SCOMPATTATO A METÀ: lo zip è verificato ma in {cartella}/ mancano {len(mancanti)} file"
                alterati = [n for n in membri if sha256(target / n) != _sha256_membro(z, n)]
                if alterati:
                    return (f"ESTRATTI ALTERATI: lo zip è verificato ma in {cartella}/ {len(alterati)} file non "
                            f"corrispondono al loro membro nello zip (es. {alterati[0]})")
                esito += f"; {len(membri)} estratti verificati sullo zip"
            else:
                z.extractall(target)
        esito += f"; scompattato in {cartella}/"
    return esito


def _git(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def tratta_git(voce: dict, base: Path, solo_verifica: bool) -> str:
    dest = _sotto(base, voce["destinazione"])
    commit = voce["commit"]
    if not _host_ufficiale(voce["repository"]):
        return f"repository non ufficiale, rifiutato: {voce['repository']}"
    if (dest / ".git").exists():
        try:
            head = _git("rev-parse", "HEAD", cwd=dest)
            origine = _git("remote", "get-url", "origin", cwd=dest)
        except RuntimeError as e:
            return f"ERRORE: {e}"
        if origine != voce["repository"]:
            return f"ORIGINE DIVERSA: {origine} (attesa {voce['repository']})"
        if head == commit:
            sporco = _git("status", "--porcelain", "--untracked-files=no", cwd=dest)
            return "ok (commit giusto)" if not sporco else "COMMIT GIUSTO MA FILE MODIFICATI"
        if solo_verifica:
            return f"COMMIT DIVERSO: {head}"
    elif solo_verifica:
        return "MANCA"
    elif dest.exists() and any(dest.iterdir()):
        return "ERRORE: la cartella esiste e non è un repository git (spostarla a mano)"
    if shutil.which("git") is None:
        return "ERRORE: serve il comando git"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        if not (dest / ".git").exists():
            _git("init", "-q", cwd=dest)
            _git("remote", "add", "origin", voce["repository"], cwd=dest)
        if voce.get("solo_cartelle"):
            _git("sparse-checkout", "set", *voce["solo_cartelle"], cwd=dest)
        _git("fetch", "-q", "--depth", "1", "origin", commit, cwd=dest)
        _git("checkout", "-q", "--detach", commit, cwd=dest)
        head = _git("rev-parse", "HEAD", cwd=dest)
    except RuntimeError as e:
        return f"ERRORE: {e}"
    return "ok (scaricato, commit verificato)" if head == commit else f"COMMIT DIVERSO: {head}"


def main(argv: list[str] | None = None) -> int:
    manifesto = json.loads(MANIFESTO.read_text(encoding="utf-8"))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gruppi", nargs="+", choices=sorted(manifesto["gruppi"]), default=list(GRUPPI_DEFAULT))
    ap.add_argument("--tutto", action="store_true", help="tutti i gruppi")
    ap.add_argument("--solo-verifica", action="store_true", help="nessuna rete: verifica i file già presenti")
    ap.add_argument("--destinazione", default=str(RADICE / CARTELLA_PREDEFINITA))
    args = ap.parse_args(argv)
    gruppi = set(manifesto["gruppi"]) if args.tutto else set(args.gruppi)
    base = Path(args.destinazione)
    base.mkdir(parents=True, exist_ok=True)

    errori = 0
    for voce in manifesto["file"]:
        if voce["gruppo"] in gruppi:
            esito = tratta_file(voce, base, args.solo_verifica)
            errori += not esito.startswith("ok")
            print(f"[{voce['gruppo']}] {voce['destinazione']}: {esito}")
    for voce in manifesto["git"]:
        if voce["gruppo"] in gruppi:
            esito = tratta_git(voce, base, args.solo_verifica)
            errori += not esito.startswith("ok")
            print(f"[{voce['gruppo']}] {voce['destinazione']}@{voce['commit'][:12]}: {esito}")
    print(f"\n{'TUTTO VERIFICATO' if not errori else f'{errori} VOCI NON VERIFICATE'} (gruppi: {', '.join(sorted(gruppi))})")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.exit(main())
