# SPDX-License-Identifier: EUPL-1.2
"""Esecutore Python dei casi di conformità.

I casi NON stanno qui: stanno in `conformita/` alla radice del progetto, in JSON,
con il loro schema (`conformita/schema/caso.schema.json`). Lo schema è la
specifica: descrive ogni operazione, ogni aspettativa e l'oggetto "osservato"
che un esecutore deve ricavare dalla risposta. Questo modulo è UNA
implementazione di quella specifica; un'altra (in Java, C#, JavaScript...) legge
gli stessi file. In strumenti/validatore-ufficiale c'è un esecutore Java dei casi FSE.

Famiglie:
  offline  risposte reali registrate e codifica delle richieste (niente rete)
  online   scenari contro il SAC di test (rete, utenze pubbliche del kit MEF)
  fse      documenti CDA2 e loro esito di validazione (niente rete)
  sist     SIST Regione Puglia: codifica delle richieste CVP e del CDA2 di prescrizione,
           lettura di risposte SINTETICHE (conformita/risposte/sist/LEGGIMI.md). Niente rete.
           La parte XSD della codifica CVP richiede CVPService.xsd ($VARCO_XSD_SIST o
           --xsd-sist): lo schema è della Regione e non sta nel repository; senza, SALTATO.
  fvg      SAR Regione Friuli-Venezia Giulia (Insiel): codifica delle richieste e lettura di
           risposte SINTETICHE (conformita/risposte/fvg/LEGGIMI.md). Niente rete. La parte XSD
           richiede la cartella `wsdl/sar` di wsdl_prescritto.zip ($VARCO_XSD_FVG o --xsd-fvg):
           gli schemi sono di Insiel e non stanno nel repository; senza, SALTATO.
  piemonte SIRPED Regione Piemonte (CSI): codifica delle richieste di prescrizione (XSD del MEF,
           inclusi) e di CreateAuth/CheckToken/RevokeAuth (XSD del kit A2F del Sistema TS, da fuori:
           $VARCO_XSD_A2F o --xsd-a2f; senza, SALTATO), intestazioni HTTP delle due modalità di
           secondo fattore, PKCE, firma del JWT, lettura di risposte SINTETICHE
           (conformita/risposte/piemonte/LEGGIMI.md). Niente rete.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..ambiente import leggi
from ..credenziali import Credenziali
from ..kit_mef import UTENTE_INESISTENTE
from ..errori import ErroreSOAP, RicettaNonValida
from ..ricetta import xml_sac
from ..ricetta.json import criteri_da_dict, ricetta_da_dict
from ..trasporto.soap import sbusta
from .adattatori import Adattatore
from .piemonte import testo_in

RE_NRE = re.compile(r"^[0-9]{3}[0-9A-Z]{2}[0-9]{10}$")
_SEGNAPOSTO = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

OPERAZIONI_ONLINE = {"invia", "visualizza", "annulla", "interroga_nre"}
OPERAZIONI_OFFLINE = {"leggi_invio", "leggi_visualizza", "leggi_annulla", "leggi_interroga_nre",
                      "codifica_invio", "codifica_interroga_nre"}
OPERAZIONI_FSE = {"valida_documento", "genera_pss"}
OPERAZIONI_SIST_LEGGI = {"leggi_sist_chk", "leggi_sist_registra", "leggi_sist_annulla", "leggi_sist_identificata",
                         "leggi_sist_ricerca"}
OPERAZIONI_SIST_CODIFICA = {"codifica_sist_chk", "codifica_sist_ricerca", "codifica_sist_cda"}
OPERAZIONI_FVG = {"leggi_fvg", "codifica_fvg"}
OPERAZIONI_PIEMONTE = {"codifica_piemonte", "leggi_piemonte_a2f", "intestazioni_piemonte", "pkce_piemonte",
                       "leggi_piemonte_jwt"}
FAMIGLIE = ("offline", "online", "fse", "sist", "fvg", "piemonte")

CREDENZIALI_INESISTENTI = Credenziali(UTENTE_INESISTENTE, "password-errata", "0000000000")

GeneratorePSS = Callable[[dict], bytes]


def cartella_conformita() -> Path:
    """`conformita/` alla radice del progetto, oppure $VARCO_CONFORMITA."""
    env = leggi("VARCO_CONFORMITA")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "conformita"


@dataclass
class EsitoPasso:
    operazione: str
    superato: bool
    dettagli: list[str] = field(default_factory=list)
    osservato: dict[str, Any] = field(default_factory=dict)
    saltato: str | None = None


@dataclass
class EsitoCaso:
    id: str
    titolo: str
    stato: str  # SUPERATO | FALLITO | SALTATO | ERRORE
    passi: list[EsitoPasso] = field(default_factory=list)
    motivo: str | None = None


class _Salta(Exception):
    pass


class SuiteNonValida(ValueError):
    """La selezione dei casi non è eseguibile (cartella inesistente, id sconosciuti): mai un verde vuoto."""


def carica_casi(famiglia: str | None = None, soli: list[str] | None = None, cartella: Path | None = None) -> list[dict]:
    """Casi della famiglia (o 'tutte'), eventualmente solo gli id in `soli`.

    Solleva SuiteNonValida se la cartella dei casi non esiste o se un id richiesto non c'è
    (o è di un'altra famiglia): una selezione vuota per sbaglio non deve diventare un verde."""
    cartella_casi = (cartella or cartella_conformita()).joinpath("casi")
    if not cartella_casi.is_dir():
        raise SuiteNonValida(f"la cartella dei casi non esiste: {cartella_casi}")
    casi = []
    for f in sorted(cartella_casi.glob("*.json")):
        caso = json.loads(f.read_text(encoding="utf-8"))
        if famiglia and famiglia != "tutte" and caso.get("famiglia") != famiglia:
            continue
        if soli and caso["id"] not in soli:
            continue
        casi.append(caso)
    if soli:
        ignoti = sorted(set(soli) - {c["id"] for c in casi})
        if ignoti:
            dove = f" nella famiglia {famiglia}" if famiglia and famiglia != "tutte" else ""
            raise SuiteNonValida(f"id non trovati{dove} in {cartella_casi}: {', '.join(ignoti)}")
    return casi


def _sostituisci(v: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(v, dict):
        return {k: _sostituisci(x, ctx) for k, x in v.items()}
    if isinstance(v, list):
        return [_sostituisci(x, ctx) for x in v]
    if isinstance(v, str):
        m = _SEGNAPOSTO.fullmatch(v)
        if m:
            if m.group(1) not in ctx:
                raise KeyError(f"variabile non definita: {m.group(1)}")
            return copy.deepcopy(ctx[m.group(1)])
        return _SEGNAPOSTO.sub(lambda mm: str(ctx[mm.group(1)]), v)
    return v


# ------------------------------------------------------------------ osservazione (vedi schema: "osservato")


def osserva_esito(esito: Any) -> dict[str, Any]:
    """L'oggetto 'osservato' dello schema, ricavato da un esito del kit."""
    oss: dict[str, Any] = {
        "codice": esito.codice,
        "messaggi": [
            {"codice": m.codice, "testo": m.testo, "tipo": m.tipo, "bloccante": m.bloccante, "avviso": m.gravita == "W"}
            for m in esito.messaggi
        ],
        "comunicazioni": [c.codice for c in esito.comunicazioni],
    }
    for campo in ("nre", "codice_autenticazione", "data_inserimento", "stato_processo", "cognome_medico", "nome_medico",
                  "stato_sar", "oscurato"):
        if getattr(esito, campo, None) is not None:
            oss[campo] = getattr(esito, campo)
    if hasattr(esito, "solo_ricetta_rossa"):  # esiti SAR (SIST)
        oss["solo_ricetta_rossa"] = esito.solo_ricetta_rossa
    if getattr(esito, "cda", None):
        oss["cda"] = True
    if hasattr(esito, "righe"):
        oss["righe"] = len(esito.righe)
    if hasattr(esito, "testata") and esito.testata:
        for t in ("cfMedico1", "cfMedico2"):
            if esito.testata.get(t):
                oss[t] = esito.testata[t]
    if hasattr(esito, "pdf_promemoria"):
        oss["pdf_promemoria"] = esito.pdf_promemoria is not None and esito.pdf_promemoria.startswith(b"%PDF")
    if hasattr(esito, "ricette"):
        oss["ricette"] = [r.nre for r in esito.ricette]
    if getattr(esito, "note", None):  # ElencoNota della ricevuta d'invio
        oss["note"] = [{"progressivo": n.progressivo, "codice_prestazione": n.codice_prestazione,
                        "tipo_ambulatorio": n.tipo_ambulatorio} for n in esito.note]
    return oss


def _nel(valore: Any, ammessi: Any) -> bool:
    return valore in ammessi if isinstance(ammessi, list) else valore == ammessi


# ------------------------------------------------------------------ aspettative: chiavi note e coerenza
#
# Il motore confronta OGNI aspettativa dichiarata. Una chiave che non conosce (un refuso, un
# dialetto di un altro esecutore) o che non si applica all'operazione non si può verificare:
# il passo è FALLITO, mai un verde per omissione. Le chiavi sono quelle di
# conformita/schema/caso.schema.json ($defs/atteso*); un test le tiene allineate.

CHIAVI_ATTESO: dict[str, set[str]] = {
    "attesoSac": {"codice", "nre_formato", "presenti", "stato_processo", "errore_codice", "errore_bloccante",
                  "nessun_bloccante", "avvisi_codici", "comunicazioni_presenti", "righe", "nre", "pdf_promemoria",
                  "ricette_numero", "ricette_almeno", "ricette_contengono", "campi", "fault_contiene", "rifiuto_locale"},
    "attesoCodifica": {"xsd_valido", "tag", "tag_assenti", "attributi", "rifiuto_locale", "testi"},
    "attesoFse": {"esito", "valido", "errori_contengono", "senza_errori"},
    "attesoPiemonte": {"codice", "ok", "campi", "errore_codice", "errore_bloccante", "nessun_bloccante", "intestazioni",
                       "intestazioni_prefisso", "assenti", "rifiuto_locale"},
}

_ESITI_FSE_VALIDI = ("OK", "SEMANTIC_WARNING")
_CHIAVI_INTESTAZIONI = ("intestazioni", "intestazioni_prefisso", "assenti")


def tipo_atteso(op: str) -> str | None:
    """Il $defs dello schema che descrive 'atteso' per l'operazione (None: operazione sconosciuta)."""
    if op in OPERAZIONI_FSE:
        return "attesoFse"
    if op.startswith("codifica_"):
        return "attesoCodifica"
    if op in OPERAZIONI_PIEMONTE:
        return "attesoPiemonte"
    if op in OPERAZIONI_ONLINE or op in OPERAZIONI_SIST_LEGGI or op == "leggi_fvg" or op in OPERAZIONI_OFFLINE:
        return "attesoSac"
    return None


def _lista(v: Any) -> list:
    return v if isinstance(v, list) else [v]


def incoerenze(op: str, atteso: dict[str, Any]) -> list[str]:
    """Aspettative che il motore non sa verificare o che si contraddicono. Non dipende
    dall'implementazione: un caso così è sbagliato comunque, e il passo è FALLITO."""
    tipo = tipo_atteso(op)
    if tipo is None or not isinstance(atteso, dict):
        return []  # l'operazione sconosciuta la segnala l'esecuzione (ERRORE)
    ko = [f"aspettativa sconosciuta {k!r} per {op}: il motore non sa verificarla"
          for k in sorted(set(atteso) - CHIAVI_ATTESO[tipo])]
    altre = sorted(set(atteso) - {"rifiuto_locale"})
    if atteso.get("rifiuto_locale") and altre:
        ko.append(f"rifiuto_locale con altre aspettative {altre}: un rifiuto locale non produce niente da confrontare")
    if "fault_contiene" in atteso and len(atteso) > 1:
        ko.append(f"fault_contiene con altre aspettative {sorted(set(atteso) - {'fault_contiene'})}: un SOAP Fault non le ha")
    if atteso.get("errore_bloccante") and atteso.get("nessun_bloccante"):
        ko.append("errore_bloccante e nessun_bloccante insieme")
    if tipo == "attesoSac":
        if "ricette_numero" in atteso:
            if atteso.get("ricette_almeno", 0) > atteso["ricette_numero"]:
                ko.append("ricette_almeno maggiore di ricette_numero")
            if len(set(atteso.get("ricette_contengono", []))) > atteso["ricette_numero"]:
                ko.append("ricette_contengono ha più NRE di ricette_numero")
        # Un segnaposto (${nre_atteso}) si giudica dopo la sostituzione, sul valore osservato: qui no
        # (revisione esterna giro 2, N3: «${nre_atteso}» era dato per NRE malformato).
        if ("nre" in atteso and atteso.get("nre_formato") and not _SEGNAPOSTO.search(str(atteso["nre"]))
                and not RE_NRE.match(atteso["nre"])):
            ko.append(f"nre atteso {atteso['nre']!r} non rispetta nre_formato")
    if tipo == "attesoCodifica":
        doppi = sorted(set(atteso.get("tag", {})) & set(atteso.get("tag_assenti", [])))
        if doppi:
            ko.append(f"tag sia attesi sia assenti: {doppi}")
        # Lo schema li ammette solo lì: altrove un esecutore indipendente li ignorerebbe (verde finto).
        if "attributi" in atteso and not (op.startswith("codifica_sist_") or op == "codifica_fvg"):
            ko.append(f"aspettativa 'attributi' in {op}: lo schema la prevede solo per codifica_sist_* e codifica_fvg")
        if "testi" in atteso and op != "codifica_piemonte":
            ko.append(f"aspettativa 'testi' in {op}: lo schema la prevede solo per codifica_piemonte")
    if tipo == "attesoFse":
        esiti = _lista(atteso["esito"]) if "esito" in atteso else None
        if esiti is not None and "valido" in atteso:
            compatibili = [e for e in esiti if (e in _ESITI_FSE_VALIDI) == atteso["valido"]]
            if not compatibili:
                ko.append(f"valido={atteso['valido']} incompatibile con esito {atteso['esito']}")
        if atteso.get("senza_errori") and atteso.get("errori_contengono"):
            ko.append("senza_errori ed errori_contengono insieme")
    if tipo == "attesoPiemonte":
        if any(k in atteso for k in _CHIAVI_INTESTAZIONI) and op != "intestazioni_piemonte":
            ko.append(f"aspettative sulle intestazioni HTTP in {op}: solo intestazioni_piemonte le produce")
        if op != "leggi_piemonte_a2f":
            for k in ("codice", "ok", "errore_codice", "errore_bloccante", "nessun_bloccante"):
                if k in atteso:
                    ko.append(f"aspettativa {k!r} in {op}: solo leggi_piemonte_a2f la produce")
        # i nomi degli header non distinguono le maiuscole; anche un prefisso (pure vuoto) dice «presente»
        # (revisione esterna giro 3, Piemonte N2)
        assenti = {str(n).lower() for n in atteso.get("assenti", [])}
        for chiave in ("intestazioni", "intestazioni_prefisso"):
            doppi = sorted(n for n in atteso.get(chiave, {}) if str(n).lower() in assenti)
            if doppi:
                ko.append(f"header sia in {chiave} sia assenti: {doppi}")
        if atteso.get("ok") is True and (atteso.get("errore_bloccante") or ("codice" in atteso and "0" not in atteso["codice"])):
            ko.append("ok=true vuole codice 0 e nessun errore bloccante")
    return ko


def _verifica_rifiuto(atteso: dict[str, Any], oss: dict[str, Any]) -> list[str] | None:
    """Parte comune a tutte le famiglie sul rifiuto locale. None: nessun rifiuto in gioco, si
    prosegue col confronto; altrimenti l'elenco definitivo delle aspettative non rispettate."""
    if oss.get("rifiuto_locale") is not None:
        ko = [] if atteso.get("rifiuto_locale") else [f"rifiuto locale inatteso: {oss['rifiuto_locale']}"]
        ko += [f"aspettativa {k!r} non verificabile: l'implementazione ha rifiutato prima di produrre la richiesta"
               for k in sorted(atteso) if k != "rifiuto_locale"]
        return ko
    if atteso.get("rifiuto_locale"):
        return ["atteso un rifiuto locale, l'implementazione è andata avanti"]
    return None


def verifica(atteso: dict[str, Any], oss: dict[str, Any]) -> list[str]:
    """Elenco delle aspettative NON rispettate. Lavora solo sull'oggetto osservato."""
    rifiuto = _verifica_rifiuto(atteso, oss)
    if rifiuto is not None:
        return rifiuto
    ko: list[str] = []
    if "fault_contiene" in atteso:
        if oss.get("fault") is None:
            ko.append(f"atteso SOAP Fault contenente {atteso['fault_contiene']!r}, arrivata una risposta {oss.get('codice')}")
        elif atteso["fault_contiene"].lower() not in oss["fault"].lower():
            ko.append(f"fault {oss['fault']!r} non contiene {atteso['fault_contiene']!r}")
        ko += [f"aspettativa {k!r} non verificabile su un SOAP Fault" for k in sorted(atteso) if k != "fault_contiene"]
        return ko
    if oss.get("fault") is not None:
        return [f"SOAP Fault inatteso: {oss['fault']}"]
    if "codice" in atteso and not _nel(oss.get("codice"), atteso["codice"]):
        ko.append(f"codice esito {oss.get('codice')} non in {atteso['codice']}")
    if atteso.get("nre_formato") and not RE_NRE.match(oss.get("nre") or ""):
        ko.append(f"NRE {oss.get('nre')!r} non ha il formato AAA BB C DDDDDDD EE (15 caratteri)")
    for campo in atteso.get("presenti", []):
        if not oss.get(campo):
            ko.append(f"campo {campo} assente")
    if "stato_processo" in atteso and oss.get("stato_processo") != atteso["stato_processo"]:
        ko.append(f"stato processo {oss.get('stato_processo')} invece di {atteso['stato_processo']}")
    messaggi = oss.get("messaggi", [])
    codici = [m["codice"] for m in messaggi]
    if "errore_codice" in atteso and atteso["errore_codice"] not in codici:
        ko.append(f"errore {atteso['errore_codice']} non presente (presenti: {codici})")
    if atteso.get("errore_bloccante") and not any(m["bloccante"] for m in messaggi):
        ko.append("nessun messaggio bloccante")
    if atteso.get("nessun_bloccante") and any(m["bloccante"] for m in messaggi):
        ko.append(f"messaggi bloccanti presenti: {[m['codice'] for m in messaggi if m['bloccante']]}")
    for cod in atteso.get("avvisi_codici", []):
        if not any(m["codice"] == cod and m["avviso"] for m in messaggi):
            ko.append(f"avviso {cod} assente")
    for cod in atteso.get("comunicazioni_presenti", []):
        if cod not in oss.get("comunicazioni", []):
            ko.append(f"comunicazione {cod} assente")
    if "righe" in atteso and oss.get("righe") != atteso["righe"]:
        ko.append(f"righe {oss.get('righe')} invece di {atteso['righe']}")
    if "nre" in atteso and oss.get("nre") != atteso["nre"]:
        ko.append(f"NRE {oss.get('nre')} invece di {atteso['nre']}")
    if atteso.get("pdf_promemoria") and not oss.get("pdf_promemoria"):
        ko.append("PDF promemoria assente o non valido")
    if "ricette_numero" in atteso and len(oss.get("ricette", [])) != atteso["ricette_numero"]:
        ko.append(f"{len(oss.get('ricette', []))} ricette invece di {atteso['ricette_numero']}")
    if "ricette_almeno" in atteso and len(oss.get("ricette", [])) < atteso["ricette_almeno"]:
        ko.append(f"{len(oss.get('ricette', []))} ricette, attese almeno {atteso['ricette_almeno']}")
    for nre in atteso.get("ricette_contengono", []):
        if nre not in oss.get("ricette", []):
            ko.append(f"NRE {nre} non è nella lista")
    for k, v in atteso.get("campi", {}).items():
        if oss.get(k) != v:
            ko.append(f"{k} = {oss.get(k)!r} invece di {v!r}")
    return ko


def verifica_documento(atteso: dict[str, Any], oss: dict[str, Any]) -> list[str]:
    """Aspettative sulla validazione di un documento FSE (vedi schema, "attesoFse")."""
    ko: list[str] = []
    if oss.get("rifiuto_locale") is not None:
        # Un rifiuto locale soddisfa SOLO valido:false (schema, attesoFse). Ogni altra aspettativa
        # dichiarata riguarda un documento che non esiste: non verificabile, quindi non rispettata.
        if atteso.get("valido") is not False:
            ko.append(f"l'implementazione ha rifiutato di generare il documento: {oss['rifiuto_locale']}")
        ko += [f"aspettativa {k!r} non verificabile: nessun documento generato (rifiuto locale)"
               for k in sorted(atteso) if k != "valido"]
        return ko
    if "esito" in atteso and not _nel(oss["esito"], atteso["esito"]):
        ko.append(f"esito {oss['esito']} invece di {atteso['esito']}")
    if "valido" in atteso and (oss["esito"] in _ESITI_FSE_VALIDI) != atteso["valido"]:
        ko.append(f"esito {oss['esito']}: atteso {'valido' if atteso['valido'] else 'non valido'}")
    for frammento in atteso.get("errori_contengono", []):
        if not any(frammento in e for e in oss["errori"]):
            ko.append(f"nessun errore contiene {frammento!r} (errori: {oss['errori'][:5]})")
    if atteso.get("senza_errori") and oss["errori"]:
        ko.append(f"errori presenti: {oss['errori'][:5]}")
    return ko


# ------------------------------------------------------------------ motore


class Motore:
    def __init__(
        self,
        adattatore: Adattatore | None = None,
        credenziali: Credenziali | None = None,
        contesto: dict[str, Any] | None = None,
        *,
        credenziali_sostituto: Credenziali | None = None,
        validatore_fse=None,
        generatore_pss: GeneratorePSS | None = None,
        cartella: Path | None = None,
        xsd_sist: Path | None = None,
        xsd_fvg: Path | None = None,
        xsd_a2f: Path | None = None,
    ):
        self.adattatore = adattatore
        self.credenziali = credenziali
        self.credenziali_sostituto = credenziali_sostituto
        self.contesto_base = contesto or {}
        self.validatore_fse = validatore_fse
        self.generatore_pss = generatore_pss
        self.cartella = cartella or cartella_conformita()
        env = leggi("VARCO_XSD_SIST")
        self.xsd_sist = Path(xsd_sist) if xsd_sist else (Path(env) if env else None)
        self._schema_sist = None
        env_fvg = leggi("VARCO_XSD_FVG")
        self.xsd_fvg = Path(xsd_fvg) if xsd_fvg else (Path(env_fvg) if env_fvg else None)
        self._schemi_fvg: dict[str, Any] = {}
        env_a2f = leggi("VARCO_XSD_A2F")
        self.xsd_a2f = Path(xsd_a2f) if xsd_a2f else (Path(env_a2f) if env_a2f else None)
        self._schema_a2f = None

    # ------------------------------------------------------------------
    def esegui(self, caso: dict) -> EsitoCaso:
        famiglia = caso.get("famiglia", "online")
        # Un caso senza passi non verifica niente: lo schema ne vuole almeno uno (caso.schema.json,
        # "passi": minItems 1). Prima il risultato partiva SUPERATO e restava tale (revisione esterna
        # giro 2, 6-conformita N1).
        if not isinstance(caso.get("passi"), list) or not caso["passi"]:
            return EsitoCaso(caso.get("id", "?"), caso.get("titolo", ""), "FALLITO",
                             motivo="caso senza passi: lo schema ne vuole almeno uno (passi, minItems 1)")
        # Un caso che non rispetta lo schema del formato non si esegue: FALLITO. In particolare ogni
        # passo vuole le sue aspettative, non vuote: senza `atteso` (o con `atteso: {}`) il passo
        # non confronta niente e il caso diventava SUPERATO (revisione esterna giro 3, conformità n. 2).
        difetti_formato = self._difetti_di_formato(caso)
        if difetti_formato:
            return EsitoCaso(caso.get("id", "?"), caso.get("titolo", ""), "FALLITO",
                             motivo="caso non conforme al formato: " + "; ".join(difetti_formato))
        # Un caso incoerente è sbagliato con qualunque implementazione: FALLITO, prima di tutto
        # (anche prima dei SALTATO per mancanza di credenziali o validatori).
        for passo in caso["passi"]:
            difetti = incoerenze(passo["operazione"], passo.get("atteso", {}))
            if difetti:
                return EsitoCaso(caso["id"], caso["titolo"], "FALLITO",
                                 [EsitoPasso(passo["operazione"], False, difetti)],
                                 motivo="aspettative incoerenti o sconosciute: " + "; ".join(difetti))
        if famiglia == "online" and (self.adattatore is None or self.credenziali is None):
            return EsitoCaso(caso["id"], caso["titolo"], "SALTATO", motivo="serve un adattatore online e le credenziali di test")
        if any(p["operazione"].startswith("codifica_") for p in caso["passi"]):
            from ..schemi import lxml_disponibile

            if not lxml_disponibile():
                return EsitoCaso(caso["id"], caso["titolo"], "SALTATO", motivo="lxml non installato (validazione XSD)")
        if famiglia == "fse" and self.validatore_fse is None:
            return EsitoCaso(caso["id"], caso["titolo"], "SALTATO", motivo="nessun validatore FSE disponibile (il locale vuole lxml, saxonche e gli "
                             "schemi HL7: python strumenti/scarica_specifiche.py --gruppi cda-xsd)")
        ctx = dict(self.contesto_base)
        risultato = EsitoCaso(caso["id"], caso["titolo"], "SUPERATO")
        try:
            for passo in caso["passi"]:
                ep = self._passo(passo, ctx)
                risultato.passi.append(ep)
                if ep.saltato:
                    risultato.stato, risultato.motivo = "SALTATO", ep.saltato
                    break
                if not ep.superato:
                    risultato.stato = "FALLITO"
                    break
        except Exception as e:  # errore dell'implementazione o del caso
            risultato.stato = "ERRORE"
            risultato.motivo = repr(e)
        finally:
            for passo in caso.get("finale", []):
                try:
                    self._passo(passo, ctx, pulizia=True)
                except Exception:
                    pass
        return risultato

    def _difetti_di_formato(self, caso: dict) -> list[str]:
        """Ciò che rende il caso non conforme: aspettative assenti o vuote in un passo (sempre) e, se
        `jsonschema` è installato, ogni errore rispetto a conformita/schema/caso.schema.json."""
        difetti = []
        for i, passo in enumerate(caso["passi"], 1):
            if not isinstance(passo, dict):
                difetti.append(f"passo {i}: non è un oggetto")
                continue
            atteso = passo.get("atteso")
            if not isinstance(atteso, dict) or not atteso:
                difetti.append(f"passo {i} ({passo.get('operazione')}): "
                               + ("manca 'atteso'" if atteso is None else "'atteso' vuoto o non è un oggetto")
                               + ": un passo senza aspettative non verifica niente")
        schema = self._schema_casi()
        if schema is not None:
            for e in sorted(schema.iter_errors(caso), key=lambda e: list(e.absolute_path)):
                dove = "/".join(str(x) for x in e.absolute_path) or "(radice)"
                difetti.append(f"schema: {dove}: {e.message[:200]}")
                if len(difetti) >= 10:
                    break
        return difetti

    def _schema_casi(self):
        if not hasattr(self, "_validatore_schema"):
            self._validatore_schema = None
            percorso = self.cartella / "schema" / "caso.schema.json"
            try:
                from jsonschema import Draft202012Validator
                from referencing import Registry, Resource
            except ImportError:  # dipendenza dei test: senza, resta il controllo sulle aspettative
                return None
            if percorso.is_file():
                # gli altri schemi della cartella (ricetta, pss) per i $ref per nome di file
                risorse = []
                for f in sorted(percorso.parent.glob("*.schema.json")):
                    contenuto = json.loads(f.read_text(encoding="utf-8"))
                    for uri in {f.name, contenuto.get("$id") or f.name}:
                        risorse.append((uri, Resource.from_contents(contenuto)))
                registro = Registry().with_resources(risorse)
                self._validatore_schema = Draft202012Validator(
                    json.loads(percorso.read_text(encoding="utf-8")), registry=registro)
        return self._validatore_schema

    # ------------------------------------------------------------------
    def _credenziali(self, nome: str) -> Credenziali:
        if nome == "inesistente":
            # Utenza che non esiste: così non si rischia di bloccare l'utenza condivisa del kit.
            return CREDENZIALI_INESISTENTI
        if nome == "sostituto":
            if self.credenziali_sostituto is None:
                raise _Salta("servono le credenziali del medico sostituto di test")
            return self.credenziali_sostituto
        return self.credenziali

    def _servizio(self, passo: dict):
        return self.adattatore(self._credenziali(passo.get("credenziali", "kit")), passo.get("valida_localmente", True))

    def _passo(self, passo: dict, ctx: dict[str, Any], pulizia: bool = False) -> EsitoPasso:
        op = passo["operazione"]
        if pulizia:
            usate = _SEGNAPOSTO.findall(json.dumps(passo))
            if any(ctx.get(v) in (None, "") for v in usate):
                return EsitoPasso(op, True, ["pulizia saltata: variabile non valorizzata"])
        try:
            p = _sostituisci({k: v for k, v in passo.items() if k != "salva"}, ctx)
            if op in OPERAZIONI_FSE:
                return self._passo_fse(op, p)
            if op in OPERAZIONI_SIST_CODIFICA:
                return self._passo_codifica_sist(op, p)
            if op == "codifica_fvg":
                return self._passo_codifica_fvg(p)
            if op == "leggi_fvg":
                return self._passo_leggi_fvg(p)
            if op in OPERAZIONI_PIEMONTE:
                return self._passo_piemonte(op, p)
            if op.startswith("codifica_"):
                return self._passo_codifica(op, p)
            esito, fault = None, None
            if op in OPERAZIONI_ONLINE:
                servizio = self._servizio(p)
                cf = p.get("cf_medico")
                try:
                    if op == "invia":
                        esito = servizio.invia(ricetta_da_dict(p["ricetta"]))
                    elif op == "visualizza":
                        esito = servizio.visualizza(p["nre"], cf_medico=cf)
                    elif op == "annulla":
                        esito = servizio.annulla(p["nre"], cf_medico=cf)
                    else:
                        esito = servizio.interroga_nre_utilizzati(criteri_da_dict(p["criteri"]), cf_medico=cf)
                except ErroreSOAP as e:
                    fault = e
                except RicettaNonValida as e:
                    oss = {"rifiuto_locale": str(e)}
                    ko = [] if pulizia else verifica(p.get("atteso", {}), oss)
                    return EsitoPasso(op, not ko, ko or [str(e)], oss)
            elif op in ("leggi_invio", "leggi_visualizza", "leggi_annulla", "leggi_interroga_nre"):
                xml = self.cartella.joinpath("risposte", p["risposta"]).read_bytes()
                try:
                    el = sbusta(xml, 200)
                    lettore = {
                        "leggi_invio": xml_sac.leggi_ricevuta_invio,
                        "leggi_visualizza": xml_sac.leggi_ricevuta_visualizza,
                        "leggi_annulla": xml_sac.leggi_ricevuta_annulla,
                        "leggi_interroga_nre": xml_sac.leggi_ricevuta_interroga_nre,
                    }[op]
                    esito = lettore(el)
                except ErroreSOAP as e:
                    fault = e
            elif op in OPERAZIONI_SIST_LEGGI:
                esito, fault = self._leggi_sist(op, p)
            else:
                raise ValueError(f"operazione sconosciuta: {op}")
        except _Salta as s:
            return EsitoPasso(op, False, saltato=str(s))

        oss = osserva_esito(esito) if esito is not None else {"fault": fault.faultstring if fault else None}
        ko = [] if pulizia else verifica(p.get("atteso", {}), oss)
        if esito is not None:
            for var, campo in passo.get("salva", {}).items():
                ctx[var] = oss.get(campo)
        return EsitoPasso(op, not ko, ko, oss)

    def _passo_codifica(self, op: str, p: dict) -> EsitoPasso:
        from ..schemi import errori_xsd

        if op == "codifica_invio":
            el = xml_sac.richiesta_invio(ricetta_da_dict(p["ricetta"]), "0000000000", lambda s: "CIFRATO==")
        else:
            el = xml_sac.richiesta_interroga_nre(criteri_da_dict(p["criteri"]), p.get("cf_medico", "PROVAX00X00X000Y"),
                                                 "0000000000", lambda s: "CIFRATO==")
        return _esito_codifica(op, p.get("atteso", {}), el, errori_xsd(el))

    # ------------------------------------------------------------------ SIST (Regione Puglia)

    def _leggi_sist(self, op: str, p: dict):
        from ..ricetta import xml_sist
        from ..ricetta.modello import Esito

        xml = self.cartella.joinpath("risposte", p["risposta"]).read_bytes()
        try:
            el = sbusta(xml, 200)
        except ErroreSOAP as e:
            return None, e
        if op == "leggi_sist_chk":
            return xml_sist.leggi_chk(el), None
        if op == "leggi_sist_registra":
            # setRegistraPrescrizione risponde solo esito TRUE/FALSE: lo si riporta al codice esito
            return Esito(codice="0000" if xml_sist.leggi_registra(el) else "9999"), None
        if op == "leggi_sist_annulla":
            return xml_sist.leggi_annulla(el), None
        if op == "leggi_sist_identificata":
            return xml_sist.leggi_identificata(el), None
        return xml_sist.leggi_ricerca(el), None

    def _xsd_sist(self):
        if self.xsd_sist is None:
            return None
        if self._schema_sist is None:
            from lxml import etree

            self._schema_sist = etree.XMLSchema(etree.parse(str(self.xsd_sist)))
        return self._schema_sist

    def _passo_codifica_sist(self, op: str, p: dict) -> EsitoPasso:
        """Codifica SIST senza mandare niente: richiesta CVP (chkPrescrizione, getPrescrizioniIdentificate)
        contro CVPService.xsd, oppure CDA2 di prescrizione contro lo schema CDA del kit."""
        from ..ricetta import cda_sist, xml_sist
        from ..trasporto.sist import DatiChiamata, OperatoreSIST

        atteso = p.get("atteso", {})
        codici = p.get("codici_regionali", {})

        def codice(cf: str) -> str:
            return codici[cf]  # KeyError: lo segnala problemi_sist

        operatore = OperatoreSIST(p.get("operatore", "PROVAX00X00X000Y"), "160114")
        dati = DatiChiamata(operatore, "VARCO", "varco", "0.1", "A" * 20, "2026-10-01T11:00:00+0200", "DIGEST==")
        try:
            if op == "codifica_sist_ricerca":
                el = xml_sist.richiesta_ricerca(dati, criteri_da_dict(p["criteri"]), codice(operatore.codice_fiscale))
            else:
                ricetta = ricetta_da_dict(p["ricetta"])
                # Come RicettaSIST: ciò che impedisce di scrivere il CDA (es. motivo di non
                # sostituibilità fuori da 1-4) si controlla PRIMA di chkPrescrizione.
                problemi = (cda_sist.problemi_righe_cda(ricetta) + ricetta.problemi()
                            + xml_sist.problemi_sist(ricetta, codice))
                if problemi:
                    raise RicettaNonValida(problemi)
                if op == "codifica_sist_chk":
                    el = xml_sist.richiesta_chk(ricetta, dati, codice)
                else:
                    sost = ricetta.prescrittore.codice_fiscale_sostituto
                    el = cda_sist.genera(
                        ricetta, p["nre"], p.get("codice_autenticazione"),
                        codice_regionale_prescrittore=codice(sost or ricetta.prescrittore.codice_fiscale),
                        codice_regionale_sostituito=codice(ricetta.prescrittore.codice_fiscale) if sost else None,
                        maggior_tutela=p.get("maggior_tutela", False),
                    )
        except RicettaNonValida as e:
            return _esito_rifiuto(op, atteso, str(e))
        if op == "codifica_sist_cda":
            from ..fse.validazione import SchemiNonTrovati, _schema_cda

            try:
                schema = _schema_cda()
            except SchemiNonTrovati as e:
                return _esito_codifica(op, atteso, el, None, str(e))
        else:
            schema = self._xsd_sist()
        return _esito_codifica(op, atteso, el, _errori_schema(schema, el),
                               "CVPService.xsd non fornito (--xsd-sist o $VARCO_XSD_SIST): "
                               "lo schema della Regione non sta nel repository")

    # ------------------------------------------------------------------ SAR FVG (Insiel)

    # servizio -> XSD della richiesta, relativo alla cartella wsdl/sar di wsdl_prescritto.zip
    XSD_FVG = {
        "invio": "invioPrescritto/v1.0/InvioPrescrittoRichiesta-v1.0.xsd",
        "visualizza": "visualizzaPrescritto/v1.0/VisualizzaPrescrittoRichiesta-v1.0.xsd",
        "annulla": "annullaPrescritto/v1.0/AnnullaPrescrittoRichiesta-v1.0.xsd",
        "interroga_nre": "interrogaNreUtilizzati/InterrogaNreUtilRichiesta.xsd",
        "verifica_sostituto": "gestoreAutorizzazioni/v1.0/VerificaPosizioneMedicoSostitutoRichiesta-v1.0.xsd",
    }

    def _passo_leggi_fvg(self, p: dict) -> EsitoPasso:
        """Lettura di una risposta SINTETICA del SAR FVG. Osservato come osservatoSac; per l'invio in
        più downgrade_mir; per verifica_sostituto abilitato (e i messaggi)."""
        from ..ricetta import fvg, xml_fvg

        servizio = p["servizio"]
        xml = self.cartella.joinpath("risposte", p["risposta"]).read_bytes()
        try:
            el = sbusta(xml, 200)
        except ErroreSOAP as e:
            esito = fvg.esito_da_fault_invio(e) if servizio == "invio" else None
            if esito is None:
                oss = {"fault": e.faultstring}
                ko = verifica(p.get("atteso", {}), oss)
                return EsitoPasso("leggi_fvg", not ko, ko, oss)
        else:
            if servizio == "verifica_sostituto":
                v = xml_fvg.leggi_verifica_sostituto(el)
                oss = {"messaggi": [{"codice": m.codice, "testo": m.testo, "tipo": m.tipo, "bloccante": m.bloccante,
                                     "avviso": m.gravita == "W"} for m in v["messaggi"]],
                       "comunicazioni": [c.codice for c in v["comunicazioni"]],
                       "abilitato": v["abilitato"], "cfMedico1": v["cf_titolare"], "cfMedico2": v["cf_sostituto"]}
                ko = verifica(p.get("atteso", {}), oss)
                return EsitoPasso("leggi_fvg", not ko, ko, oss)
            lettore = {
                "invio": xml_fvg.leggi_ricevuta_invio,
                "visualizza": xml_fvg.leggi_ricevuta_visualizza,
                "annulla": xml_fvg.leggi_ricevuta_annulla,
                "interroga_nre": xml_fvg.leggi_ricevuta_interroga_nre,
            }[servizio]
            esito = lettore(el)
        oss = osserva_esito(esito)
        if servizio == "invio":
            oss["downgrade_mir"] = fvg.richiede_downgrade_mir(esito)
        ko = verifica(p.get("atteso", {}), oss)
        return EsitoPasso("leggi_fvg", not ko, ko, oss)

    def _schema_fvg(self, servizio: str):
        if self.xsd_fvg is None:
            return None
        if servizio not in self._schemi_fvg:
            from lxml import etree

            self._schemi_fvg[servizio] = etree.XMLSchema(etree.parse(str(self.xsd_fvg / self.XSD_FVG[servizio])))
        return self._schemi_fvg[servizio]

    def _passo_codifica_fvg(self, p: dict) -> EsitoPasso:
        """Codifica per il SAR FVG SENZA mandare niente (CF dell'assistito «cifrato» con un valore
        finto), poi validazione contro lo XSD ufficiale FVG del servizio."""
        from ..ricetta import xml_fvg

        op, servizio, atteso = "codifica_fvg", p["servizio"], p.get("atteso", {})
        prodotto = p.get("prodotto_cme", "VARCO-PROVA")
        try:
            if servizio == "invio":
                ricetta = ricetta_da_dict(p["ricetta"])
                # Come RicettaFVG.invia: la versione del catalogo regionale è obbligatoria per la
                # specialistica (par. 3.1, p. 13). Senza questo controllo il caso sarebbe verde su
                # una richiesta che il client vero non manderebbe mai.
                problemi = (ricetta.problemi() + xml_fvg.problemi_fvg(ricetta)
                            + xml_fvg.problemi_versione_cr(ricetta, p.get("versione_cr")))
                if problemi:
                    raise RicettaNonValida(problemi)
                el = xml_fvg.richiesta_invio(ricetta, lambda s: "CIFRATO==", prodotto, p.get("versione_cr"))
            elif servizio == "visualizza":
                el = xml_fvg.richiesta_visualizza(p["nre"], p["cf_medico"], prodotto)
            elif servizio == "annulla":
                el = xml_fvg.richiesta_annulla(p["nre"], p["cf_medico"], prodotto)
            elif servizio == "verifica_sostituto":
                el = xml_fvg.richiesta_verifica_sostituto(p["cf_titolare"], p["cf_sostituto"], p["codice_asl"], prodotto)
            else:
                criteri = criteri_da_dict(p["criteri"])
                problemi = criteri.problemi()
                if problemi:
                    raise RicettaNonValida(problemi)
                try:
                    el = xml_fvg.richiesta_interroga_nre(criteri, p["cf_medico"])
                except ValueError as e:
                    raise RicettaNonValida([str(e)]) from e
        except RicettaNonValida as e:
            return _esito_rifiuto(op, atteso, str(e))
        return _esito_codifica(op, atteso, el, _errori_schema(self._schema_fvg(servizio), el),
                               "XSD del SAR FVG non forniti (--xsd-fvg o $VARCO_XSD_FVG, cartella wsdl/sar): "
                               "gli schemi di Insiel non stanno nel repository")

    # ------------------------------------------------------------------ SIRPED (Regione Piemonte)

    def _xsd_a2f(self):
        if self.xsd_a2f is None:
            return None
        if self._schema_a2f is None:
            from lxml import etree

            self._schema_a2f = etree.XMLSchema(etree.parse(str(self.xsd_a2f / "sts-a2f-service.v0.1.xsd")))
        return self._schema_a2f

    def _passo_piemonte(self, op: str, p: dict) -> EsitoPasso:
        from . import piemonte as pie

        atteso = p.get("atteso", {})
        try:
            if op == "codifica_piemonte":
                return self._codifica_piemonte(p)
            if op == "leggi_piemonte_a2f":
                xml = self.cartella.joinpath("risposte", p["risposta"]).read_bytes()
                oss = pie.osserva_a2f(xml)
            elif op == "intestazioni_piemonte":
                oss = pie.osserva_intestazioni(p)
            elif op == "pkce_piemonte":
                oss = pie.osserva_pkce(p)
            else:
                oss = pie.osserva_jwt(self.cartella, p)
        except pie.RifiutoLocale as e:
            return _esito_rifiuto(op, atteso, str(e))
        ko = _verifica_rifiuto(atteso, oss)
        if ko is None:
            ko = pie.verifica_piemonte(atteso, oss)
        return EsitoPasso(op, not ko, ko, oss)

    def _codifica_piemonte(self, p: dict) -> EsitoPasso:
        from . import piemonte as pie

        op, atteso = "codifica_piemonte", p.get("atteso", {})
        try:
            el, a2f = pie.codifica(p)
        except pie.RifiutoLocale as e:
            return _esito_rifiuto(op, atteso, str(e))
        if a2f:
            problemi = _errori_schema(self._xsd_a2f(), el)
        else:
            from ..schemi import errori_xsd

            problemi = errori_xsd(el)
        return _esito_codifica(op, atteso, el, problemi,
                               "XSD del kit A2F del Sistema TS non forniti (--xsd-a2f o $VARCO_XSD_A2F, "
                               "cartella wsdl): non stanno nel repository")

    def _passo_fse(self, op: str, p: dict) -> EsitoPasso:
        atteso = p.get("atteso", {})
        if op == "valida_documento":
            xml = self.cartella.joinpath(p["documento"]).read_bytes()
        else:
            dati = p["dati"]
            if isinstance(dati, str):
                dati = json.loads(self.cartella.joinpath(dati).read_text(encoding="utf-8"))
            generatore = self.generatore_pss or genera_pss_kit
            try:
                xml = generatore(dati)
            except RicettaNonValida as e:  # pragma: no cover - le implementazioni usano la loro eccezione
                return self._fse_rifiuto(op, atteso, str(e))
            except Exception as e:
                from ..fse.modello import DocumentoNonValido

                if isinstance(e, DocumentoNonValido):
                    return self._fse_rifiuto(op, atteso, str(e))
                raise
        try:
            esito = self.validatore_fse.valida(xml)
        except Exception as e:
            from ..fse.validazione import SchemiNonTrovati

            if isinstance(e, SchemiNonTrovati):
                # prerequisito mancante, non un errore dell'implementazione: SALTATO, come nel percorso
                # automatico (revisione esterna giro 3, conformità n. 3)
                return EsitoPasso(op, False, [], {}, saltato=f"schemi HL7 assenti: {e}")
            raise
        oss = {
            "esito": esito.esito,
            "errori": esito.errori,
            "avvisi": esito.avvisi,
            "vocabolario_verificato": esito.vocabolario_verificato,
            "validatore": esito.validatore,
        }
        ko = verifica_documento(atteso, oss)
        if not esito.vocabolario_verificato:
            if _verdetto_dipende_dai_vocabolari(atteso, oss):
                # Senza vocabolari non si può dire né sì né no: niente verde e niente rosso finti.
                return EsitoPasso(op, False, ko, oss, saltato="il verdetto dipende dai vocabolari, che questo validatore "
                                                             "non controlla: serve il validatore ufficiale "
                                                             "(strumenti/validatore-ufficiale)")
            oss["nota"] = "vocabolari non verificati da questo validatore"
        return EsitoPasso(op, not ko, ko, oss)

    @staticmethod
    def _fse_rifiuto(op: str, atteso: dict, motivo: str) -> EsitoPasso:
        oss = {"rifiuto_locale": motivo}
        ko = verifica_documento(atteso, oss)
        return EsitoPasso(op, not ko, ko or [motivo], oss)


def _attributo(radice, chiave: str) -> str | None:
    """'percorso/di/nomi/locali@attributo' sul primo elemento che corrisponde (percorso vuoto = radice).
    L'attributo si cerca per nome locale: `@prodottoCme` trova anche `tip:prodottoCme` (SAR FVG)."""
    percorso, _, attributo = chiave.rpartition("@")
    el = radice
    for nome in filter(None, percorso.split("/")):
        el = next((c for c in el if c.tag.rsplit("}", 1)[-1] == nome), None)
        if el is None:
            return None
    if attributo in el.attrib:
        return el.get(attributo)
    return next((v for k, v in el.attrib.items() if k.rsplit("}", 1)[-1] == attributo), None)


def _errori_schema(schema, el) -> list[str] | None:
    """Errori XSD dell'elemento; None se lo schema non è disponibile."""
    if schema is None:
        return None
    import xml.etree.ElementTree as ET

    from lxml import etree

    if schema.validate(etree.fromstring(ET.tostring(el))):
        return []
    return [f"riga {e.line}: {e.message}" for e in schema.error_log]


def _esito_rifiuto(op: str, atteso: dict, motivo: str) -> EsitoPasso:
    """L'implementazione ha rifiutato prima di codificare: soddisfa solo rifiuto_locale, e da solo."""
    oss = {"rifiuto_locale": motivo}
    ko = _verifica_rifiuto(atteso, oss) or []
    return EsitoPasso(op, not ko, ko, oss)


def _esito_codifica(op: str, atteso: dict, el, problemi_xsd: list[str] | None, senza_schema: str = "") -> EsitoPasso:
    """Confronto di TUTTE le aspettative di codifica (attesoCodifica) su una richiesta prodotta.

    problemi_xsd None = schema non disponibile: se un'altra aspettativa è già violata il rosso è
    certo (FALLITO), altrimenti SALTATO, mai SUPERATO. xsd_valido:false vuol dire «mi aspetto
    che NON validi», non «salta lo XSD»."""
    if atteso.get("rifiuto_locale"):
        return EsitoPasso(op, False, ["atteso un rifiuto locale, la richiesta è stata codificata"])
    valori = {e.tag.rsplit("}", 1)[-1]: (e.text or "") for e in el}
    oss = {"tag": valori}
    ko: list[str] = []
    for tag, val in atteso.get("tag", {}).items():
        if tag not in valori:
            ko.append(f"<{tag}> assente, atteso {val!r}")
        elif valori[tag] != val:
            ko.append(f"<{tag}> = {valori[tag]!r} invece di {val!r}")
    for tag in atteso.get("tag_assenti", []):
        if tag in valori:
            ko.append(f"<{tag}> presente, doveva mancare")
    for chiave, val in atteso.get("attributi", {}).items():
        trovato = _attributo(el, chiave)
        if trovato != val:
            ko.append(f"{chiave} = {trovato!r} invece di {val!r}")
    for percorso, val in atteso.get("testi", {}).items():
        trovato = testo_in(el, percorso)
        if trovato != val:
            ko.append(f"{percorso} = {trovato!r} invece di {val!r}")
    if problemi_xsd is None:
        if ko:
            return EsitoPasso(op, False, ko, oss)
        return EsitoPasso(op, False, [], oss, saltato=senza_schema or "schema XSD non disponibile")
    if atteso.get("xsd_valido", True):
        ko += problemi_xsd
    elif not problemi_xsd:
        ko.append("atteso NON valido secondo lo XSD (xsd_valido: false), invece la richiesta è valida")
    return EsitoPasso(op, not ko, ko, oss)


def _verdetto_dipende_dai_vocabolari(atteso: dict, oss: dict) -> bool:
    """True se un validatore che controlla i vocabolari potrebbe dare il verdetto opposto.

    Come nel gateway, i vocabolari si controllano solo dopo uno schematron OK o con soli avvisi:
    con un esito osservato OK/SEMANTIC_WARNING, l'esito vero può essere anche VOCABULARY_ERROR
    (con un errore in più, di testo non noto). Se il confronto cambia fra le due possibilità,
    il verdetto non si può dare: né verde (es. esito OK atteso) né rosso (es. VOCABULARY_ERROR atteso)."""
    if oss.get("esito") not in _ESITI_FSE_VALIDI:
        return False  # schema o schematron già falliti: i vocabolari non si guarderebbero comunque
    alternativo = dict(oss, esito="VOCABULARY_ERROR", errori=list(oss.get("errori", [])) + ["(errore dei vocabolari)"])
    atteso_alternativo = {k: v for k, v in atteso.items() if k != "errori_contengono"}  # testo dell'errore non noto
    return bool(verifica_documento(atteso, oss)) != bool(verifica_documento(atteso_alternativo, alternativo))


def genera_pss_kit(dati: dict) -> bytes:
    """Generatore di riferimento: questo kit."""
    from ..fse.cda_pss import genera_xml
    from ..fse.json import pss_da_dict

    return genera_xml(pss_da_dict(dati))


def validatore_fse_predefinito(preferenza: str = "auto"):
    """'ufficiale' se preparato (strumenti/validatore-ufficiale), altrimenti 'locale' se ci sono lxml e
    saxonche e gli schemi HL7 scaricati (strumenti/scarica_specifiche.py --gruppi cda-xsd)."""
    from ..fse import validazione as v

    if preferenza in ("auto", "ufficiale") and v.validatore_ufficiale_pronto():
        return v.ValidatoreUfficiale()
    if preferenza == "ufficiale":
        return None
    if preferenza in ("auto", "locale") and v.dipendenze_locali_presenti() and v.schemi_locali_presenti():
        return v.ValidatoreLocale()
    return None


def rapporto_json(risultati: list[EsitoCaso]) -> dict:
    return {
        "totale": len(risultati),
        "superati": sum(r.stato == "SUPERATO" for r in risultati),
        "falliti": sum(r.stato == "FALLITO" for r in risultati),
        "errori": sum(r.stato == "ERRORE" for r in risultati),
        "saltati": sum(r.stato == "SALTATO" for r in risultati),
        "casi": [
            {
                "id": r.id,
                "titolo": r.titolo,
                "stato": r.stato,
                "motivo": r.motivo,
                "passi": [
                    {"operazione": p.operazione, "superato": p.superato, "dettagli": p.dettagli, "osservato": p.osservato}
                    for p in r.passi
                ],
            }
            for r in risultati
        ],
    }
