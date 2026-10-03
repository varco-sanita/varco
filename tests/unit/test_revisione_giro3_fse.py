# SPDX-License-Identifier: EUPL-1.2
"""Controesempi del giro 3 di revisione esterna (03/10/2026), area FSE: i due bug del giro 2 chiusi a
metà (rapporto kit-mmg-review/2026-10-02-giro3/2-fse.md, G3-N1 = giro 2 N4, G3-N2 = giro 2 N2).

Ogni test riproduce il controesempio del revisore (prima della correzione falliva); i test «famiglia»
coprono le varianti dello stesso principio. Lettura indipendente del risultato con pypdf, non pyHanko.
Dati sintetici (esempi.pss_completo).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import io

import pytest

from varco.fse import cda_pss, esempi
from varco.fse.modello import Stato


def _moduli():
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pyhanko")
    from varco.fse import pdf

    return pypdf, pdf


def _terapia_conclusa(p):
    """Il PSS del revisore: Ramipril passa da attiva a conclusa il 1° gennaio 2025, stesso id e versione."""
    return dataclasses.replace(p, terapie=(
        dataclasses.replace(p.terapie[0], stato=Stato.CONCLUSO, fine=dt.date(2025, 1, 1)), *p.terapie[1:]))


# ====================================================================== G3-N1 (giro 2 N4): contenuto


def test_g3_n1_contenuto_diverso_stesso_id_e_versione_rifiutato():
    """G3-N1: `inietta_cda(pdf_pss(p), genera_xml(q), sostituisci=True)` restituiva un PDF che mostra la terapia
    attiva con dentro il CDA che la dice conclusa («CONTENUTO_PDF: … stato: attivo (active)»)."""
    _, pdf = _moduli()
    p = esempi.pss_completo()
    q = _terapia_conclusa(p)
    nuovo_cda = cda_pss.genera_xml(q)
    with pytest.raises(pdf.TestoPdfIncoerente, match="contenuto"):
        pdf.inietta_cda(pdf.pdf_pss(p), nuovo_cda, sostituisci=True)


def _varianti(p):
    a = p.allergie[0]
    return {
        "nota_allergia": dataclasses.replace(p, allergie=(dataclasses.replace(a, note="nota cambiata"), *p.allergie[1:])),
        "via_terapia": dataclasses.replace(p, terapie=(dataclasses.replace(p.terapie[0], via="IV"), *p.terapie[1:])),
        "terapia_tolta": dataclasses.replace(p, terapie=p.terapie[1:] or p.terapie),
        "stato_terapia": _terapia_conclusa(p),
    }


@pytest.mark.parametrize("variante", ["nota_allergia", "via_terapia", "terapia_tolta", "stato_terapia"])
@pytest.mark.parametrize("percorso", ["sostituisci", "prima_iniezione", "dichiarato_verificato"])
def test_g3_n1_famiglia_contenuto_diverso(variante, percorso):
    """Famiglia G3-N1: qualunque valore clinico diverso, sotto un testo scritto dal kit, con o senza CDA già
    allegato, anche con testo_verificato=True (che vale solo per un testo NON scritto dal kit)."""
    _, pdf = _moduli()
    p = esempi.pss_completo()
    q = _varianti(p)[variante]
    if q == p:
        pytest.skip("variante uguale al PSS di partenza")
    if percorso == "prima_iniezione":
        base, kw = pdf.pdf_con_cda(pdf.righe_leggibili_pss(p), None), {}
    else:
        base, kw = pdf.pdf_pss(p), {"sostituisci": True, "testo_verificato": percorso == "dichiarato_verificato"}
    with pytest.raises(pdf.TestoPdfIncoerente):
        pdf.inietta_cda(base, cda_pss.genera_xml(q), **kw)


def test_g3_n1_famiglia_testo_del_kit_senza_impronta():
    """Famiglia G3-N1: un PDF del kit di prima (testo senza impronta): il contenuto non si può confrontare,
    quindi solo lo stesso CDA già allegato è accettato."""
    _, pdf = _moduli()
    p = esempi.pss_completo()
    righe = [r for r in pdf.righe_leggibili_pss(p) if not r.startswith("SHA-256")]
    cda_p = cda_pss.genera_xml(p)
    vecchio_stile = pdf.pdf_con_cda(righe, cda_p)
    with pytest.raises(pdf.TestoPdfIncoerente, match="impronta"):
        pdf.inietta_cda(vecchio_stile, cda_pss.genera_xml(_terapia_conclusa(p)), sostituisci=True)
    assert pdf.estrai_cda(pdf.inietta_cda(vecchio_stile, cda_p, sostituisci=True)) == cda_p


def test_g3_n1_gruppo_di_controllo_stesso_contenuto_accettato():
    """Gruppo di controllo: lo stesso PSS rigenerato (id tecnici casuali nuovi) passa; il modo coerente per
    cambiare contenuto, pdf_pss del nuovo PSS, funziona."""
    _, pdf = _moduli()
    p = esempi.pss_completo()
    rigenerato = cda_pss.genera_xml(p)
    assert pdf.estrai_cda(pdf.inietta_cda(pdf.pdf_pss(p), rigenerato, sostituisci=True)) == rigenerato
    gestionale = pdf.pdf_con_cda(pdf.righe_leggibili_pss(p), None)
    assert pdf.estrai_cda(pdf.inietta_cda(gestionale, rigenerato)) == rigenerato
    q = _terapia_conclusa(p)
    assert b"completed" in pdf.estrai_cda(pdf.pdf_pss(q))


def test_g3_n1_impronta_ignora_solo_gli_id_tecnici():
    """L'impronta del contenuto: uguale tra due generazioni dello stesso PSS, diversa con un solo valore cambiato."""
    _, pdf = _moduli()
    p = esempi.pss_completo()
    a, b = cda_pss.genera_xml(p), cda_pss.genera_xml(p)
    assert a != b  # id tecnici casuali
    assert pdf.impronta_contenuto_cda(a) == pdf.impronta_contenuto_cda(b)
    assert pdf.impronta_contenuto_cda(a) != pdf.impronta_contenuto_cda(cda_pss.genera_xml(_terapia_conclusa(p)))


