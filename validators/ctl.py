"""
CTL Checker — Certificado de Tradición y Libertad
===================================================
Valida formato de matrícula inmobiliaria y analiza texto del CTL
buscando patrones de riesgo (embargos, hipotecas, falsas tradiciones, etc.).

Fuentes manuales:
  - SNR: https://radicacion.supernotariado.gov.co
  - VUR: https://www.vur.gov.co (~$20.600 COP)
"""

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# ─── Patrones de riesgo en texto CTL ─────────────────────────────

PATRONES_RIESGO = {
    "embargo": re.compile(r"EMBARGO|SECUESTRO|MEDIDA\s*CAUTELAR\s*DE\s*EMBARGO", re.I),
    "hipoteca": re.compile(r"HIPOTECA|GRAVAMEN\s*HIPOTECARIO|CONSTITUCI[OÓ]N\s*DE\s*HIPOTECA", re.I),
    "medida_cautelar": re.compile(r"MEDIDA\s*CAUTELAR|INSCRIPCI[OÓ]N\s*DE\s*DEMANDA", re.I),
    "demanda": re.compile(r"DEMANDA|PROCESO\s*EJECUTIVO|ACCI[OÓ]N\s*REIVINDICATORIA", re.I),
    "falsa_tradicion": re.compile(r"FALSA\s*TRADICI[OÓ]N|TRADICI[OÓ]N\s*IRREGULAR", re.I),
    "tradicion": re.compile(r"TRADICI[OÓ]N|COMPRAVENTA|TRANSFERENCIA\s*DE\s*DOMINIO", re.I),
    "propietario": re.compile(r"PROPIETARIO|TITULAR\s*DEL\s*DERECHO|DOMINIO", re.I),
    "limitacion": re.compile(r"LIMITACI[OÓ]N|PROHIBICI[OÓ]N\s*DE\s*ENAJENAR|PATRIMONIO\s*DE\s*FAMILIA", re.I),
    "sucesion": re.compile(r"SUCESI[OÓ]N|ADJUDICACI[OÓ]N\s*POR\s*HERENCIA", re.I),
}


# ─── Modelos de entrada ─────────────────────────────────────────

class CTLRequest(BaseModel):
    lead_id: str = ""
    celular: str = ""
    matricula: str = ""
    ciudad: Optional[str] = None
    texto_ctl: Optional[str] = None


# ─── Lógica de validación ───────────────────────────────────────

