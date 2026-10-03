# SPDX-License-Identifier: EUPL-1.2
"""Controesempi del giro 2 di revisione esterna, area FSE (kit-mmg-review/2026-10-02-giro2/2-fse.md).

Ogni test riproduce il controesempio del revisore: falliva sul codice precedente, passa ora.
I test marcati `ufficiale` usano il codice del dispatcher (strumenti/validatore-ufficiale/estrai_cda.sh,
PDF passato via stdin come ha fatto il revisore) e si attivano da soli con JAVA_HOME e prepara.sh.

La regola del dispatcher che i test rifanno in modo indipendente dal kit (con pypdf, non pyHanko):
PDFUtility.extractContentFromAttachments + PDFBox 2.0.26 PDComplexFileSpecification.getFilename,
priorità del nome verificata con javap: /UF, /DOS, /Mac, /Unix, /F; contenuto da getEmbeddedFile(),
cioè SOLO /EF/F (se manca: NullPointerException, l'estrazione intera fallisce e il CDA non si trova).
"""

import dataclasses
import datetime as dt
import hashlib
import io
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from varco.fse import cda_pss, esempi
from varco.fse.modello import Stato

RADICE = Path(__file__).resolve().parents[2]
PROVA_COERENTE = RADICE / "prove" / "20261002-fse-v2-coerente"
H = {"h": "urn:hl7-org:v3"}


def _moduli():
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pyhanko")
    from varco.fse import pdf

    return pypdf, pdf


# ------------------------------------------------------------------ lettura indipendente (pypdf), regola del dispatcher


def _stringa(v):
    """COSDictionary.getString: solo le stringhe PDF, altrimenti null."""
    from pypdf.generic import ByteStringObject, TextStringObject

    v = v.get_object() if v is not None else None
    if isinstance(v, TextStringObject):
        return str(v)
    if isinstance(v, ByteStringObject):
        return bytes(v).decode("latin-1")
    return None


def _dispatcher_pypdf(dati: bytes, nome: str = "cda.xml"):
    """Il risultato del dispatcher secondo pypdf: ('trovato', bytes) | ('assente', None) | ('errore', motivo)."""
    import pypdf
    from pypdf.generic import ArrayObject, DictionaryObject, StreamObject

    root = pypdf.PdfReader(io.BytesIO(dati)).trailer["/Root"]
    try:
        names = root["/Names"].get_object()
        ef = names.get("/EmbeddedFiles")
        if ef is None:
            return ("assente", None)
        ef = ef.get_object()
        if isinstance(ef.get("/Names", None) and ef["/Names"].get_object(), ArrayObject):
            array = [ef["/Names"].get_object()]
        else:
            array = [k.get_object()["/Names"].get_object() for k in ef["/Kids"].get_object()
                     if "/Names" in k.get_object()]
        mappa = {}
        for arr in array:
            for i in range(0, len(arr) - 1, 2):
                chiave = _stringa(arr[i])
                if chiave is None:
                    return ("errore", "chiave non stringa")
                spec = arr[i + 1].get_object()
                if not isinstance(spec, DictionaryObject):
                    return ("errore", "filespec non dizionario")
                nome_file = next((n for n in (_stringa(spec.get(k)) for k in ("/UF", "/DOS", "/Mac", "/Unix", "/F"))
                                  if n is not None), None)
                efd = spec.get("/EF")
                efd = efd.get_object() if efd is not None else None
                stream = efd.get("/F").get_object() if isinstance(efd, DictionaryObject) and "/F" in efd else None
                if not isinstance(stream, StreamObject):
                    return ("errore", f"getEmbeddedFile() null per {chiave!r}")
                mappa[chiave.lower()] = (chiave, nome_file, stream.get_data())
    except (KeyError, AttributeError, TypeError):
        return ("errore", "struttura")
    trovati = [d for c, f, d in mappa.values() if nome == c or nome == f]
    return ("trovato", trovati[0]) if len(trovati) == 1 else ("assente", None) if not trovati else ("ambiguo", None)


def _dispatcher_java(dati: bytes) -> dict:
    from varco.fse.validazione import cartella_validatore_ufficiale

    r = subprocess.run([str(cartella_validatore_ufficiale() / "estrai_cda.sh"), "/dev/stdin"],
                       input=dati, capture_output=True, timeout=600)
    righe = [json.loads(x[len("RISULTATO "):]) for x in r.stdout.decode().splitlines() if x.startswith("RISULTATO ")]
    assert len(righe) == 1, r.stderr.decode()[-2000:]
    return righe[0] | {"log": r.stdout.decode() + r.stderr.decode()}


