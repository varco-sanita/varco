# SPDX-License-Identifier: EUPL-1.2
"""Mutation check della suite di conformità: un verde che non sa diventare rosso non prova niente.

Si prendono i casi REALI di ogni famiglia, si altera in memoria l'atteso di un passo in quattro
modi tipici e si esegue il motore:

  rifiuto     rifiuto locale invertito (atteso dove non c'è, tolto dove c'è)
  invertita   aspettativa invertita (xsd_valido, valido, ok, fault, bloccante)
  codice      codice/valore sbagliato (codice esito, tag, esito FSE, header, campo)
  inesistente errore atteso che non esiste (errore_codice, frammento d'errore, tag, campo)

Regola: se il caso originale è SUPERATO, ogni mutante deve essere FALLITO. Se l'originale è
SALTATO (manca uno schema, un validatore, le credenziali) il mutante non può essere SUPERATO.
Lo stesso controllo gira sull'esecutore Java dei casi FSE (validatore ufficiale) se il banco è pronto.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess

import pytest

from varco.conformita.esegui import contesto_offline
from varco import Credenziali
from varco.conformita.motore import (FAMIGLIE, OPERAZIONI_ONLINE, Motore, carica_casi, cartella_conformita,
                                       tipo_atteso)
from varco.fse.validazione import ValidatoreLocale, cartella_validatore_ufficiale, dipendenze_locali_presenti

CONF = cartella_conformita()
SPEC = CONF.parent / "specifiche"
XSD_SIST = SPEC / "sist" / "specifiche SIST 4.02.27" / "wsdl-pddasl" / "CVPService.xsd"
XSD_FVG = SPEC / "fvg" / "wsdl" / "sar"
XSD_A2F = SPEC / "piemonte" / "a2f" / "Kit per lo sviluppo - A2F SistemaTS - ver. 20250902" / "wsdl"
OPENAPI_UMBRIA = SPEC / "umbria" / "openapi" / "sar-open-api-prescrittore.yaml"

INESISTENTE = "ERRORE-INESISTENTE-MUTANTE"
CREDENZIALI_SPECCHIO = Credenziali("specchio", "nessuna-rete", "0000000000", "PROVAX00X00X000Y")


def _altro_codice(codici: list[str]) -> list[str]:
    return ["4242"] if "4242" not in codici else ["4243"]


def mutazioni(op: str, atteso: dict) -> list[tuple[str, dict]]:
    """(nome, atteso alterato). Ogni alterazione deve rendere il passo NON rispettato."""
    tipo = tipo_atteso(op)
    a = copy.deepcopy(atteso)
    out: list[tuple[str, dict]] = []
    rifiuto = bool(a.get("rifiuto_locale"))

    # 1. rifiuto locale invertito
    if tipo != "attesoFse":
        out.append(("rifiuto", {} if rifiuto else {"rifiuto_locale": True}))

    if tipo == "attesoSac":
        if rifiuto:
            out.append(("invertita", {"codice": ["0000"]}))
            out.append(("codice", {"codice": ["4242"]}))
        elif "fault_contiene" in a:
            out.append(("invertita", {}))  # nessun fault atteso: arriva un fault
            out.append(("codice", {"fault_contiene": INESISTENTE}))
        else:
            out.append(("invertita", {"fault_contiene": "Fault"}))
            m = copy.deepcopy(a)
            if "codice" in m:
                m["codice"] = _altro_codice(m["codice"])
            elif "nre" in m:
                m["nre"] = "999ZZ9999999999"
            else:
                m["codice"] = ["4242"]
            out.append(("codice", m))
            out.append(("inesistente", {**a, "errore_codice": INESISTENTE}))
    elif tipo == "attesoCodifica":
        if rifiuto:
            out.append(("invertita", {"xsd_valido": True}))
            out.append(("codice", {"tag": {"pinCode": "MUTATO"}}))
        else:
            out.append(("invertita", {**a, "xsd_valido": not a.get("xsd_valido", True)}))
            m = copy.deepcopy(a)
            for chiave in ("tag", "attributi", "testi"):
                if m.get(chiave):
                    primo = next(iter(m[chiave]))
                    m[chiave][primo] = m[chiave][primo] + "-MUTATO"
                    break
            else:
                m["tag"] = {"pinCode": "MUTATO"}
            out.append(("codice", m))
            out.append(("inesistente", {**a, "tag": {**a.get("tag", {}), "TagInesistenteMutante": "x"}}))
    elif tipo == "attesoFse":
        esiti = a["esito"] if isinstance(a.get("esito"), list) else [a["esito"]] if "esito" in a else []
        if "valido" in a:
            out.append(("invertita", {**a, "valido": not a["valido"]}))
        else:  # esiti validi → atteso non valido (senza vocabolari: SYNTAX), esiti di errore → atteso valido
            validi = all(e in ("OK", "SEMANTIC_WARNING") for e in esiti)
            inv = {k: v for k, v in a.items() if k not in ("errori_contengono", "senza_errori")}
            inv["esito"] = "SYNTAX_ERROR" if validi else "OK"
            out.append(("invertita", inv))
        out.append(("codice", {**a, "esito": "SYNTAX_ERROR" if "SEMANTIC_ERROR" in esiti else "SEMANTIC_ERROR"}))
        m = {k: v for k, v in a.items() if k != "senza_errori"}
        m["errori_contengono"] = list(a.get("errori_contengono", [])) + [INESISTENTE]
        out.append(("inesistente", m))
    elif tipo == "attesoPiemonte":
        if rifiuto:
            out.append(("invertita", {"campi": {}}))
            out.append(("codice", {"campi": {"campo_mutato": "x"}}))
        else:
            if "ok" in a:
                out.append(("invertita", {**a, "ok": not a["ok"]}))
            elif any(isinstance(v, bool) for v in a.get("campi", {}).values()):
                campi = {k: (not v if isinstance(v, bool) else v) for k, v in a["campi"].items()}
                out.append(("invertita", {**a, "campi": campi}))
            elif a.get("assenti"):
                out.append(("invertita", {**a, "assenti": [], "intestazioni": {**a.get("intestazioni", {}),
                                                                                a["assenti"][0]: "presente"}}))
            m = copy.deepcopy(a)
            if "codice" in m:
                m["codice"] = _altro_codice(m["codice"])
            elif m.get("intestazioni"):
                primo = next(iter(m["intestazioni"]))
                m["intestazioni"][primo] = str(m["intestazioni"][primo]) + "-MUTATO"
            elif m.get("campi"):
                primo = next(iter(m["campi"]))
                m["campi"][primo] = "VALORE-MUTATO"
            else:
                m["campi"] = {"campo_mutato": "x"}
            out.append(("codice", m))
            if op == "leggi_piemonte_a2f":
                out.append(("inesistente", {**a, "errore_codice": INESISTENTE}))
            elif op == "intestazioni_piemonte":
                out.append(("inesistente", {**a, "intestazioni": {**a.get("intestazioni", {}), "X-Inesistente": "x"}}))
            else:
                out.append(("inesistente", {**a, "campi": {**a.get("campi", {}), "campo_inesistente": "x"}}))
    return out


def mutanti(caso: dict):
    """(descrizione, caso alterato) per ogni passo con un atteso e ogni mutazione."""
    for i, passo in enumerate(caso["passi"]):
        if "atteso" not in passo:
            continue
        for nome, atteso in mutazioni(passo["operazione"], passo["atteso"]):
            m = copy.deepcopy(caso)
            m["passi"][i]["atteso"] = atteso
            yield f"{caso['id']} passo {i} {nome}: {json.dumps(atteso, ensure_ascii=False)[:120]}", m


# ------------------------------------------------------------------ specchio per i casi online
#
# I casi online chiamano il SAC di test: qui non c'è rete. Prima il motore non aveva adattatore e
# credenziali, tutti i casi online erano SALTATO e il confronto online non si esercitava mai: un
# `verifica` sempre verde restava inosservato (revisione esterna giro 2, 6-conformita N4).
# Lo specchio NON prova un'implementazione: dà, passo per passo, l'esito che rispetta l'atteso
# ORIGINALE del caso. Così l'originale è SUPERATO e ogni mutante, confrontato con lo stesso esito,
# deve risultare FALLITO: si prova il confronto.

NRE_SPECCHIO = "1300A4019294833"


def _risolvi(v, ctx: dict):
    if isinstance(v, str):
        return re.sub(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}", lambda m: str(ctx.get(m.group(1), m.group(0))), v)
    if isinstance(v, list):
        return [_risolvi(x, ctx) for x in v]
    if isinstance(v, dict):
        return {k: _risolvi(x, ctx) for k, x in v.items()}
    return v


def esito_specchio(atteso: dict, ctx: dict):
    """L'esito (forma degli esiti del kit) che rispetta `atteso`; solleva per fault e rifiuto locale."""
    from types import SimpleNamespace

    from varco import ErroreSOAP
    from varco.errori import RicettaNonValida
    from varco.ricetta.modello import Messaggio

    a = _risolvi(atteso, ctx)
    if a.get("rifiuto_locale"):
        raise RicettaNonValida(["rifiuto locale dello specchio"])
    if "fault_contiene" in a:
        raise ErroreSOAP("soap:Server", f"specchio: {a['fault_contiene']}", 500)
    gravita = "W" if a.get("nessun_bloccante") else "E"
    messaggi = [Messaggio(a["errore_codice"], "errore dello specchio", "0", gravita)] if "errore_codice" in a else []
    messaggi += [Messaggio(c, "avviso dello specchio", "0", "W") for c in a.get("avvisi_codici", [])]
    e = SimpleNamespace(codice=(a.get("codice") or ["0000"])[0], messaggi=messaggi,
                        comunicazioni=[SimpleNamespace(codice=c) for c in a.get("comunicazioni_presenti", [])],
                        nre=a.get("nre", NRE_SPECCHIO))
    for campo in a.get("presenti", []):
        setattr(e, campo, getattr(e, campo, None) or "presente")
    if "stato_processo" in a:
        e.stato_processo = a["stato_processo"]
    if "righe" in a:
        e.righe = [{}] * a["righe"]
    if a.get("campi"):
        e.testata = dict(a["campi"])
    if any(k in a for k in ("ricette_numero", "ricette_almeno", "ricette_contengono")):
        ricette = list(dict.fromkeys(a.get("ricette_contengono", [])))
        n = a.get("ricette_numero", max(len(ricette), a.get("ricette_almeno", 0)))
        ricette += [f"1300A40192{i:05d}" for i in range(n - len(ricette))]
        e.ricette = [SimpleNamespace(nre=x) for x in ricette]
    if a.get("pdf_promemoria"):
        e.pdf_promemoria = b"%PDF-specchio"
    return e


