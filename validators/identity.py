"""
Validación de Identidad
========================
Recibe SOLO la cédula y consulta automáticamente:
  - OFAC SDN List (CSV público del Tesoro de EE.UU.)
  - ONU Consolidated Sanctions (XML público)

Fuentes que requieren CAPTCHA/login (NO automatizables gratis):
  Procuraduría, Contraloría, Policía, Rama Judicial → se omiten.

Normativa: Circular 055 SFC — SARLAFT / Ley 1581 de 2012
"""

import re
from datetime import datetime

import httpx
from pydantic import BaseModel


# ─── URLs públicas ───────────────────────────────────────────────

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
ONU_SANCTIONS_URL = "https://scsanctions.un.org/resources/xml/sp/consolidated.xml"

TIMEOUT = 15.0  # segundos


# ─── Modelo de entrada ──────────────────────────────────────────

class IdentityRequest(BaseModel):
    lead_id: str = ""
    celular: str = ""
    cedula: str


# ─── Helpers ─────────────────────────────────────────────────────

def _limpiar_cedula(cedula: str) -> str:
    return re.sub(r"[.\-\s]", "", cedula.strip())


def _buscar_en_ofac(cedula: str) -> dict:
    """Descarga la lista OFAC SDN (CSV) y busca la cédula."""
    resultado = {
        "fuente": "OFAC_SDN_LIST",
        "url": OFAC_SDN_URL,
        "en_lista": False,
        "coincidencias": 0,
        "estado": "error",
        "detalle": "",
    }

    try:
        resp = httpx.get(OFAC_SDN_URL, timeout=TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        contenido = resp.text

        coincidencias = 0
        for linea in contenido.split("\n"):
            if cedula in linea:
                coincidencias += 1

        resultado["estado"] = "consultado"
        resultado["coincidencias"] = coincidencias
        resultado["en_lista"] = coincidencias > 0
        resultado["detalle"] = (
            f"{coincidencias} coincidencia(s) encontrada(s)"
            if coincidencias > 0
            else "Sin coincidencias"
        )
        resultado["archivo_tamano_kb"] = round(len(contenido) / 1024)

    except httpx.TimeoutException:
        resultado["detalle"] = "Timeout al descargar lista OFAC"
    except httpx.HTTPStatusError as e:
        resultado["detalle"] = f"Error HTTP {e.response.status_code}"
    except Exception as e:
        resultado["detalle"] = f"Error: {str(e)}"

    return resultado


def _buscar_en_onu(cedula: str) -> dict:
    """Descarga la lista ONU Consolidated Sanctions (XML) y busca la cédula."""
    resultado = {
        "fuente": "ONU_CONSOLIDATED_SANCTIONS",
        "url": ONU_SANCTIONS_URL,
        "en_lista": False,
        "coincidencias": 0,
        "estado": "error",
        "detalle": "",
    }

    try:
        resp = httpx.get(ONU_SANCTIONS_URL, timeout=TIMEOUT, follow_redirects=True, verify=False)
        resp.raise_for_status()
        contenido = resp.text

        coincidencias = contenido.count(cedula)

        resultado["estado"] = "consultado"
        resultado["coincidencias"] = coincidencias
        resultado["en_lista"] = coincidencias > 0
        resultado["detalle"] = (
            f"{coincidencias} coincidencia(s) encontrada(s)"
            if coincidencias > 0
            else "Sin coincidencias"
        )
        resultado["archivo_tamano_kb"] = round(len(contenido) / 1024)

    except httpx.TimeoutException:
        resultado["detalle"] = "Timeout al descargar lista ONU"
    except httpx.HTTPStatusError as e:
        resultado["detalle"] = f"Error HTTP {e.response.status_code}"
    except Exception as e:
        resultado["detalle"] = f"Error: {str(e)}"

    return resultado


# ─── Lógica de validación ───────────────────────────────────────

def validar_identidad(data: IdentityRequest) -> dict:
    ahora = datetime.now().isoformat()
    alertas: list[str] = []

    cedula = data.cedula.strip()

    if not cedula:
        return {"estado": "error", "error": "No se proporcionó número de cédula"}

    cedula_limpia = _limpiar_cedula(cedula)
    formato_valido = bool(re.match(r"^\d{6,12}$", cedula_limpia))

    if not formato_valido:
        return {
            "estado": "error",
            "resultado": "FORMATO_INVALIDO",
            "error": f"Formato de cédula inválido: {cedula}. Se esperan 6-12 dígitos.",
            "formato_cedula_valido": False,
            "fecha_validacion": ahora,
        }

    # ── Consultas automáticas reales ─────────────────────────────
    ofac = _buscar_en_ofac(cedula_limpia)
    onu = _buscar_en_onu(cedula_limpia)

    ofac["fecha_consulta"] = ahora
    onu["fecha_consulta"] = ahora

    # ── Evaluar resultado ────────────────────────────────────────
    en_lista_ofac = ofac.get("en_lista", False)
    en_lista_onu = onu.get("en_lista", False)
    en_listas_restrictivas = en_lista_ofac or en_lista_onu

    fuentes_consultadas = sum(
        1 for f in [ofac, onu] if f["estado"] == "consultado"
    )

    if en_lista_ofac:
        alertas.append("[CRITICO] BLOQUEANTE: Coincidencia encontrada en lista OFAC SDN")
    if en_lista_onu:
        alertas.append("[CRITICO] BLOQUEANTE: Coincidencia encontrada en lista ONU Sanciones")

    if ofac["estado"] == "error":
        alertas.append(f"[ALERTA] No se pudo consultar OFAC: {ofac['detalle']}")
    if onu["estado"] == "error":
        alertas.append(f"[ALERTA] No se pudo consultar ONU: {onu['detalle']}")

    # ── Resultado ────────────────────────────────────────────────
    if en_listas_restrictivas:
        resultado = "BLOQUEADO"
        es_apto = False
    elif fuentes_consultadas == 2:
        resultado = "LIMPIO"
        es_apto = True
    elif fuentes_consultadas == 1:
        resultado = "PARCIAL"
        es_apto = True
    else:
        resultado = "ERROR_CONSULTA"
        es_apto = None

    return {
        "estado": "completado",
        "resultado": resultado,
        "es_apto": es_apto,
        "formato_cedula_valido": True,
        "cedula": cedula_limpia,
        "listas_restrictivas": {
            "ofac": ofac,
            "onu": onu,
        },
        "en_listas_restrictivas": en_listas_restrictivas,
        "alertas": alertas,
        "fuentes_consultadas": fuentes_consultadas,
        "fuentes_total": 2,
        "normativa": "Circular 055 SFC — SARLAFT / Ley 1581 de 2012",
        "fecha_validacion": ahora,
    }