def _ritocca(dati: bytes, ritocco) -> bytes:
    """Clona il PDF con pypdf e applica `ritocco(writer)` (come ha fatto il revisore)."""
    import pypdf

    w = pypdf.PdfWriter(clone_from=io.BytesIO(dati))
    ritocco(w)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _spec(w, indice: int = 0):
    return w._root_object["/Names"]["/EmbeddedFiles"]["/Names"][2 * indice + 1].get_object()


def _due_pss():
    """Due PSS completi: nel nuovo la prima terapia è conclusa, nel vecchio è ancora attiva."""
    vecchio = esempi.pss_completo()
    t = vecchio.terapie[0]
    concluso = dataclasses.replace(vecchio, terapie=(dataclasses.replace(t, stato=Stato.CONCLUSO, fine=dt.date(2026, 9, 1)),
                                                     *vecchio.terapie[1:]))
    return cda_pss.genera_xml(vecchio), cda_pss.genera_xml(concluso)


def _base_n1(vecchio: bytes) -> bytes:
    """N1: vecchio CDA con chiave e /F = b.xml, /DOS = cda.xml, senza /UF."""
    from pypdf.generic import NameObject, TextStringObject

    _, pdf = _moduli()

    def ritocco(w):
        spec = _spec(w)
        del spec[NameObject("/UF")]
        spec[NameObject("/DOS")] = TextStringObject("cda.xml")

    return _ritocca(pdf.pdf_con_cda(["test"], vecchio, nome_allegato="b.xml"), ritocco)


# ------------------------------------------------------------------ N1


def test_g2_n1_dos_riconosciuto_come_cda_niente_secondo_allegato():
    """N1: chiave e /F = b.xml, /DOS = cda.xml, senza /UF; il kit non lo riconosceva e aggiungeva un secondo CDA."""
    _, pdf = _moduli()
    vecchio, nuovo = _due_pss()
    base = _base_n1(vecchio)
    # il dispatcher (getFilename: /UF assente -> /DOS) prende questo allegato come cda.xml
    assert _dispatcher_pypdf(base) == ("trovato", vecchio)
    assert pdf.estrai_cda(base) == vecchio
    with pytest.raises(pdf.CdaGiaPresente):
        pdf.inietta_cda(base, nuovo)
    sostituito = pdf.inietta_cda(base, nuovo, sostituisci=True)
    assert _dispatcher_pypdf(sostituito) == ("trovato", nuovo)
    assert pdf.estrai_cda(sostituito) == nuovo


@pytest.mark.parametrize("chiave_nome", ["/Mac", "/Unix"])
def test_g2_n1_mac_unix_riconosciuti(chiave_nome):
    """N1: le altre due chiavi della priorità di getFilename (/Mac, /Unix), stesso scenario."""
    from pypdf.generic import NameObject, TextStringObject

    _, pdf = _moduli()

    def ritocco(w):
        spec = _spec(w)
        del spec[NameObject("/UF")]
        spec[NameObject(chiave_nome)] = TextStringObject("cda.xml")

    base = _ritocca(pdf.pdf_con_cda(["test"], b"<vecchio/>", nome_allegato="b.xml"), ritocco)
    assert _dispatcher_pypdf(base) == ("trovato", b"<vecchio/>")
    assert pdf.estrai_cda(base) == b"<vecchio/>"
    with pytest.raises(pdf.CdaGiaPresente):
        pdf.inietta_cda(base, b"<nuovo/>")


def test_g2_n1_priorita_uf_batte_dos():
    """N1, gruppo di controllo: con /UF = b.xml e /DOS = cda.xml il dispatcher usa /UF e NON lo prende per il CDA;
    il kit lo considera comunque un candidato (in dubbio) e non aggiunge un secondo allegato alla cieca."""
    from pypdf.generic import NameObject, TextStringObject

    _, pdf = _moduli()

    def ritocco(w):
        _spec(w)[NameObject("/DOS")] = TextStringObject("cda.xml")

    base = _ritocca(pdf.pdf_con_cda(["test"], b"<vecchio/>", nome_allegato="b.xml"), ritocco)
    assert _dispatcher_pypdf(base) == ("assente", None)
    with pytest.raises(pdf.AllegatiCdaAmbigui):
        pdf.estrai_cda(base)  # un candidato che il dispatcher non vede: non si dice "nessun CDA"
    with pytest.raises(pdf.CdaGiaPresente):
        pdf.inietta_cda(base, b"<nuovo/>")