class Specchio:
    """Adattatore online: restituisce, nell'ordine dei passi, gli esiti dell'atteso ORIGINALE."""

    def __init__(self, caso: dict, contesto: dict):
        self.coda = [p.get("atteso", {}) for p in caso["passi"] if p["operazione"] in OPERAZIONI_ONLINE]
        self.ctx = dict(contesto) | {"nre": NRE_SPECCHIO}

    def __call__(self, credenziali, valida_localmente=True):
        return self

    def _prossimo(self, *a, **k):
        return esito_specchio(self.coda.pop(0) if self.coda else {}, self.ctx)

    invia = visualizza = annulla = interroga_nre_utilizzati = _prossimo


def _motore() -> Motore:
    return Motore(contesto=contesto_offline(),
                  validatore_fse=ValidatoreLocale() if dipendenze_locali_presenti() else None,
                  xsd_sist=XSD_SIST if XSD_SIST.exists() else None,
                  xsd_fvg=XSD_FVG if XSD_FVG.exists() else None,
                  xsd_a2f=XSD_A2F if XSD_A2F.exists() else None,
                  openapi_umbria=OPENAPI_UMBRIA if OPENAPI_UMBRIA.exists() else None)


def controlla_mutanti(famiglia: str, motore: Motore) -> tuple[list[str], int, set[str]]:
    """(mutanti giudicati male, mutanti giudicati, tipi di mutazione giudicati). Per i casi online
    il motore riceve lo specchio dell'atteso originale e credenziali finte (nessuna rete)."""
    sbagliati, giudicati, tipi = [], 0, set()
    for caso in carica_casi(famiglia):
        if caso.get("famiglia") == "online":
            motore.credenziali = motore.credenziali_sostituto = CREDENZIALI_SPECCHIO
        for nome, c in [("originale", caso)] + list(mutanti(caso)):
            if caso.get("famiglia") == "online":
                motore.adattatore = Specchio(caso, motore.contesto_base)  # sempre l'atteso ORIGINALE
            stato = motore.esegui(c).stato
            if nome == "originale":
                originale = stato
                assert originale in ("SUPERATO", "SALTATO"), (caso["id"], originale)
                continue
            if originale == "SUPERATO":
                giudicati += 1
                tipi.add(nome.split(":")[0].rsplit(" ", 1)[-1])
                if stato != "FALLITO":
                    sbagliati.append(f"{nome} → {stato}")
            elif stato == "SUPERATO":
                sbagliati.append(f"{nome} → SUPERATO (originale SALTATO)")
    return sbagliati, giudicati, tipi