# ====================================================================== G3-N2 (giro 2 N2): /AF


def _af(pypdf, dati: bytes) -> list[tuple[str, bytes]]:
    r = pypdf.PdfReader(io.BytesIO(dati))
    root = r.trailer["/Root"]
    out = []
    for x in root.get("/AF", []):
        spec = x.get_object()
        out.append((str(spec.get("/UF") or spec.get("/F")), spec["/EF"]["/F"].get_object().get_data()))
    return out


def _nomi(pypdf, dati: bytes) -> list[tuple[str, bytes]]:
    root = pypdf.PdfReader(io.BytesIO(dati)).trailer["/Root"]
    arr = root["/Names"]["/EmbeddedFiles"]["/Names"]
    return [(str(arr[i]), arr[i + 1].get_object()["/EF"]["/F"].get_object().get_data()) for i in range(0, len(arr), 2)]


def _base_revisore(vecchio_cda: bytes, ritocco=None) -> bytes:
    """Il PDF del revisore: chiave cda.xml, /F e /UF = precedente.xml, e in /AF un filespec DISTINTO che
    punta allo stesso stream."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

    _, pdf = _moduli()
    w = PdfWriter(clone_from=io.BytesIO(pdf.pdf_con_cda(["PDF gestionale"], vecchio_cda)))
    root = w._root_object
    spec = root["/Names"]["/EmbeddedFiles"]["/Names"][1].get_object()
    spec[NameObject("/F")] = TextStringObject("precedente.xml")
    spec[NameObject("/UF")] = TextStringObject("precedente.xml")
    altro_spec = DictionaryObject(dict(spec))
    root[NameObject("/AF")] = ArrayObject([w._add_object(altro_spec)])
    if ritocco:
        ritocco(w, root, spec)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _due_cda():
    p = esempi.pss_completo()
    return cda_pss.genera_xml(p), cda_pss.genera_xml(_terapia_conclusa(p))


def test_g3_n2_filespec_distinto_stesso_stream_tolto_da_af():
    """G3-N2: «AF_CDA_REALI [('precedente.xml', True, False), ('cda.xml', False, True)]»: il vecchio CDA restava
    nel catalogo corrente."""
    pypdf, pdf = _moduli()
    vecchio_cda, nuovo_cda = _due_cda()
    risultato = pdf.inietta_cda(_base_revisore(vecchio_cda), nuovo_cda, sostituisci=True)
    af = _af(pypdf, risultato)
    assert [d == vecchio_cda for _, d in af] == [False], af
    assert [d == nuovo_cda for _, d in af] == [True]
    assert pdf.estrai_cda(risultato) == nuovo_cda


def _nel_name_tree_con_altro_nome(w, root, spec):
    """Variante: lo stesso stream anche nel name tree, sotto un nome che non è cda.xml."""
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    terzo = DictionaryObject(dict(spec))
    terzo[NameObject("/F")] = TextStringObject("allegato.bin")
    terzo[NameObject("/UF")] = TextStringObject("allegato.bin")
    arr = root["/Names"]["/EmbeddedFiles"]["/Names"]
    arr.insert(0, w._add_object(terzo))
    arr.insert(0, TextStringObject("allegato.bin"))


def _copia_dei_byte_in_af(w, root, spec):
    """Variante: in /AF un filespec con un ALTRO stream che contiene gli stessi byte del vecchio CDA."""
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, TextStringObject

    dati = spec["/EF"]["/F"].get_object().get_data()
    flusso = DecodedStreamObject()
    flusso.set_data(dati)
    copia = DictionaryObject({NameObject("/Type"): NameObject("/Filespec"),
                              NameObject("/F"): TextStringObject("copia.xml"),
                              NameObject("/UF"): TextStringObject("copia.xml"),
                              NameObject("/EF"): DictionaryObject({NameObject("/F"): w._add_object(flusso)})})
    root["/AF"].append(w._add_object(copia))


def _solo_uf(w, root, spec):
    """Variante: il filespec distinto in /AF porta lo stream solo in /EF/UF."""
    from pypdf.generic import DictionaryObject, NameObject

    altro = root["/AF"][0].get_object()
    altro[NameObject("/EF")] = DictionaryObject({NameObject("/UF"): spec["/EF"].raw_get("/F")})


@pytest.mark.parametrize("ritocco", [_nel_name_tree_con_altro_nome, _copia_dei_byte_in_af, _solo_uf],
                         ids=["name_tree_altro_nome", "copia_dei_byte", "solo_uf"])
def test_g3_n2_famiglia_nessun_filespec_porta_il_vecchio_cda(ritocco):
    """Famiglia G3-N2: dopo la sostituzione nessun filespec del catalogo corrente (name tree o /AF, con
    qualunque nome, stesso stream o copia dei byte) porta il CDA precedente."""
    pypdf, pdf = _moduli()
    vecchio_cda, nuovo_cda = _due_cda()
    risultato = pdf.inietta_cda(_base_revisore(vecchio_cda, ritocco), nuovo_cda, sostituisci=True)
    root = pypdf.PdfReader(io.BytesIO(risultato)).trailer["/Root"]
    portati = []
    for x in list(root.get("/AF", [])) + [v for i, v in enumerate(root["/Names"]["/EmbeddedFiles"]["/Names"]) if i % 2]:
        ef = x.get_object().get("/EF", {})
        for k in ("/F", "/UF"):
            if k in ef:
                portati.append(ef[k].get_object().get_data())
    assert vecchio_cda not in portati
    assert pdf.estrai_cda(risultato) == nuovo_cda


def test_g3_n2_gruppo_di_controllo_un_altro_allegato_resta():
    """Gruppo di controllo: un allegato diverso dal CDA (altri byte) resta nel PDF."""
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, TextStringObject

    def altro_allegato(w, root, spec):
        flusso = DecodedStreamObject()
        flusso.set_data(b"note del gestionale")
        f = DictionaryObject({NameObject("/Type"): NameObject("/Filespec"),
                              NameObject("/F"): TextStringObject("note.txt"),
                              NameObject("/UF"): TextStringObject("note.txt"),
                              NameObject("/EF"): DictionaryObject({NameObject("/F"): w._add_object(flusso)})})
        root["/AF"].append(w._add_object(f))

    pypdf, pdf = _moduli()
    vecchio_cda, nuovo_cda = _due_cda()
    risultato = pdf.inietta_cda(_base_revisore(vecchio_cda, altro_allegato), nuovo_cda, sostituisci=True)
    assert ("note.txt", b"note del gestionale") in _af(pypdf, risultato)