def test_g2_n1_verifica_finale_indipendente_dal_riconoscimento():
    """N1: la verifica finale di inietta_cda (pdf.py:330) ricontrollava con lo stesso estrattore incompleto.
    Rimesso il riconoscimento vecchio (solo /UF e /F), la verifica finale deve comunque fermare il PDF con due CDA."""
    _, pdf = _moduli()
    vecchio, nuovo = _due_pss()

    def solo_uf_f(spec):  # il _nomi_filespec del codice precedente
        spec = pdf._ogg(spec)
        return [str(pdf._ogg(spec[k])) for k in ("/UF", "/F") if hasattr(spec, "keys") and k in spec]

    originale = pdf._nomi_filespec
    try:
        pdf._nomi_filespec = solo_uf_f
        with pytest.raises(pdf.AllegatiCdaAmbigui):
            pdf.inietta_cda(_base_n1(vecchio), nuovo)
    finally:
        pdf._nomi_filespec = originale


@pytest.mark.ufficiale
def test_g2_n1_dispatcher_ufficiale_via_stdin():
    """N1 col codice ufficiale: sul PDF base il dispatcher trova il vecchio CDA; dopo la sostituzione il nuovo,
    lo stesso che rilegge il kit; l'iniezione senza sostituzione è rifiutata (prima: due CDA, dispatcher il vecchio)."""
    _, pdf = _moduli()
    vecchio, nuovo = _due_pss()
    base = _base_n1(vecchio)
    d = _dispatcher_java(base)
    assert d["trovato"] and d["sha256"] == hashlib.sha256(vecchio).hexdigest()
    with pytest.raises(pdf.CdaGiaPresente):
        pdf.inietta_cda(base, nuovo)
    sostituito = pdf.inietta_cda(base, nuovo, sostituisci=True)
    d = _dispatcher_java(sostituito)
    assert d["trovato"] and d["sha256"] == hashlib.sha256(nuovo).hexdigest() == hashlib.sha256(pdf.estrai_cda(sostituito)).hexdigest()


# ------------------------------------------------------------------ N2


def test_g2_n2_sostituzione_toglie_il_vecchio_da_af():
    """N2: chiave cda.xml, /F e /UF = precedente.xml; dopo sostituisci=True il vecchio restava in /AF."""
    from pypdf.generic import NameObject, TextStringObject

    pypdf, pdf = _moduli()

    def ritocco(w):
        spec = _spec(w)
        spec[NameObject("/F")] = TextStringObject("precedente.xml")
        spec[NameObject("/UF")] = TextStringObject("precedente.xml")

    base = _ritocca(pdf.pdf_con_cda(["test"], b"<vecchio/>"), ritocco)
    assert _dispatcher_pypdf(base) == ("trovato", b"<vecchio/>")  # chiave cda.xml: è il CDA
    nuovo = pdf.inietta_cda(base, b"<nuovo/>", sostituisci=True)
    assert pdf.estrai_cda(nuovo) == b"<nuovo/>"
    r = pypdf.PdfReader(io.BytesIO(nuovo))
    af = [(str(x.get_object().get("/UF")), x.get_object()["/EF"]["/F"].get_object().get_data())
          for x in r.trailer["/Root"]["/AF"]]
    assert af == [("cda.xml", b"<nuovo/>")]


# ------------------------------------------------------------------ N3


def _base_n3() -> bytes:
    """N3: PDF del gestionale con un altro allegato, a-note.txt, il cui stream è solo in /EF/UF."""
    from pypdf.generic import NameObject

    pypdf, pdf = _moduli()

    def ritocco(w):
        spec = _spec(w)
        del spec["/EF"][NameObject("/F")]

    return _ritocca(pdf.pdf_con_cda(["test"], b"note del gestionale", nome_allegato="a-note.txt"), ritocco)