@pytest.mark.parametrize("famiglia", FAMIGLIE)
@pytest.mark.schemi_hl7
def test_ogni_mutante_di_un_caso_verde_e_fallito(famiglia):
    pytest.importorskip("lxml")
    sbagliati, giudicati, tipi = controlla_mutanti(famiglia, _motore())
    assert sbagliati == [], "\n".join(sbagliati)
    if famiglia != "fse" or dipendenze_locali_presenti():  # online compreso: lo specchio lo fa giudicare
        # il controllo deve mordere davvero: almeno una dozzina di mutanti giudicati, di ogni tipo
        assert giudicati >= 12, giudicati
        assert tipi >= {"invertita", "codice", "inesistente"}, tipi


def test_le_mutazioni_coprono_ogni_tipo_di_atteso():
    """Nessun tipo di passo resta senza mutanti (altrimenti il controllo sarebbe vuoto per quella famiglia)."""
    for caso in carica_casi("tutte"):
        for passo in caso["passi"]:
            if "atteso" in passo:
                assert len(mutazioni(passo["operazione"], passo["atteso"])) >= 3, (caso["id"], passo["operazione"])


def test_un_motore_che_dice_sempre_superato_viene_scoperto(monkeypatch):
    """Gruppo di controllo del mutation check stesso: se il confronto non confronta niente, i mutanti passano."""
    pytest.importorskip("lxml")
    from varco.conformita import motore as mod

    monkeypatch.setattr(mod, "verifica", lambda atteso, oss: [])
    m = _motore()
    caso = json.loads((CONF / "casi" / "OFF-001.json").read_text(encoding="utf-8"))
    assert m.esegui(caso).stato == "SUPERATO"
    assert any(m.esegui(x).stato == "SUPERATO" for _, x in mutanti(caso))


