"""
Validación de Inmueble
=======================
Valida cobertura geográfica, tipo de inmueble y valor mínimo.
Fuentes catastrales son manuales (no tienen API pública libre).

Fuentes manuales:
  - Catastro Bogotá: https://servicios.catastrobogota.gov.co
  - IGAC: https://geoportal.igac.gov.co
  - Datos Abiertos: https://www.datos.gov.co
"""

import re
import unicodedata
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ─── Constantes ──────────────────────────────────────────────────

SMMLV = 1_300_000
VALOR_MINIMO = 100_000_000  # $100M COP

CIUDADES_COBERTURA = [
    "bogota", "bogotá", "medellin", "medellín",
    "cali", "barranquilla", "bucaramanga",
    "cartagena", "pereira", "manizales",
    "santa marta", "ibague", "ibagué",
]

TIPOS_ACEPTADOS = ["casa", "apartamento", "apto", "apt"]


# ─── Modelos de entrada ─────────────────────────────────────────

class PropertyRequest(BaseModel):
    lead_id: str = ""
    celular: str = ""
    ciudad: str = ""
    tipo_inmueble: str = ""
    valor_inmueble: float = 0
    direccion: Optional[str] = None
    chip: Optional[str] = None
    matricula: Optional[str] = None


# ─── Lógica de validación ───────────────────────────────────────

def validar_inmueble(data: PropertyRequest) -> dict:
    ahora = datetime.now().isoformat()
    alertas: list[str] = []

    ciudad = data.ciudad
    tipo_inmueble = data.tipo_inmueble
    valor_inmueble = data.valor_inmueble
    direccion = data.direccion or ""
    chip = data.chip or ""
    matricula = data.matricula or ""

    if not ciudad:
        return {"estado": "error", "error": "No se proporcionó ciudad"}

    # ── Cobertura ────────────────────────────────────────────────
    ciudad_norm = _normalizar(ciudad)
    en_cobertura = any(ciudad_norm.find(_normalizar(c)) >= 0 for c in CIUDADES_COBERTURA)

    if not en_cobertura:
        alertas.append(f"[CRITICO] '{ciudad}' fuera de cobertura Sureti")

    # ── Tipo de inmueble ─────────────────────────────────────────
    tipo_lower = tipo_inmueble.lower().strip()
    tipo_aceptado = any(t in tipo_lower for t in TIPOS_ACEPTADOS)

    if not tipo_aceptado and tipo_inmueble:
        alertas.append(f"[CRITICO] Tipo '{tipo_inmueble}' no aceptado (solo casa/apartamento)")

    # ── Valor mínimo ─────────────────────────────────────────────
    cumple_valor_minimo = valor_inmueble >= VALOR_MINIMO
    if valor_inmueble > 0 and not cumple_valor_minimo:
        alertas.append(
            f"[CRITICO] Valor (${valor_inmueble:,.0f}) menor al mínimo (${VALOR_MINIMO:,.0f})"
        )

    # ── Clasificación VIS/VIP/No VIS ─────────────────────────────
    clasificacion_vivienda = "no_vis"
    if valor_inmueble > 0:
        if valor_inmueble <= 90 * SMMLV:
            clasificacion_vivienda = "vip"
        elif valor_inmueble <= 150 * SMMLV:
            clasificacion_vivienda = "vis"

    # ── Fuentes manuales ─────────────────────────────────────────
    fuentes_manuales: list[dict] = []

    if "bogota" in ciudad_norm:
        fuentes_manuales.append({
            "fuente": "CATASTRO_BOGOTA",
            "url": "https://servicios.catastrobogota.gov.co",
            "estado": "consulta_manual_requerida",
            "instrucciones": (
                f"Consultar chip {chip} en https://servicios.catastrobogota.gov.co"
                if chip
                else f'Consultar dirección "{direccion}" en https://servicios.catastrobogota.gov.co'
            ),
            "fecha_consulta": ahora,
        })

    fuentes_manuales.append({
        "fuente": "IGAC",
        "url": "https://geoportal.igac.gov.co",
        "estado": "consulta_manual_requerida",
        "instrucciones": "Consultar datos prediales en https://geoportal.igac.gov.co/contenido/consulta-catastral",
        "fecha_consulta": ahora,
    })

    fuentes_manuales.append({
        "fuente": "DATOS_ABIERTOS_COLOMBIA",
        "url": "https://www.datos.gov.co",
        "estado": "consulta_manual_requerida",
        "instrucciones": f'Buscar "{ciudad} catastro" en https://www.datos.gov.co',
        "fecha_consulta": ahora,
    })

    # ── Resultado ────────────────────────────────────────────────
    es_viable = en_cobertura and tipo_aceptado and cumple_valor_minimo

    return {
        "estado": "completado",
        "en_cobertura": en_cobertura,
        "tipo_aceptado": tipo_aceptado,
        "cumple_valor_minimo": cumple_valor_minimo,
        "es_viable": es_viable,
        "clasificacion_vivienda": clasificacion_vivienda,
        "datos_inmueble": {
            "ciudad": ciudad,
            "tipo_inmueble": tipo_inmueble,
            "valor_comercial": valor_inmueble,
            "direccion": direccion or None,
            "chip": chip or None,
            "matricula": matricula or None,
        },
        "valor_minimo_requerido": VALOR_MINIMO,
        "fuentes_manuales": fuentes_manuales,
        "alertas": alertas,
        "fecha_validacion": ahora,
    }


# ─── Helpers ─────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    t = texto.lower().strip()
    t = unicodedata.normalize("NFD", t)
    t = re.sub(r"[\u0300-\u036f]", "", t)
    return t