def test_g2_n3_altro_allegato_illeggibile_dal_dispatcher_rifiutato():
    """N3: allegato a-note.txt con stream solo in /EF/UF: il dispatcher va in NullPointerException e non trova il CDA,
    il kit dichiarava l'iniezione riuscita."""
    _, pdf = _moduli()
    base = _base_n3()
    assert _dispatcher_pypdf(base)[0] == "errore"
    with pytest.raises(pdf.PdfNonEstraibile, match="a-note.txt"):
        pdf.inietta_cda(base, b"<nuovo/>")


def test_g2_n3_estrazione_dice_che_il_dispatcher_non_lo_troverebbe():
    """N3: anche la rilettura del kit (estrai_cda) deve dire che il dispatcher non trova il CDA, non restituirlo."""
    from pyhanko.pdf_utils import embed
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    _, pdf = _moduli()
    w = IncrementalPdfFileWriter(io.BytesIO(_base_n3()))  # CDA aggiunto senza passare dal kit
    ef = embed.EmbeddedFileObject.from_file_data(w, data=b"<nuovo/>", mime_type="text/xml")
    embed.embed_file(w, embed.FileSpec(file_spec_string="cda.xml", file_name="cda.xml", embedded_data=ef))
    out = io.BytesIO()
    w.write(out)
    assert _dispatcher_pypdf(out.getvalue())[0] == "errore"
    with pytest.raises(pdf.PdfNonEstraibile):
        pdf.estrai_cda(out.getvalue())


@pytest.mark.ufficiale
def test_g2_n3_dispatcher_ufficiale_conferma():
    """N3 col codice ufficiale: sul PDF che il kit prima certificava il dispatcher non trova il CDA (NullPointerException)."""
    from pyhanko.pdf_utils import embed
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    _, pdf = _moduli()
    w = IncrementalPdfFileWriter(io.BytesIO(_base_n3()))
    ef = embed.EmbeddedFileObject.from_file_data(w, data=b"<nuovo/>", mime_type="text/xml")
    embed.embed_file(w, embed.FileSpec(file_spec_string="cda.xml", file_name="cda.xml", embedded_data=ef))
    out = io.BytesIO()
    w.write(out)
    d = _dispatcher_java(out.getvalue())
    assert d["trovato"] is False and "NullPointerException" in d["log"] and "getEmbeddedFile()" in d["log"]
    # gruppo di controllo: senza l'allegato rotto il dispatcher il CDA lo trova
    assert _dispatcher_java(pdf.inietta_cda(pdf.pdf_con_cda(["test"], None), b"<nuovo/>"))["trovato"] is True


# ------------------------------------------------------------------ N4


def _v2():
    return dataclasses.replace(esempi.pss_completo("PSS-TEST-0001-V2"), versione=2, id_set="PSS-TEST-0001",
                               id_documento_precedente="PSS-TEST-0001")


def test_g2_n4_sostituire_il_cda_sotto_un_testo_v1_rifiutato():
    """N4: inietta_cda(pdf_v1, cda_v2, sostituisci=True) produceva un PDF che mostra «versione 1» con dentro il CDA v2."""
    _, pdf = _moduli()
    completo = esempi.pss_completo()
    cda_v1, cda_v2 = cda_pss.genera_xml(completo), cda_pss.genera_xml(_v2())
    pdf_v1 = pdf.pdf_con_cda(pdf.righe_leggibili_pss(completo), cda_v1)
    with pytest.raises(pdf.TestoPdfIncoerente, match="versione"):
        pdf.inietta_cda(pdf_v1, cda_v2, sostituisci=True)
    with pytest.raises(pdf.TestoPdfIncoerente):  # la dichiarazione non scavalca un'incoerenza VISTA
        pdf.inietta_cda(pdf_v1, cda_v2, sostituisci=True, testo_verificato=True)
    # il modo coerente: il PDF leggibile rigenerato dal PSS v2, con il CDA v2 dello stesso PSS
    v2 = pdf.pdf_pss(_v2())
    assert _identita(pdf.estrai_cda(v2)) == (completo.radice_id, "PSS-TEST-0001-V2", "2")
    # e un PDF il cui testo è già v2 ma con l'allegato vecchio si può correggere sostituendo l'allegato
    v2_allegato_vecchio = pdf.pdf_con_cda(pdf.righe_leggibili_pss(_v2()), cda_v1)
    assert pdf.estrai_cda(pdf.inietta_cda(v2_allegato_vecchio, cda_v2, sostituisci=True)) == cda_v2