# ------------------------------------------------------------------ lo stesso controllo sull'esecutore Java (FSE)


@pytest.mark.ufficiale
def test_mutanti_fse_sull_esecutore_java(tmp_path):
    """I mutanti dei casi FSE con il validatore ufficiale, eseguiti da EseguiCasiFse.java (nessun codice in comune)."""
    qui = cartella_validatore_ufficiale()
    validatore = SPEC / "fse" / "it-fse-gtw-validator"
    cp = (validatore / "target" / "cp.txt")
    if not cp.exists():
        pytest.skip("validatore ufficiale non preparato (strumenti/validatore-ufficiale/prepara.sh)")
    conf = tmp_path / "conformita"
    (conf / "casi").mkdir(parents=True)
    (conf / "documenti").symlink_to(CONF / "documenti")
    attesi: dict[str, str] = {}
    n = 900
    for caso in carica_casi("fse"):
        if any(p["operazione"] != "valida_documento" for p in caso["passi"]):
            continue  # genera_pss: l'esecutore Java non ha un generatore (SALTATO)
        (conf / "casi" / f"{caso['id']}.json").write_text(json.dumps(caso), encoding="utf-8")
        attesi[caso["id"]] = "SUPERATO"
        for descrizione, m in mutanti(caso):
            m["id"], m["titolo"] = f"FSE-{n}", descrizione
            (conf / "casi" / f"FSE-{n}.json").write_text(json.dumps(m), encoding="utf-8")
            attesi[m["id"]] = "FALLITO"
            n += 1
    assert sum(v == "FALLITO" for v in attesi.values()) >= 12
    rapporto = tmp_path / "rapporto.json"
    classpath = f"{qui / 'classi'}:{validatore / 'target' / 'classes'}:{cp.read_text().strip()}"
    r = subprocess.run([os.path.join(os.environ["JAVA_HOME"], "bin", "java"), "-Xmx3g", "-cp", classpath, "EseguiCasiFse",
                        str(SPEC / "fse" / "mongo-dump"), str(conf), str(rapporto)],
                       capture_output=True, text=True, timeout=900)
    assert rapporto.exists(), r.stdout[-2000:] + r.stderr[-2000:]
    stati = {c["id"]: (c["stato"], c["titolo"]) for c in json.loads(rapporto.read_text())["casi"]}
    sbagliati = [f"{i} {stati[i][1]} → {stati[i][0]} (atteso {v})" for i, v in attesi.items() if stati[i][0] != v]
    assert sbagliati == [], "\n".join(sbagliati)
    assert r.returncode == 1  # ci sono FALLITO: l'esecutore Java non deve uscire con 0