def validar_ctl(data: CTLRequest) -> dict:
    ahora = datetime.now().isoformat()
    matricula = data.matricula.strip()
    texto_ctl = data.texto_ctl or ""

    if not matricula:
        return {"estado": "error", "error": "No se proporcionó matrícula inmobiliaria"}

    # ── Formato matrícula: XXX-NNNNN ─────────────────────────────
    formato_valido = bool(re.match(r"^[A-Z0-9]{2,4}-[0-9]{1,10}$", matricula, re.I))

    if not formato_valido:
        return {
            "estado": "error",
            "matricula": matricula,
            "formato_matricula_valido": False,
            "error": f"Formato inválido. Esperado: XXX-NNNNN (ej: 50N-12345678). Recibido: {matricula}",
        }

    # ── Fuentes manuales ─────────────────────────────────────────
    fuentes = [
        {
            "fuente": "SNR_RADICACION",
            "url": "https://radicacion.supernotariado.gov.co",
            "estado": "consulta_manual_requerida",
            "matricula": matricula,
            "instrucciones": f"Consultar matrícula {matricula} en https://radicacion.supernotariado.gov.co",
            "fecha_consulta": ahora,
        },
        {
            "fuente": "VUR",
            "url": "https://www.vur.gov.co",
            "estado": "consulta_manual_requerida",
            "matricula": matricula,
            "instrucciones": "Comprar CTL digital en https://www.vur.gov.co (~$20.600 COP). Login con cédula + clave.",
            "fecha_consulta": ahora,
        },
    ]

    # ── Análisis de texto CTL ────────────────────────────────────
    analisis_texto = None
    tiene_embargos = None
    tiene_hipotecas = None
    tiene_medidas_cautelares = None
    tiene_demandas = None
    tiene_falsa_tradicion = None
    tiene_tradicion = None
    tiene_propietarios = None
    anotaciones_riesgo: list[str] = []
    nivel_riesgo = ""
    es_viable = None
    recomendacion = ""

    if texto_ctl and len(texto_ctl) > 50:
        tiene_embargos = bool(PATRONES_RIESGO["embargo"].search(texto_ctl))
        tiene_hipotecas = bool(PATRONES_RIESGO["hipoteca"].search(texto_ctl))
        tiene_medidas_cautelares = bool(PATRONES_RIESGO["medida_cautelar"].search(texto_ctl))
        tiene_demandas = bool(PATRONES_RIESGO["demanda"].search(texto_ctl))
        tiene_falsa_tradicion = bool(PATRONES_RIESGO["falsa_tradicion"].search(texto_ctl))
        tiene_tradicion = bool(PATRONES_RIESGO["tradicion"].search(texto_ctl))
        tiene_propietarios = bool(PATRONES_RIESGO["propietario"].search(texto_ctl))

        if tiene_embargos:
            anotaciones_riesgo.append("embargo")
        if tiene_hipotecas:
            anotaciones_riesgo.append("hipoteca")
        if tiene_medidas_cautelares:
            anotaciones_riesgo.append("medida_cautelar")
        if tiene_demandas:
            anotaciones_riesgo.append("demanda")
        if tiene_falsa_tradicion:
            anotaciones_riesgo.append("falsa_tradicion")
        if PATRONES_RIESGO["limitacion"].search(texto_ctl):
            anotaciones_riesgo.append("limitacion")
        if PATRONES_RIESGO["sucesion"].search(texto_ctl):
            anotaciones_riesgo.append("sucesion")

        bloqueantes = tiene_embargos or tiene_falsa_tradicion
        if bloqueantes:
            nivel_riesgo = "alto"
            es_viable = False
            recomendacion = "BLOQUEADO: El inmueble tiene anotaciones críticas (embargo o falsa tradición)."
        elif len(anotaciones_riesgo) >= 2:
            nivel_riesgo = "medio"
            es_viable = True
            recomendacion = "Revisar anotaciones con abogado antes de aprobar."
        elif len(anotaciones_riesgo) == 1:
            nivel_riesgo = "bajo"
            es_viable = True
            recomendacion = "Anotación menor. Verificar que no afecte la operación."
        else:
            nivel_riesgo = "bajo"
            es_viable = True
            recomendacion = "Sin anotaciones de riesgo detectadas."

        analisis_texto = {
            "texto_analizado_caracteres": len(texto_ctl),
            "anotaciones_encontradas": anotaciones_riesgo,
            "nivel_riesgo": nivel_riesgo,
            "tiene_tradicion": tiene_tradicion,
            "tiene_propietarios": tiene_propietarios,
            "es_viable": es_viable,
            "recomendacion": recomendacion,
        }
    else:
        recomendacion = "Se requiere el texto/imagen del CTL para análisis completo. Comprar en VUR."

    return {
        "estado": "completado",
        "matricula": matricula,
        "formato_matricula_valido": formato_valido,
        "fuentes": fuentes,
        "analisis_texto": analisis_texto,
        "tiene_embargos": tiene_embargos,
        "tiene_hipotecas_vigentes": tiene_hipotecas,
        "tiene_medidas_cautelares": tiene_medidas_cautelares,
        "tiene_demandas": tiene_demandas,
        "tiene_falsa_tradicion": tiene_falsa_tradicion,
        "tiene_tradicion": tiene_tradicion,
        "tiene_propietarios": tiene_propietarios,
        "anotaciones_riesgo": anotaciones_riesgo,
        "nivel_riesgo": nivel_riesgo,
        "es_viable": es_viable,
        "recomendacion": recomendacion,
        "fecha_validacion": ahora,
    }
