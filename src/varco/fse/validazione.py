# SPDX-License-Identifier: EUPL-1.2
"""Validazione dei CDA2 contro schemi e schematron UFFICIALI del gateway FSE 2.0.

Due validatori con lo stesso contratto (`valida(xml) -> EsitoValidazione`):

- `ValidatoreLocale`: XSD (lxml) + schematron (Saxon via saxonche), con gli stessi
  file che il gateway carica nel suo database (it-fse-catalogs) e la stessa
  pipeline ISO Schematron XSLT2 di ph-schematron, la libreria del validatore
  ufficiale. Non fa il controllo dei vocabolari.
- `ValidatoreUfficiale`: esegue il CODICE del microservizio it-fse-gtw-validator
  (Java) tramite strumenti/validatore-ufficiale, con i dump pubblici del suo
  database. Fa XSD, schematron e vocabolari, nello stesso ordine del gateway.

L'esito usa gli stessi nomi del gateway (RawValidationEnum):
OK, SYNTAX_ERROR, SEMANTIC_ERROR, SEMANTIC_WARNING, VOCABULARY_ERROR.

Dipendenze facoltative: lxml e saxonche (extra "fse").

Schemi HL7: gli XSD del CDA R2 e lo schematron del PSS NON sono nel pacchetto (la HL7 IP
Policy e il copyright di HL7 Italia non ne permettono la redistribuzione: docs/TERZE_PARTI.md).
`ValidatoreLocale` li legge dalla copia scaricata dalla fonte ufficiale con
`python strumenti/scarica_specifiche.py --gruppi cda-xsd`:

- XSD: cartella `$VARCO_CDA_XSD`, altrimenti `fse/cda-xsd/` nella cartella dello script;
- schematron: cartella `$VARCO_SCHEMATRON`, altrimenti `fse/schematron/` nella stessa.

Se mancano, `SchemiNonTrovati` dice quali file e come scaricarli.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Protocol

from ..ambiente import leggi

ESITI = ("OK", "SYNTAX_ERROR", "SEMANTIC_ERROR", "SEMANTIC_WARNING", "VOCABULARY_ERROR")

NS_SVRL = "http://purl.oclc.org/dsdl/svrl"

# templateId root del documento -> schematron da usare (come la collezione "schematron" del gateway)
SCHEMATRON_PER_TEMPLATE = {
    "2.16.840.1.113883.2.9.10.1.4.1.1": "schematron_PSS_v4.0.sch",  # Profilo Sanitario Sintetico
}


@dataclass
class EsitoValidazione:
    esito: str  # uno di ESITI
    errori: list[str] = field(default_factory=list)  # XSD o assert schematron falliti
    avvisi: list[str] = field(default_factory=list)  # report schematron (W00x)
    vocabolario_verificato: bool = False
    messaggio_vocabolario: str | None = None
    # Sistemi di codifica presenti nel documento che il validatore ufficiale NON conosce, quindi non
    # controlla (es. gruppi di equivalenza, esenzioni): i loro codici passano qualunque essi siano.
    sistemi_non_verificati: list[str] = field(default_factory=list)
    schematron: str | None = None
    validatore: str = ""
    dettagli: dict = field(default_factory=dict)

    @property
    def valido(self) -> bool:
        return self.esito in ("OK", "SEMANTIC_WARNING")

    def a_dict(self) -> dict:
        return {
            "esito": self.esito,
            "errori": self.errori,
            "avvisi": self.avvisi,
            "vocabolario_verificato": self.vocabolario_verificato,
            "messaggio_vocabolario": self.messaggio_vocabolario,
            "sistemi_non_verificati": self.sistemi_non_verificati,
            "schematron": self.schematron,
            "validatore": self.validatore,
        }


# Nomi leggibili per gli avvisi sui sistemi di codifica non verificati
NOMI_SISTEMI = {
    "2.16.840.1.113883.2.9.6.1.51": "gruppi di equivalenza",
    "2.16.840.1.113883.2.9.6.1.22": "esenzioni",
    "2.16.840.1.113883.2.9.6.1.5": "AIC",
    "2.16.840.1.113883.6.73": "ATC",
    "2.16.840.1.113883.6.103": "ICD-9-CM",
    "2.16.840.1.113883.5.112": "RouteOfAdministration",
}


class ValidatoreCDA(Protocol):
    def valida(self, xml: bytes) -> EsitoValidazione: ...


# ---------------------------------------------------------------------- locale


def dipendenze_locali_presenti() -> bool:
    try:
        import lxml.etree  # noqa: F401
        import saxonche  # noqa: F401
    except ImportError:
        return False
    return True


# Gli 11 XSD del CDA R2 che CDA.xsd carica (gruppo cda-xsd di strumenti/fonti_specifiche.json)
XSD_CDA = (
    "CDA.xsd", "POCD_MT000040UV02.xsd", "NarrativeBlock.xsd", "datatypes.xsd", "datatypes-base.xsd",
    "datatypes-rX-cs.xsd", "infrastructureRoot.xsd", "voc.xsd", "sdtcExtension.xsd", "pharmExtension.xsd",
    "labExtension_1.2_gen.xsd",
)
VAR_XSD = "VARCO_CDA_XSD"
VAR_SCHEMATRON = "VARCO_SCHEMATRON"
COME_SCARICARE = (
    "Gli schemi HL7 non sono nel pacchetto (licenza: docs/TERZE_PARTI.md). Scaricali dalla fonte "
    "ufficiale, dalla radice del repository, con: python strumenti/scarica_specifiche.py --gruppi cda-xsd "
    f"oppure imposta ${VAR_XSD} (cartella degli XSD) e ${VAR_SCHEMATRON} (cartella dello schematron)."
)


class SchemiNonTrovati(FileNotFoundError):
    """Mancano gli XSD del CDA o lo schematron del PSS: il messaggio dice come scaricarli."""


def _cartella_scaricata(sottocartella: str) -> Path | None:
    """Dove li mette strumenti/scarica_specifiche.py: <radice>/<CARTELLA_PREDEFINITA>/fse/<sottocartella>.

    Il nome della cartella si legge dallo script (costante CARTELLA_PREDEFINITA), così src/ non ne
    ripete il percorso. Senza lo script accanto (pacchetto installato fuori dal repository) non c'è
    un default: servono le variabili d'ambiente."""
    radice = Path(__file__).resolve().parents[3]
    script = radice / "strumenti" / "scarica_specifiche.py"
    try:
        m = re.search(r'^CARTELLA_PREDEFINITA = "([^"/\\]+)"$', script.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return radice / m.group(1) / "fse" / sottocartella if m else None


def cartella_xsd_cda() -> Path | None:
    v = os.environ.get(VAR_XSD)
    return Path(v) if v else _cartella_scaricata("cda-xsd")


def cartella_schematron() -> Path | None:
    v = os.environ.get(VAR_SCHEMATRON)
    return Path(v) if v else _cartella_scaricata("schematron")


def _richiedi(cartella: Path | None, nomi, variabile: str) -> Path:
    if cartella is None:
        raise SchemiNonTrovati(
            f"Cartella degli schemi HL7 non impostata (${variabile}) e strumenti/scarica_specifiche.py "
            f"non trovato accanto al pacchetto. {COME_SCARICARE}"
        )
    mancanti = [n for n in nomi if not (cartella / n).is_file()]
    if mancanti:
        raise SchemiNonTrovati(
            f"Schemi HL7 mancanti in {cartella} (${variabile}): {', '.join(mancanti)}. {COME_SCARICARE}"
        )
    return cartella


def schemi_locali_presenti() -> bool:
    """True se ci sono tutti gli XSD del CDA e gli schematron che ValidatoreLocale usa."""
    try:
        _richiedi(cartella_xsd_cda(), XSD_CDA, VAR_XSD)
        _richiedi(cartella_schematron(), sorted(set(SCHEMATRON_PER_TEMPLATE.values())), VAR_SCHEMATRON)
    except SchemiNonTrovati:
        return False
    return True


@lru_cache(maxsize=None)
def _schema_cda_in(cartella: Path):
    from lxml import etree

    class PerNomeFile(etree.Resolver):
        # Come il ResourceResolver del gateway: si risolve per nome del file, ignorando
        # le cartelle (CDA.xsd include "./coreschemas/POCD_MT000040UV02.xsd").
        def resolve(self, url, pubid, context):
            return self.resolve_filename(str(cartella / os.path.basename(url)), context)

    parser = etree.XMLParser()
    parser.resolvers.add(PerNomeFile())
    return etree.XMLSchema(etree.parse(str(cartella / "CDA.xsd"), parser))


def _schema_cda():
    """Schema CDA R2 compilato dalla copia scaricata (SchemiNonTrovati se manca)."""
    return _schema_cda_in(_richiedi(cartella_xsd_cda(), XSD_CDA, VAR_XSD).resolve())


class _Schematron:
    """ISO Schematron -> XSLT2 con lo scheletro del 2010-04-14 (lo stesso di ph-schematron 5.6.5)."""

    def __init__(self, sch: Path):
        from saxonche import PySaxonProcessor

        self.proc = PySaxonProcessor(license=False)
        xslt = self.proc.new_xslt30_processor()
        scheletro = Path(str(resources.files("varco.fse").joinpath("iso_schematron")))
        passo = self.proc.parse_xml(xml_file_name=str(sch))
        for nome in ("iso_dsdl_include.xsl", "iso_abstract_expand.xsl", "iso_svrl_for_xslt2.xsl"):
            esec = xslt.compile_stylesheet(stylesheet_file=str(scheletro / nome))
            esec.set_cwd(str(sch.parent))
            passo = esec.transform_to_value(xdm_node=passo).head
        self.validatore = xslt.compile_stylesheet(stylesheet_node=passo)

    def svrl(self, xml: bytes) -> bytes:
        # Il file si CHIUDE prima che Saxon lo legga: su Windows un NamedTemporaryFile ancora aperto
        # non si riapre («I/O error reported by XML parser»). I byte restano quelli ricevuti, così la
        # dichiarazione di encoding del documento vale come per lxml.
        with tempfile.TemporaryDirectory(prefix="varco-sch-") as cartella:
            percorso = Path(cartella) / "documento.xml"
            percorso.write_bytes(xml)
            nodo = self.proc.parse_xml(xml_file_name=str(percorso))
            return self.validatore.transform_to_string(xdm_node=nodo).encode("utf-8")


@lru_cache(maxsize=None)
def _schematron_in(percorso: Path) -> _Schematron:
    return _Schematron(percorso)


def _schematron(nome: str) -> _Schematron:
    cartella = _richiedi(cartella_schematron(), [nome], VAR_SCHEMATRON)
    return _schematron_in((cartella / nome).resolve())


def _testo(el) -> str:
    return " ".join("".join(el.itertext()).split())


class ValidatoreLocale:
    """XSD + schematron ufficiali, senza vocabolari."""

    nome = "locale (lxml + saxonche, XSD e schematron ufficiali)"

    def valida(self, xml: bytes) -> EsitoValidazione:
        from lxml import etree

        try:
            doc = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
        except etree.XMLSyntaxError as e:
            return EsitoValidazione("SYNTAX_ERROR", [f"XML non ben formato: {e}"], validatore=self.nome)
        schema = _schema_cda()
        if not schema.validate(doc):
            errori = [f"ERROR: riga {e.line}: {e.message}" for e in schema.error_log]
            return EsitoValidazione("SYNTAX_ERROR", errori, validatore=self.nome)
        ns = {"h": "urn:hl7-org:v3"}
        radici = [t.get("root") for t in doc.iterfind(".//h:templateId", ns)]
        nome_sch = next((SCHEMATRON_PER_TEMPLATE[r] for r in radici if r in SCHEMATRON_PER_TEMPLATE), None)
        if nome_sch is None:
            return EsitoValidazione(
                "SEMANTIC_ERROR", [f"Schematron with template id roots {radici} not found"], validatore=self.nome
            )
        svrl = etree.fromstring(_schematron(nome_sch).svrl(xml))
        errori = [_testo(e.find(f"{{{NS_SVRL}}}text")) for e in svrl.iter(f"{{{NS_SVRL}}}failed-assert")]
        avvisi = [_testo(e.find(f"{{{NS_SVRL}}}text")) for e in svrl.iter(f"{{{NS_SVRL}}}successful-report")]
        esito = "SEMANTIC_ERROR" if errori else ("SEMANTIC_WARNING" if avvisi else "OK")
        return EsitoValidazione(esito, errori, avvisi, schematron=nome_sch, validatore=self.nome)


# ---------------------------------------------------------------------- ufficiale


def cartella_validatore_ufficiale() -> Path:
    base = leggi("VARCO_VALIDATORE_UFFICIALE")
    if base:
        return Path(base)
    return Path(__file__).resolve().parents[3] / "strumenti" / "validatore-ufficiale"


def validatore_ufficiale_pronto() -> bool:
    c = cartella_validatore_ufficiale()
    return (c / "classi" / "ValidatoreUfficiale.class").exists() and bool(os.environ.get("JAVA_HOME"))


class ValidatoreUfficiale:
    """Il codice del gateway (it-fse-gtw-validator), eseguito in locale. Vedi strumenti/validatore-ufficiale."""

    nome = "ufficiale (codice it-fse-gtw-validator, dump it-fse-catalogs)"

    def valida_file(self, percorsi: list[Path]) -> list[EsitoValidazione]:
        script = cartella_validatore_ufficiale() / "valida.sh"
        esecuzione = subprocess.run(
            [str(script), *[str(p) for p in percorsi]], capture_output=True, text=True, timeout=900
        )
        righe = [r[len("RISULTATO "):] for r in esecuzione.stdout.splitlines() if r.startswith("RISULTATO ")]
        if len(righe) != len(percorsi):
            raise RuntimeError(
                f"validatore ufficiale: attesi {len(percorsi)} risultati, arrivati {len(righe)}\n{esecuzione.stderr[-2000:]}"
            )
        return [self._esito(json.loads(r)) for r in righe]

    def valida(self, xml: bytes) -> EsitoValidazione:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "documento.xml"
            p.write_bytes(xml)
            return self.valida_file([p])[0]

    @staticmethod
    def _sistemi_ignoti(log_avvisi: list[str]) -> list[str]:
        """OID dalla riga del validatore "Unknown CodeSystems found during the validation: [a, b]"."""
        out: list[str] = []
        for riga in log_avvisi:
            m = re.search(r"Unknown CodeSystems found during the validation:\s*\[([^\]]*)\]", riga)
            if m:
                out += [x.strip() for x in m.group(1).split(",") if x.strip() and x.strip() not in out]
        return out

    @staticmethod
    def _testo(t: str) -> str:
        # ph-schematron restituisce il testo come lista Java: "[ERRORE-1| ...]"
        t = " ".join(t.split())
        return t[1:-1] if t.startswith("[") and t.endswith("]") else t

    def _esito(self, d: dict) -> EsitoValidazione:
        sch = d.get("schematron") or {}
        voc = d.get("vocabolario")
        errori = [self._testo(e["testo"]) for e in sch.get("errori", [])]
        if d.get("esito") == "SYNTAX_ERROR":
            errori = list(d.get("messaggi", []))
        elif d.get("esito") == "VOCABULARY_ERROR":
            errori = list(d.get("messaggi", []))
        avvisi = [self._testo(e["testo"]) for e in sch.get("avvisi", [])]
        ignoti = self._sistemi_ignoti(d.get("log_avvisi", []))
        if ignoti:
            # L'esito resta quello del gateway (OK resta OK), ma chi legge l'esito pubblico deve sapere
            # che questi codici non sono stati controllati: un codice inventato passerebbe.
            avvisi.append(
                "VOCABOLARIO NON VERIFICATO: il validatore ufficiale non conosce i sistemi di codifica "
                + ", ".join(f"{o} ({NOMI_SISTEMI.get(o, 'sistema non censito')})" for o in ignoti)
                + ": i codici di questi sistemi non sono stati controllati")
        return EsitoValidazione(
            esito=d.get("esito", "ECCEZIONE"),
            errori=errori,
            avvisi=avvisi,
            vocabolario_verificato=voc is not None,
            messaggio_vocabolario=(voc or {}).get("messaggio"),
            sistemi_non_verificati=ignoti,
            schematron=f"v{sch.get('versione')}" if sch else None,
            validatore=self.nome,
            dettagli=d,
        )
