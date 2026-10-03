# SPDX-License-Identifier: EUPL-1.2
"""PDF del documento clinico con il CDA2 iniettato, e firma PAdES.

Il gateway FSE 2.0 riceve un PDF con dentro il CDA2 (it-fse-support, FAQ "In che
formato sono i documenti?"). Modalità ATTACHMENT: il CDA è un file allegato di
nome "cda.xml" nel name tree /EmbeddedFiles del catalogo, ed è lì che lo cerca il
dispatcher (PDFUtility.extractContentFromAttachments, cda.attachment.name=cda.xml).

Tre funzioni, separate perché fanno cose diverse:

- `pdf_con_cda(testo, cda)`: un PDF leggibile e minimale scritto con la sola
  libreria standard, con il CDA già allegato;
- `inietta_cda(pdf, cda)`: allega il CDA a un PDF che esiste già (quello del
  gestionale), con un aggiornamento incrementale. Richiede pyHanko;
- `firma_pades(pdf, firmatario)`: firma PAdES (baseline B-B) sul PDF. Richiede pyHanko.

La firma vera è quella del medico (smart card o firma remota): qui c'è solo
l'interfaccia `Firmatario` e un firmatario da file PKCS#12. Per le prove il kit
genera un certificato AUTOFIRMATO DI TEST (`crea_certificato_di_test`), che non
ha alcun valore legale e lo dichiara nel proprio nome.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import re
import textwrap
from dataclasses import dataclass
from typing import Protocol

NOME_ALLEGATO = "cda.xml"


# ------------------------------------------------------------------ PDF minimale (stdlib)


class TestoNonRappresentabile(ValueError):
    """Il PDF minimale usa Helvetica WinAnsi (cp1252): un carattere fuori da lì sarebbe sostituito
    da "?" nel testo leggibile. Il kit non altera il testo: rifiuta."""


def _stringa_pdf(s: str) -> bytes:
    try:
        b = s.encode("cp1252")
    except UnicodeEncodeError as e:
        raise TestoNonRappresentabile(
            f"carattere {s[e.start:e.end]!r} non rappresentabile nel PDF minimale (cp1252) in {s!r}: "
            "usare il PDF del gestionale e inietta_cda()") from e
    return b"(" + b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def pdf_con_cda(righe: list[str], cda: bytes | None, *, titolo: str = "Documento clinico", nome_allegato: str = NOME_ALLEGATO) -> bytes:
    """PDF di testo (Helvetica, A4) con `cda` allegato come file `nome_allegato` (None = senza allegato).

    Allegato con /AFRelationship /Source, dichiarato sia in /Names/EmbeddedFiles sia in /AF
    del catalogo (come raccomanda PDF/A-3 per i file associati). Il PDF NON è dichiarato PDF/A.
    """
    testo: list[str] = []
    for r in righe:
        # niente a capo dentro codici come "no-known-allergies": il testo estratto deve restare confrontabile
        testo.extend(textwrap.wrap(r, 95, break_on_hyphens=False) or [""])
    per_pagina = 60
    pagine = [testo[i:i + per_pagina] for i in range(0, len(testo), per_pagina)] or [[]]

    oggetti: list[bytes] = []

    def nuovo(contenuto: bytes) -> int:
        oggetti.append(contenuto)
        return len(oggetti)

    catalogo = nuovo(b"")  # riempito dopo
    radice_pagine = nuovo(b"")
    font = nuovo(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids = []
    for pagina in pagine:
        flusso = io.BytesIO()
        flusso.write(b"BT /F1 9 Tf 12 TL 50 800 Td\n")
        for riga in pagina:
            flusso.write(_stringa_pdf(riga) + b" Tj T*\n")
        flusso.write(b"ET\n")
        dati = flusso.getvalue()
        contenuto = nuovo(b"<< /Length %d >>\nstream\n" % len(dati) + dati + b"\nendstream")
        kids.append(nuovo(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (radice_pagine, font, contenuto)
        ))
    oggetti[radice_pagine - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids))

    adesso = _dt.datetime.now(_dt.timezone.utc).strftime("D:%Y%m%d%H%M%SZ").encode()
    info = None
    if cda is None:
        info = nuovo(b"<< /Title %s /Producer (varco) /CreationDate (%s) >>" % (_stringa_pdf(titolo), adesso))
        oggetti[catalogo - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % radice_pagine
        return _scrivi(oggetti, catalogo, info, hashlib.md5(adesso).hexdigest().encode())
    allegato = nuovo(
        b"<< /Type /EmbeddedFile /Subtype /text#2Fxml /Length %d /Params << /Size %d /ModDate (%s) /CheckSum <%s> >> >>\nstream\n"
        % (len(cda), len(cda), adesso, hashlib.md5(cda).hexdigest().encode())
        + cda + b"\nendstream"
    )
    nome = _stringa_pdf(nome_allegato)
    filespec = nuovo(
        b"<< /Type /Filespec /F %s /UF %s /Desc (CDA2) /AFRelationship /Source /EF << /F %d 0 R /UF %d 0 R >> >>"
        % (nome, nome, allegato, allegato)
    )
    info = nuovo(b"<< /Title %s /Producer (varco) /CreationDate (%s) >>" % (_stringa_pdf(titolo), adesso))
    oggetti[catalogo - 1] = (
        b"<< /Type /Catalog /Pages %d 0 R /Names << /EmbeddedFiles << /Names [%s %d 0 R] >> >> /AF [%d 0 R] >>"
        % (radice_pagine, nome, filespec, filespec)
    )

    return _scrivi(oggetti, catalogo, info, hashlib.md5(cda + adesso).hexdigest().encode())


def _scrivi(oggetti: list[bytes], catalogo: int, info: int, ident: bytes) -> bytes:
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    posizioni = []
    for i, contenuto in enumerate(oggetti, 1):
        posizioni.append(out.tell())
        out.write(b"%d 0 obj\n" % i + contenuto + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(oggetti) + 1))
    for pos in posizioni:
        out.write(b"%010d 00000 n \n" % pos)
    out.write(
        b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R /ID [<%s> <%s>] >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(oggetti) + 1, catalogo, info, ident, ident, xref)
    )
    return out.getvalue()


_STATI = {"active": "attivo", "completed": "concluso", "suspended": "sospeso", "aborted": "interrotto"}
_RISERVATEZZA = {"N": "normale", "R": "riservato", "V": "molto riservato"}


def _d(x) -> str:
    return x.isoformat() if x else "non noto"


def _stato(v) -> str:
    if v.stato is None:
        return "stato non indicato"
    s = f"stato: {_STATI[v.stato.value]} ({v.stato.value})"
    return s + (f", fine {_d(v.fine)}" if v.fine else "")


def _indirizzo(ind) -> str:
    if ind is None:
        return "non indicato"
    parti = [ind.via, ind.cap, ind.comune, f"({ind.provincia})" if ind.provincia else None,
             f"ISTAT comune {ind.codice_istat_comune}", f"regione {ind.codice_regione}" if ind.codice_regione else None,
             f"Stato {ind.stato}"]
    return " ".join(p for p in parti if p)


def righe_leggibili_pss(pss) -> list[str]:
    """Il contenuto del PSS in chiaro, per la parte visibile del PDF (non sostituisce il CDA).

    Riporta TUTTO quello che il CDA contiene, campo per campo: date di inizio e fine, stato,
    note, tipo di allergia, via di somministrazione, tutti i codici, versione e documento
    sostituito. Un dato assente si scrive come assente ("non noto", "non indicato"), mai
    completato. Il test che lo garantisce confronta ogni valore del modello presente nel CDA
    con il testo estratto dal PDF.
    """
    from .modello import ASSENZA_ALLERGIE, ASSENZA_PROBLEMI, ASSENZA_TERAPIE

    pz, m, c = pss.paziente, pss.autore, pss.custode
    r = [
        "PROFILO SANITARIO SINTETICO",
        f"Documento {pss.radice_id} / {pss.id_documento} - versione {pss.versione} - insieme (setId) {pss.set_id}",
    ]
    if pss.id_documento_precedente:
        r.append(f"Sostituisce il documento {pss.id_documento_precedente}")
    r += [
        f"Data: {pss.data.isoformat(sep=' ')} - riservatezza {_RISERVATEZZA.get(pss.riservatezza, '')} ({pss.riservatezza})",
        f"Paziente: {pz.cognome} {pz.nome} - CF {pz.codice_fiscale} - sesso {pz.sesso} - data di nascita {_d(pz.data_nascita)}",
        f"  luogo di nascita: {_indirizzo(pz.luogo_nascita)}",
        f"  residenza: {_indirizzo(pz.residenza)}",
        f"Medico ({m.ruolo}): {m.titolo or ''} {m.nome} {m.cognome} - CF {m.codice_fiscale}"
        f" - tel. {m.telefono or 'non indicato'} - email {m.email or 'non indicata'}",
        f"Custode: {c.nome} - id {c.id_radice} / {c.id_estensione} - tel. {c.telefono or 'non indicato'}"
        f" - indirizzo: {_indirizzo(c.indirizzo)}",
        "",
        "Allergie e intolleranze:",
    ]
    for a in pss.allergie:
        codice = f"{a.agente_sistema} {a.agente_codice}" if a.agente_codice else "agente non codificato"
        r.append(f"  - {a.agente} ({codice}) - tipo {a.tipo or 'non indicato'} - inizio {_d(a.inizio)} - {_stato(a)}"
                 + (f" - note: {a.note}" if a.note else ""))
    if pss.allergie_assenti:
        r.append(f"  - {ASSENZA_ALLERGIE.get(pss.allergie_assenti, '')} ({pss.allergie_assenti})")
    r += ["", "Terapie farmacologiche:"]
    for t in pss.terapie:
        codici = ", ".join(f"{n} {v}" for n, v in (("AIC", t.codice_aic), ("ATC", t.codice_atc),
                                                    ("GE", t.codice_gruppo_equivalenza)) if v)
        r.append(f"  - {t.descrizione} ({codici}) - via {t.via or 'non indicata'} - inizio {_d(t.inizio)} - {_stato(t)}")
    if pss.terapie_assenti:
        r.append(f"  - {ASSENZA_TERAPIE.get(pss.terapie_assenti, '')} ({pss.terapie_assenti})")
    r += ["", "Lista dei problemi:"]
    for p in pss.problemi:
        r.append(f"  - {p.descrizione} (ICD-9-CM {p.codice_icd9 or 'non codificato'}) - inizio {_d(p.inizio)} - {_stato(p)}")
    if pss.problemi_assenti:
        r.append(f"  - {ASSENZA_PROBLEMI.get(pss.problemi_assenti, '')} ({pss.problemi_assenti})")
    r += ["", "Anamnesi familiare:"]
    for v in pss.anamnesi_familiare:
        r.append(f"  - {v.parentela}" + (f" (sesso {v.sesso})" if v.sesso else "")
                 + f": {v.descrizione} (ICD-9-CM {v.codice_icd9 or 'non codificato'})"
                 + (f" - età di insorgenza {v.eta_insorgenza} anni" if v.eta_insorgenza is not None else ""))
    if pss.anamnesi_familiare_assente:
        r.append(f"  - {ASSENZA_PROBLEMI.get(pss.anamnesi_familiare_assente, '')} ({pss.anamnesi_familiare_assente})")
    if pss.esenzioni:
        r += ["", "Esenzioni:"]
        for e in pss.esenzioni:
            r.append(f"  - {e.codice}" + (f" {e.descrizione}" if e.descrizione else "") + f" - inizio {_d(e.inizio)} - {_stato(e)}")
    r += ["", "Il documento strutturato (CDA2 HL7 Italia) è allegato a questo PDF come cda.xml."]
    # Il testo è scritto PER il CDA di questo stesso PSS: l'impronta lo lega al contenuto, non solo a id e
    # versione. inietta_cda la confronta prima di allegare (revisione esterna giro 3, FSE G3-N1).
    try:
        from .cda_pss import genera_xml

        r.append(f"{_ETICHETTA_IMPRONTA} {impronta_contenuto_cda(genera_xml(pss))}")
    except Exception:  # noqa: BLE001 - PSS che non genera un CDA: niente impronta, nessun CDA sarà accettato
        pass
    return r


# ------------------------------------------------------------------ pyHanko


def _pyhanko():
    try:
        import pyhanko  # noqa: F401
    except ImportError as e:
        raise ImportError("Serve pyHanko (pip install 'varco[firma]')") from e


class CdaGiaPresente(ValueError):
    """Il PDF contiene già un allegato CDA: aggiungerne un secondo lascerebbe al destinatario la
    scelta di quale leggere (il kit leggeva il primo, il dispatcher ufficiale l'ultimo)."""


class AllegatiCdaAmbigui(ValueError):
    """Più allegati che il dispatcher potrebbe prendere per il CDA: non si sa quale acquisirà."""


class PdfNonEstraibile(ValueError):
    """Il dispatcher ufficiale non riuscirebbe a leggere gli allegati del PDF (per esempio un allegato
    qualunque senza /EF/F: NullPointerException, e l'estrazione intera fallisce), quindi non
    troverebbe il CDA anche se c'è."""


class TestoPdfIncoerente(ValueError):
    """Il testo leggibile del PDF descrive un documento (id o versione) diverso dal CDA allegato."""

    __test__ = False  # non è una classe di test (pytest raccoglie i nomi che iniziano con "Test")


def _ogg(x):
    return x.get_object() if hasattr(x, "get_object") else x


# Il nome del file come lo legge il dispatcher: PDComplexFileSpecification.getFilename() di PDFBox
# 2.0.26 (la versione nel classpath di it-fse-gtw-dispatcher, target/cp.txt), verificata con
# `javap -c`: il primo non nullo tra /UF, /DOS, /Mac, /Unix, /F, e solo se è una stringa PDF
# (COSDictionary.getString restituisce null per ogni altro tipo).
PRIORITA_NOME_FILE = ("/UF", "/DOS", "/Mac", "/Unix", "/F")


def _stringa_o_none(v) -> str | None:
    from pyhanko.pdf_utils import generic

    v = _ogg(v)
    if isinstance(v, generic.TextStringObject):
        return str(v)
    if isinstance(v, generic.ByteStringObject):
        return bytes(v).decode("latin-1")
    return None


def _nomi_filespec(spec) -> list[str]:
    """Tutti i nomi del file dichiarati nel filespec (per riconoscere i candidati: in dubbio, candidato)."""
    spec = _ogg(spec)
    if not hasattr(spec, "keys"):
        return []
    return [_stringa_o_none(spec[k]) or str(_ogg(spec[k])) for k in PRIORITA_NOME_FILE if k in spec]


def _nome_file_dispatcher(spec) -> str | None:
    """getFilename(): il primo nome-stringa nell'ordine di PRIORITA_NOME_FILE."""
    spec = _ogg(spec)
    for k in PRIORITA_NOME_FILE:
        n = _stringa_o_none(spec[k]) if k in spec else None
        if n is not None:
            return n
    return None


def _e_cda(chiave: str, spec, nome: str) -> bool:
    """Candidato CDA: chiave del name tree o QUALSIASI nome del filespec (/UF, /DOS, /Mac, /Unix, /F)
    uguale a `nome`, senza distinguere maiuscole. È più largo della regola del dispatcher
    (_allegati_come_dispatcher), apposta: in dubbio, candidato."""
    return any(n.casefold() == nome.casefold() for n in [chiave, *_nomi_filespec(spec)])


def _albero_allegati(root):
    if "/Names" not in root:
        return None
    nomi = _ogg(root["/Names"])
    return _ogg(nomi["/EmbeddedFiles"]) if "/EmbeddedFiles" in nomi else None


def _coppie(arr) -> list[tuple[int, str, object]]:
    arr = _ogg(arr)
    return [(i, str(_ogg(arr[i])), arr[i + 1]) for i in range(0, len(arr) - 1, 2)]


def _candidati(root, nome: str, *, come_dispatcher: bool) -> list[tuple[str, object]]:
    """Allegati che possono essere il CDA. come_dispatcher=True legge l'albero come il dispatcher
    (/Names della radice se c'è, altrimenti i /Kids di primo livello); False guarda ovunque."""
    albero = _albero_allegati(root)
    if albero is None:
        return []
    fonti = []
    if "/Names" in albero:
        fonti.append(albero["/Names"])
    if "/Kids" in albero and not (come_dispatcher and "/Names" in albero):
        fonti += [_ogg(k)["/Names"] for k in _ogg(albero["/Kids"]) if "/Names" in _ogg(k)]
    return [(chiave, spec) for arr in fonti for _, chiave, spec in _coppie(arr) if _e_cda(chiave, spec, nome)]


@dataclass(frozen=True)
class _AllegatoDispatcher:
    chiave: str
    nome_file: str | None
    dati: bytes


def _allegati_come_dispatcher(root) -> tuple[list[_AllegatoDispatcher], str | None]:
    """Gli allegati come li legge il dispatcher: (allegati, motivo del fallimento o None).

    Rifà PDFUtility.extractAttachments/extractFiles/extractFilesFromKids/createAttachmentDTO
    (it-fse-gtw-dispatcher, utility/PDFUtility.java:75-125) con PDFBox 2.0.26:
    - /Names della radice di /EmbeddedFiles se è un array, altrimenti i /Names dei /Kids di primo livello;
    - chiave non stringa: IOException (PDNameTreeNode.getNames); filespec non dizionario: ClassCastException;
    - per OGNI allegato getEmbeddedFile(), cioè /EF/F come stream: se manca, NullPointerException;
    - qualunque eccezione è presa da extractAttachments, che restituisce la mappa VUOTA: nessun CDA;
    - mappa con chiave in minuscolo: a parità di chiave vince l'ultima.
    """
    from pyhanko.pdf_utils import generic

    try:
        names = _ogg(root["/Names"]) if "/Names" in root else None
        if not isinstance(names, generic.DictionaryObject):
            return [], None if names is None else "/Names del catalogo non è un dizionario"
        if "/EmbeddedFiles" not in names:
            return [], None
        albero = _ogg(names["/EmbeddedFiles"])
        if not isinstance(albero, generic.DictionaryObject):
            return [], "/EmbeddedFiles non è un dizionario"
        radice_names = _ogg(albero["/Names"]) if "/Names" in albero else None
        if isinstance(radice_names, generic.ArrayObject):
            array = [radice_names]
        else:
            kids = _ogg(albero["/Kids"]) if "/Kids" in albero else None
            if not isinstance(kids, generic.ArrayObject):
                return [], "name tree /EmbeddedFiles senza /Names né /Kids (NullPointerException in extractFilesFromKids)"
            array = []
            for k in kids:
                k = _ogg(k)
                if not isinstance(k, generic.DictionaryObject):
                    return [], "/Kids con un elemento che non è un dizionario"
                n = _ogg(k["/Names"]) if "/Names" in k else None
                if isinstance(n, generic.ArrayObject):
                    array.append(n)
        mappa: dict[str, _AllegatoDispatcher] = {}
        for arr in array:
            for i in range(0, len(arr) - 1, 2):
                chiave = _stringa_o_none(arr[i])
                if chiave is None:
                    return [], f"chiave non stringa nel name tree ({_ogg(arr[i])!r}): IOException"
                spec = _ogg(arr[i + 1])
                if not isinstance(spec, generic.DictionaryObject):
                    return [], f"allegato {chiave!r}: il filespec non è un dizionario (ClassCastException)"
                ef = _ogg(spec["/EF"]) if "/EF" in spec else None
                flusso = _ogg(ef["/F"]) if isinstance(ef, generic.DictionaryObject) and "/F" in ef else None
                if not isinstance(flusso, generic.StreamObject):
                    return [], (f"allegato {chiave!r}: manca lo stream in /EF/F, getEmbeddedFile() è null "
                                "(NullPointerException): il dispatcher non legge NESSUN allegato, CDA compreso")
                mappa[chiave.lower()] = _AllegatoDispatcher(chiave, _nome_file_dispatcher(spec), flusso.data)
        return list(mappa.values()), None
    except Exception as e:  # come il dispatcher: qualunque errore e l'estrazione fallisce
        return [], f"struttura degli allegati non leggibile ({type(e).__name__}: {e})"


def _estrai_come_dispatcher(pdf: bytes, nome_allegato: str) -> bytes | None:
    """Il CDA che acquisirebbe il dispatcher, letto con la sua regola (non con _candidati).

    PdfNonEstraibile se l'estrazione degli allegati fallirebbe; AllegatiCdaAmbigui se più allegati
    soddisfano la condizione di extractContentFromAttachments (quale vinca dipende dall'ordine della
    HashMap Java); None se nessuno la soddisfa."""
    from pyhanko.pdf_utils.reader import PdfFileReader

    allegati, errore = _allegati_come_dispatcher(PdfFileReader(io.BytesIO(pdf)).root)
    if errore:
        raise PdfNonEstraibile(f"il dispatcher non riuscirebbe a leggere gli allegati del PDF: {errore}")
    trovati = [a for a in allegati if nome_allegato == a.chiave or nome_allegato == a.nome_file]
    if len(trovati) > 1:
        raise AllegatiCdaAmbigui(f"{len(trovati)} allegati soddisfano la regola del dispatcher "
                                 f"({', '.join(a.chiave for a in trovati)}): non si sa quale acquisirà")
    return trovati[0].dati if trovati else None


# ------------------------------------------------------------------ coerenza testo visibile / CDA

_INTESTAZIONE_KIT = re.compile(r"Documento (\S+) / (\S+) - versione (\d+)")
_ETICHETTA_IMPRONTA = "SHA-256 del contenuto del CDA per cui è scritto questo testo:"
_UUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")


def impronta_contenuto_cda(cda: bytes) -> str | None:
    """SHA-256 del CONTENUTO del CDA: XML canonico (C14N 2.0, spazi tra i tag tolti) con gli id tecnici
    casuali (<id root="UUID"/> di sezioni e voci, nuovi a ogni generazione) resi uguali. Due CDA dello
    stesso PSS hanno la stessa impronta; basta un valore clinico diverso per cambiarla. None se non è XML."""
    import xml.etree.ElementTree as ET

    try:
        radice = ET.fromstring(cda)
    except ET.ParseError:
        return None
    for el in radice.iter():
        if isinstance(el.tag, str) and el.tag.rsplit("}", 1)[-1] == "id" and _UUID.fullmatch(el.get("root", "")) \
                and el.get("extension") is None:
            el.set("root", "UUID")
    canonico = ET.canonicalize(ET.tostring(radice, encoding="unicode"), strip_text=True)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()
_IMPRONTA_KIT = re.compile(re.escape(_ETICHETTA_IMPRONTA) + r"\s+([0-9a-f]{64})")


def _identita_cda(cda: bytes) -> tuple[str, str, str] | None:
    """(root, extension, versione) di un ClinicalDocument; None se non è un CDA leggibile."""
    import xml.etree.ElementTree as ET

    try:
        cd = ET.fromstring(cda)
    except ET.ParseError:
        return None
    ns = "{urn:hl7-org:v3}"
    if cd.tag != ns + "ClinicalDocument":
        return None
    i, v = cd.find(ns + "id"), cd.find(ns + "versionNumber")
    if i is None or v is None:
        return None
    return i.get("root", ""), i.get("extension", ""), v.get("value", "")


def _testo_scritto_dal_kit(root) -> str:
    """Il testo delle pagine come lo scrive pdf_con_cda: stringhe letterali "(...) Tj" in Helvetica
    WinAnsi. Su un PDF di altra provenienza (testo compresso, font CID) di solito non trova nulla."""
    righe: list[str] = []

    def pagine(nodo):
        nodo = _ogg(nodo)
        if "/Kids" in nodo:
            for k in _ogg(nodo["/Kids"]):
                yield from pagine(k)
        else:
            yield nodo

    def escape(m):
        c = m.group(1)
        return {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}.get(c, c)

    try:
        for pagina in pagine(root["/Pages"]):
            if "/Contents" not in pagina:
                continue
            cont = _ogg(pagina["/Contents"])
            flussi = [_ogg(x) for x in cont] if isinstance(cont, list) else [cont]
            for f in flussi:
                for m in re.finditer(rb"\(((?:\\.|[^\\)])*)\)\s*Tj", f.data, re.S):
                    righe.append(re.sub(rb"\\(.)", escape, m.group(1), flags=re.S).decode("cp1252", "replace"))
    except Exception:  # testo non leggibile così: non è un PDF scritto dal kit
        return ""
    return " ".join(righe)


def _controlla_coerenza(root, cda: bytes, vecchi: list[bytes], *, testo_verificato: bool) -> None:
    """N4 (giro 2): il PDF leggibile deve descrivere lo stesso documento del CDA allegato.

    - testo scritto dal kit (intestazione "Documento <root> / <id> - versione <n>" di
      righe_leggibili_pss): id e versione del CDA devono coincidere, sempre;
    - testo non verificabile e sostituzione di un CDA di un ALTRO documento o versione: il testo
      visibile era quello del documento precedente; si rifiuta salvo testo_verificato=True (chi
      chiama dichiara di aver controllato che il testo visibile è già quello del nuovo documento).
    """
    nuovo = _identita_cda(cda)
    testo = _testo_scritto_dal_kit(root)
    m = _INTESTAZIONE_KIT.search(testo)
    if m:
        if nuovo != m.groups():
            raise TestoPdfIncoerente(
                f"il testo del PDF mostra il documento {m.group(1)} / {m.group(2)} versione {m.group(3)}, "
                f"il CDA è {'non leggibile come ClinicalDocument' if nuovo is None else '%s / %s versione %s' % nuovo}: "
                "rigenerare il PDF leggibile dallo stesso PSS (pdf_pss) invece di cambiare solo l'allegato")
        # Stesso id e stessa versione non bastano: il CONTENUTO può essere diverso (terapia «attiva» nel
        # testo, «conclusa» nel CDA; giro 3, G3-N1). Il testo scritto dal kit porta l'impronta del CDA per
        # cui è scritto; senza impronta (PDF del kit di prima) si accetta solo lo stesso CDA già allegato.
        impronte = set(_IMPRONTA_KIT.findall(" ".join(testo.split())))
        if impronte:
            if impronta_contenuto_cda(cda) not in impronte:
                raise TestoPdfIncoerente(
                    "il testo del PDF è stato scritto dal kit per un CDA con contenuto diverso (stesso id e "
                    "versione, impronta SHA-256 diversa): rigenerare il PDF leggibile dallo stesso PSS (pdf_pss) "
                    "invece di cambiare solo l'allegato")
        elif cda not in vecchi:
            raise TestoPdfIncoerente(
                "il testo del PDF è scritto dal kit ma non dice per quale CDA (manca l'impronta): il contenuto "
                "del nuovo CDA non si può confrontare col testo visibile. Rigenerare il PDF con pdf_pss")
        return
    if testo_verificato or nuovo is None:
        return
    for v in vecchi:
        prec = _identita_cda(v)
        if prec is not None and prec != nuovo:
            raise TestoPdfIncoerente(
                f"il CDA sostituito era {prec[0]} / {prec[1]} versione {prec[2]}, il nuovo è "
                f"{nuovo[0]} / {nuovo[1]} versione {nuovo[2]}: il testo visibile del PDF è stato scritto per il "
                "precedente e il kit non può verificarlo. Rigenerare il PDF leggibile (pdf_pss o il gestionale), "
                "oppure passare testo_verificato=True dopo aver controllato che il testo mostra il nuovo documento")


def inietta_cda(pdf: bytes, cda: bytes, *, nome_allegato: str = NOME_ALLEGATO, sostituisci: bool = False,
                testo_verificato: bool = False) -> bytes:
    """Allega il CDA a un PDF esistente (aggiornamento incrementale, il resto resta intatto).

    Se il PDF contiene già un CDA (allegato che il dispatcher potrebbe prendere per "cda.xml": chiave
    o uno qualunque dei nomi /UF, /DOS, /Mac, /Unix, /F, maiuscole comprese):
    - sostituisci=False (predefinito): CdaGiaPresente, nessuna modifica;
    - sostituisci=True: l'allegato precedente esce dal name tree e da /AF (stesso oggetto filespec o
      stesso criterio di riconoscimento), il nuovo entra.
    Coerenza col testo visibile (TestoPdfIncoerente): vedi _controlla_coerenza; testo_verificato=True
    vale solo quando il testo NON è scritto dal kit.
    Alla fine il risultato è riletto con la regola del dispatcher (_allegati_come_dispatcher, su TUTTI
    gli allegati): se il dispatcher non leggerebbe esattamente questo CDA, PdfNonEstraibile o
    AllegatiCdaAmbigui invece di un PDF certificato. Nota: se il PDF era firmato, l'aggiornamento
    resta fuori dalla firma precedente.
    """
    _pyhanko()
    from pyhanko.pdf_utils import embed, generic
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    w = IncrementalPdfFileWriter(io.BytesIO(pdf))
    esistenti = _candidati(w.root, nome_allegato, come_dispatcher=False)
    if esistenti and not sostituisci:
        raise CdaGiaPresente(
            f"il PDF contiene già {len(esistenti)} allegato/i CDA ({', '.join(c for c, _ in esistenti)}): "
            "aggiungerne un altro renderebbe ambiguo quale CDA acquisisce il gateway. "
            "Usare inietta_cda(..., sostituisci=True) per sostituirlo.")
    vecchi = []
    flussi_vecchi: set[tuple[int, int]] = set()  # riferimenti degli stream dei CDA da togliere
    for _, spec in esistenti:
        ef = _ogg(_ogg(spec).get("/EF", generic.DictionaryObject()))
        for k in ("/F", "/UF"):
            if k in ef:
                grezzo = ef.raw_get(k)
                if isinstance(grezzo, generic.IndirectObject):
                    flussi_vecchi.add((grezzo.idnum, grezzo.generation))
                dati = getattr(_ogg(ef[k]), "data", None)
                if dati is not None and dati not in vecchi:
                    vecchi.append(dati)

    def porta_un_vecchio_cda(spec) -> bool:
        """Il filespec incorpora uno dei CDA tolti: stesso oggetto stream o stessi byte (giro 3, G3-N2:
        un secondo filespec con altri nomi che punta allo stesso stream restava in /AF)."""
        spec = _ogg(spec)
        if not hasattr(spec, "keys") or "/EF" not in spec:
            return False
        ef = _ogg(spec["/EF"])
        for k in ("/F", "/UF"):
            if k not in ef:
                continue
            grezzo = ef.raw_get(k)
            if isinstance(grezzo, generic.IndirectObject) and (grezzo.idnum, grezzo.generation) in flussi_vecchi:
                return True
            if getattr(_ogg(ef[k]), "data", None) in vecchi:
                return True
        return False
    _controlla_coerenza(w.root, cda, vecchi, testo_verificato=testo_verificato)
    if esistenti:
        albero = _albero_allegati(w.root)
        if "/Kids" in albero:
            raise CdaGiaPresente("sostituzione del CDA in un name tree con /Kids non supportata: "
                                 "rigenerare il PDF dal gestionale senza allegato")
        arr = albero["/Names"]
        tolti = set()  # riferimenti (idnum, generation) dei filespec tolti: si tolgono anche da /AF
        for i, chiave, spec in reversed(_coppie(arr)):
            if _e_cda(chiave, spec, nome_allegato) or porta_un_vecchio_cda(spec):
                grezzo = arr.raw_get(i + 1)  # __getitem__ di pyHanko dereferenzia: serve il riferimento
                if isinstance(grezzo, generic.IndirectObject):
                    tolti.add((grezzo.idnum, grezzo.generation))
                del arr[i:i + 2]
        w.update_container(arr)
        if "/AF" in w.root:
            af_ogg = _ogg(w.root["/AF"])
            for i in reversed(range(len(af_ogg))):
                x = af_ogg.raw_get(i)
                stesso = isinstance(x, generic.IndirectObject) and (x.idnum, x.generation) in tolti
                if stesso or _e_cda("", af_ogg[i], nome_allegato) or porta_un_vecchio_cda(af_ogg[i]):
                    del af_ogg[i]
            w.update_container(af_ogg)
    ef = embed.EmbeddedFileObject.from_file_data(
        w, data=cda, mime_type="text/xml", params=embed.EmbeddedFileParams(embed_size=True, embed_checksum=True)
    )
    spec = embed.FileSpec(
        file_spec_string=nome_allegato, file_name=nome_allegato, embedded_data=ef, description="CDA2",
        af_relationship=generic.pdf_name("/Source"),
    )
    embed.embed_file(w, spec)
    out = io.BytesIO()
    w.write(out)
    risultato = out.getvalue()
    _verifica_risultato(risultato, cda, nome_allegato, vecchi=[v for v in vecchi if v != cda])
    return risultato


def _filespec_del_catalogo(root) -> list:
    """Ogni filespec raggiungibile dal catalogo corrente: /AF e il name tree degli allegati (anche /Kids)."""
    specs = list(_ogg(root["/AF"])) if "/AF" in root else []
    albero = _albero_allegati(root)
    if albero is not None:
        fonti = [albero["/Names"]] if "/Names" in albero else []
        fonti += [_ogg(k)["/Names"] for k in _ogg(albero.get("/Kids", [])) if "/Names" in _ogg(k)]
        specs += [spec for arr in fonti for _, _, spec in _coppie(arr)]
    return specs


def _dati_incorporati(spec) -> list[bytes]:
    spec = _ogg(spec)
    if not hasattr(spec, "keys") or "/EF" not in spec:
        return []
    ef = _ogg(spec["/EF"])
    return [d for k in ("/F", "/UF") if k in ef for d in [getattr(_ogg(ef[k]), "data", None)] if d is not None]


def _verifica_risultato(risultato: bytes, cda: bytes, nome_allegato: str, vecchi: list[bytes] = ()) -> None:
    """Verifica indipendente dal riconoscimento dei candidati: rilegge il PDF prodotto con un lettore
    nuovo e con la regola del dispatcher su TUTTI gli allegati, poi controlla /AF e i candidati."""
    from pyhanko.pdf_utils.reader import PdfFileReader

    letto = _estrai_come_dispatcher(risultato, nome_allegato)
    if letto != cda:
        raise AllegatiCdaAmbigui("dopo l'iniezione il dispatcher non leggerebbe il CDA iniettato "
                                 f"({'nessun CDA' if letto is None else 'un altro allegato'})")
    root = PdfFileReader(io.BytesIO(risultato)).root
    for x in (_ogg(root["/AF"]) if "/AF" in root else []):
        if _e_cda("", x, nome_allegato):
            ef = _ogg(_ogg(x).get("/EF")) if "/EF" in _ogg(x) else None
            if ef is None or "/F" not in ef or _ogg(ef["/F"]).data != cda:
                raise AllegatiCdaAmbigui("dopo l'iniezione /AF dichiara ancora un altro CDA")
    # nessun filespec del catalogo corrente porta ancora un CDA sostituito, con qualunque nome
    for x in _filespec_del_catalogo(root):
        if any(d in vecchi for d in _dati_incorporati(x)):
            raise AllegatiCdaAmbigui("dopo la sostituzione il catalogo porta ancora il CDA precedente "
                                     f"(allegato {', '.join(_nomi_filespec(x)) or 'senza nome'})")
    if estrai_cda(risultato, nome_allegato) != cda:  # pragma: no cover - coperto dalle due verifiche sopra
        raise AllegatiCdaAmbigui("dopo l'iniezione il CDA riletto non è quello iniettato")


def pdf_pss(pss, *, titolo: str = "Profilo Sanitario Sintetico") -> bytes:
    """PDF leggibile e CDA generati dallo STESSO PSS: testo visibile e allegato descrivono lo stesso
    documento (id, versione, contenuto). È il modo di produrre una nuova versione: non sostituire il
    solo allegato di un PDF che mostra la versione precedente."""
    from .cda_pss import genera_xml

    return pdf_con_cda(righe_leggibili_pss(pss), genera_xml(pss), titolo=titolo)


class Firmatario(Protocol):
    """Chi firma. In esercizio: la smart card o la firma remota del medico."""

    def firma_pades(self, pdf: bytes) -> bytes: ...


@dataclass
class FirmatarioPKCS12:
    """Firma PAdES B-B con chiave e certificato da un file .p12 (per le prove, o per chi ha un .p12 vero)."""

    percorso_p12: str
    password: bytes | None
    nome_campo: str = "FirmaMedico"
    motivo: str | None = None

    def firma_pades(self, pdf: bytes) -> bytes:
        _pyhanko()
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign import fields, signers

        firmatario = signers.SimpleSigner.load_pkcs12(self.percorso_p12, passphrase=self.password)
        if firmatario is None:
            raise ValueError(f"impossibile leggere {self.percorso_p12}")
        w = IncrementalPdfFileWriter(io.BytesIO(pdf))
        meta = signers.PdfSignatureMetadata(
            field_name=self.nome_campo,
            reason=self.motivo,
            subfilter=fields.SigSeedSubFilter.PADES,
            md_algorithm="sha256",
        )
        out = io.BytesIO()
        signers.PdfSigner(meta, signer=firmatario).sign_pdf(w, output=out)
        return out.getvalue()


def estrai_cda(pdf: bytes, nome_allegato: str = NOME_ALLEGATO) -> bytes | None:
    """Rilegge l'allegato CDA come lo legge il dispatcher ufficiale (None se non c'è).

    La regola è quella del dispatcher (_allegati_come_dispatcher: nome del file da getFilename con
    priorità /UF, /DOS, /Mac, /Unix, /F; contenuto da /EF/F; TUTTI gli allegati letti). In più:
    - AllegatiCdaAmbigui se più allegati possono essere presi per il CDA (il dispatcher li mette in
      una mappa e ne tiene uno), o se c'è un candidato che il dispatcher non vedrebbe;
    - PdfNonEstraibile se il dispatcher fallirebbe l'estrazione (un allegato qualunque senza /EF/F):
      il CDA c'è ma il gateway non lo troverebbe.
    """
    _pyhanko()
    from pyhanko.pdf_utils.reader import PdfFileReader

    r = PdfFileReader(io.BytesIO(pdf))
    tutti = _candidati(r.root, nome_allegato, come_dispatcher=False)
    if len(tutti) > 1:
        raise AllegatiCdaAmbigui(
            f"{len(tutti)} allegati possono essere il CDA ({', '.join(c for c, _ in tutti)}): "
            "non si può sapere quale acquisirà il gateway")
    allegati, errore = _allegati_come_dispatcher(r.root)
    if errore:
        if not tutti:
            return None  # il dispatcher non trova nulla, e un CDA non c'è comunque
        raise PdfNonEstraibile(f"il CDA c'è ma il dispatcher non lo troverebbe: {errore}")
    trovati = [a for a in allegati if nome_allegato == a.chiave or nome_allegato == a.nome_file]
    if len(trovati) > 1:
        raise AllegatiCdaAmbigui(f"{len(trovati)} allegati soddisfano la regola del dispatcher")
    if not trovati:
        if tutti:
            raise AllegatiCdaAmbigui(f"allegato {tutti[0][0]!r}: candidato CDA che il dispatcher non leggerebbe "
                                     f"come {nome_allegato!r} (nome diverso per maiuscole o per priorità dei "
                                     "nomi /UF, /DOS, /Mac, /Unix, /F, o fuori dal punto in cui lo cerca)")
        return None
    return trovati[0].dati


def crea_certificato_di_test(percorso_p12: str, password: bytes = b"test") -> None:
    """Certificato AUTOFIRMATO di test, RSA 2048, valido 30 giorni. Nessun valore legale.

    Il nome lo dice da solo ("CERTIFICATO DI TEST - NON VALIDO"). Serve solo a
    provare che il PDF firmato resta leggibile e che il CDA allegato sopravvive
    alla firma. La firma vera del medico richiede un certificato qualificato.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "varco (solo test)"),
        x509.NameAttribute(NameOID.COMMON_NAME, "varco CERTIFICATO DI TEST - NON VALIDO"),
    ])
    adesso = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chiave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(adesso - _dt.timedelta(minutes=5))
        .not_valid_after(adesso + _dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                          data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=False,
                          encipher_only=False, decipher_only=False),
            critical=True,
        )
        .sign(chiave, hashes.SHA256())
    )
    dati = pkcs12.serialize_key_and_certificates(
        b"varco-test", chiave, cert, None, serialization.BestAvailableEncryption(password)
    )
    with open(percorso_p12, "wb") as f:
        f.write(dati)