def test_g2_n4_iniezione_in_pdf_del_kit_con_altro_documento_rifiutata():
    """N4: anche senza sostituzione, un CDA diverso dal documento scritto nel testo del kit è rifiutato."""
    _, pdf = _moduli()
    cda_v1, cda_v2 = cda_pss.genera_xml(esempi.pss_completo()), cda_pss.genera_xml(_v2())
    gestionale = pdf.pdf_con_cda(pdf.righe_leggibili_pss(esempi.pss_completo()), None)
    with pytest.raises(pdf.TestoPdfIncoerente):
        pdf.inietta_cda(gestionale, cda_v2)
    assert pdf.estrai_cda(pdf.inietta_cda(gestionale, cda_v1)) == cda_v1


def test_g2_n4_sostituzione_che_cambia_documento_senza_testo_verificabile():
    """N4: testo non scritto dal kit (non verificabile) e CDA precedente di un altro documento/versione:
    il testo visibile era stato scritto per il precedente, quindi si rifiuta salvo dichiarazione esplicita."""
    _, pdf = _moduli()
    cda_v1, cda_v2 = cda_pss.genera_xml(esempi.pss_completo()), cda_pss.genera_xml(_v2())
    base = pdf.pdf_con_cda(["PDF del gestionale (testo non scritto dal kit)"], cda_v1)
    with pytest.raises(pdf.TestoPdfIncoerente):
        pdf.inietta_cda(base, cda_v2, sostituisci=True)
    ok = pdf.inietta_cda(base, cda_v2, sostituisci=True, testo_verificato=True)
    assert pdf.estrai_cda(ok) == cda_v2


def _identita(cda: bytes) -> tuple[str, str, str]:
    cd = ET.fromstring(cda)
    i = cd.find("h:id", H)
    return i.get("root"), i.get("extension"), cd.find("h:versionNumber", H).get("value")


@pytest.mark.parametrize("nome", ["pss_09_v2_firmato_TEST.pdf", "pss_09_v2_sostituito_firmato_TEST.pdf"])
def test_g2_n4_prova_coerente_testo_visibile_uguale_al_cda(nome):
    """N4: nella prova nuova il PDF firmato mostra la stessa versione e lo stesso id del CDA che contiene."""
    pypdf, pdf = _moduli()
    p = PROVA_COERENTE / nome
    if not p.exists():
        pytest.skip(f"{p} non generato (genera_prove_fse_v2_coerente.py)")
    dati = p.read_bytes()
    testo = "\n".join(pg.extract_text() for pg in pypdf.PdfReader(io.BytesIO(dati)).pages)
    cda = pdf.estrai_cda(dati)
    assert _dispatcher_pypdf(dati) == ("trovato", cda)
    radice, ext, ver = _identita(cda)
    m = re.search(r"Documento\s+(\S+)\s+/\s+(\S+)\s+-\s+versione\s+(\d+)", testo)
    assert m, testo[:500]
    assert m.groups() == (radice, ext, ver) == (radice, "PSS-TEST-0001-V2", "2")
    assert "Sostituisce il documento PSS-TEST-0001" in testo


def test_g2_n4_gruppo_di_controllo_la_prova_vecchia_e_incoerente():
    """N4, gruppo di controllo: lo stesso confronto sul PDF firmato di prove/20261002-fse-v2 (sola lettura)
    trova «versione 1» nel testo e la versione 2 nel CDA: è il motivo per cui la prova nuova lo sostituisce."""
    pypdf, pdf = _moduli()
    p = RADICE / "prove" / "20261002-fse-v2" / "pss_01_sostituito_v2_firmato_TEST.pdf"
    if not p.exists():
        pytest.skip(f"{p} assente")
    dati = p.read_bytes()
    testo = "\n".join(pg.extract_text() for pg in pypdf.PdfReader(io.BytesIO(dati)).pages)
    m = re.search(r"Documento\s+(\S+)\s+/\s+(\S+)\s+-\s+versione\s+(\d+)", testo)
    assert m.group(2, 3) == ("PSS-TEST-0001", "1")
    assert _identita(pdf.estrai_cda(dati))[1:] == ("PSS-TEST-0001-V2", "2")
