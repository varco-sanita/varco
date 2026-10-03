# SPDX-License-Identifier: EUPL-1.2
"""Schemi XSD ufficiali (kit di sviluppo MEF, prescrittore) e validazione.

La validazione richiede `lxml` (dipendenza facoltativa, extra "validazione"):
la libreria standard non valida XSD. In esercizio non serve: il SAC valida
comunque lato server e risponde con un SOAP Fault "cvc-..." in caso di errore.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import lru_cache
from importlib import resources

SCHEMI = {
    "InvioPrescrittoRichiesta": "InvioPrescrittoRichiesta.xsd",
    "InvioPrescrittoRicevuta": "InvioPrescrittoRicevuta.xsd",
    "VisualizzaPrescrittoRichiesta": "VisualizzaPrescrittoRichiesta.xsd",
    "VisualizzaPrescrittoRicevuta": "VisualizzaPrescrittoRicevuta.xsd",
    "AnnullaPrescrittoRichiesta": "AnnullaPrescrittoRichiesta.xsd",
    "AnnullaPrescrittoRicevuta": "AnnullaPrescrittoRicevuta.xsd",
    "InterrogaNreUtilRichiesta": "InterrogaNreUtilRichiesta.xsd",
    "InterrogaNreUtilRicevuta": "InterrogaNreUtilRicevuta.xsd",
}

# Difetto dello schema ufficiale (kit MEF, TipiDatiInterrogaNreUtilizzati.xsd v1.2, riga 127):
# <xs:complexType name="elencoNreUtilRecordType" minOccurs="0"> non è XSD valido (minOccurs
# non è ammesso su complexType) e lxml/libxml2 rifiuta di compilarlo. Il file resta com'è;
# l'attributo si toglie solo in memoria, al caricamento. Non cambia cosa è valido: su un
# complexType l'attributo non avrebbe comunque significato.
_CORREZIONI = {
    "TipiDatiInterrogaNreUtilizzati.xsd": (
        b'<xs:complexType name="elencoNreUtilRecordType" minOccurs="0">',
        b'<xs:complexType name="elencoNreUtilRecordType">',
    ),
}


def lxml_disponibile() -> bool:
    try:
        import lxml.etree  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=None)
def _schema(nome: str):
    from lxml import etree

    class _Correttore(etree.Resolver):
        def resolve(self, url, pubid, context):
            nome_file = url.rsplit("/", 1)[-1]
            if nome_file in _CORREZIONI:
                vecchio, nuovo = _CORREZIONI[nome_file]
                dati = resources.files("varco.schemi").joinpath(nome_file).read_bytes()
                if dati.count(vecchio) != 1:
                    raise ValueError(f"{nome_file}: il difetto noto non c'è più, rivedere _CORREZIONI")
                return self.resolve_string(dati.replace(vecchio, nuovo), context)
            return None

    percorso = resources.files("varco.schemi").joinpath(SCHEMI[nome])
    parser = etree.XMLParser()
    parser.resolvers.add(_Correttore())
    with resources.as_file(percorso) as p:
        return etree.XMLSchema(etree.parse(str(p), parser))


def errori_xsd(elemento: ET.Element | bytes) -> list[str]:
    """Valida un elemento radice (senza busta SOAP). Lista vuota = valido."""
    from lxml import etree

    dati = elemento if isinstance(elemento, bytes) else ET.tostring(elemento, encoding="utf-8")
    doc = etree.fromstring(dati)
    nome = etree.QName(doc).localname
    if nome not in SCHEMI:
        return [f"nessuno schema per <{nome}>"]
    schema = _schema(nome)
    if schema.validate(doc):
        return []
    return [f"riga {e.line}: {e.message}" for e in schema.error_log]
