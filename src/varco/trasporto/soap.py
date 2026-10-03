# SPDX-License-Identifier: EUPL-1.2
"""Busta SOAP 1.1 (document/literal, WS-I Basic Profile) con la sola libreria standard."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..errori import ErroreSOAP, ErroreTrasporto

NS_SOAPENV = "http://schemas.xmlsoap.org/soap/envelope/"
ET.register_namespace("soapenv", NS_SOAPENV)


def imbusta(corpo: ET.Element) -> bytes:
    env = ET.Element(f"{{{NS_SOAPENV}}}Envelope")
    ET.SubElement(env, f"{{{NS_SOAPENV}}}Header")
    body = ET.SubElement(env, f"{{{NS_SOAPENV}}}Body")
    body.append(corpo)
    return ET.tostring(env, encoding="utf-8", xml_declaration=True)


def sbusta(xml: bytes, stato_http: int | None = None) -> ET.Element:
    """Restituisce il primo figlio del Body. Solleva ErroreSOAP per i Fault."""
    try:
        radice = ET.fromstring(xml)
    except ET.ParseError as e:
        anteprima = xml[:300].decode("utf-8", "replace")
        raise ErroreTrasporto(f"Risposta non XML (HTTP {stato_http}): {anteprima!r}", stato_http, xml) from e
    body = radice.find(f"{{{NS_SOAPENV}}}Body")
    if body is None or len(body) == 0:
        raise ErroreTrasporto(f"Busta SOAP senza Body (HTTP {stato_http})", stato_http, xml)
    primo = body[0]
    if primo.tag == f"{{{NS_SOAPENV}}}Fault":
        raise ErroreSOAP(
            (primo.findtext("faultcode") or "").strip(),
            (primo.findtext("faultstring") or "").strip(),
            stato_http,
            xml,
        )
    return primo
