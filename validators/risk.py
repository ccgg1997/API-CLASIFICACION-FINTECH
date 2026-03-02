"""
Risk Analyzer — Analizador de Riesgo
======================================
Recibe datos planos mínimos y calcula un score ponderado de riesgo.
NO depende de otros endpoints — cada dimensión se evalúa internamente.

Input mínimo: cedula
Input completo: cedula + datos de inmueble + datos financieros + matrícula

Score ponderado:
  identidad:     20%
  inmueble:      15%
  CTL:           20%
  financiero:    25%
  LTV:           10%
  cuota/ingreso: 10%
"""

import re
import unicodedata
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# ─── Constantes ──────────────────────────────────────────────────

PESOS = {
    "identidad": 0.20,
    "inmueble": 0.15,
    "ctl": 0.20,
    "financiero": 0.25,
    "ltv": 0.10,
    "cuota_ingreso": 0.10,
}

SMMLV = 1_300_000
CUOTA_INGRESO_MAX = 0.30
DTI_MAX = 0.40
VIS_LIMITE = 150 * SMMLV
LTV_VIS = 0.70
LTV_NO_VIS = 0.80
VALOR_MINIMO = 100_000_000

CIUDADES_COBERTURA = [
    "bogota", "medellin", "cali", "barranquilla", "bucaramanga",
    "cartagena", "pereira", "manizales", "santa marta", "ibague",
]

TIPOS_ACEPTADOS = ["casa", "apartamento", "apto", "apt"]


# ─── Modelo de entrada ──────────────────────────────────────────

class RiskRequest(BaseModel):
    lead_id: str = ""
    celular: str = ""
    cedula: str
    ciudad: Optional[str] = None
    tipo_inmueble: Optional[str] = None
    valor_inmueble: float = 0
    matricula: Optional[str] = None
    texto_ctl: Optional[str] = None
    ingresos_mensuales: float = 0
    egresos_mensuales: float = 0
    cuotas_vigentes: float = 0
    plazo_meses: int = 240


# ─── Helpers ─────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    t = texto.lower().strip()
    t = unicodedata.normalize("NFD", t)
    t = re.sub(r"[\u0300-\u036f]", "", t)
    return t


def _calcular_cuota(monto: float, tasa_mensual: float, plazo: int) -> float:
    if plazo <= 0 or tasa_mensual <= 0:
        return 0.0
    return (
        monto
        * tasa_mensual
        * (1 + tasa_mensual) ** plazo
        / ((1 + tasa_mensual) ** plazo - 1)
    )


# ─── Lógica de análisis ─────────────────────────────────────────

def analizar_riesgo(data: RiskRequest) -> dict:
    ahora = datetime.now().isoformat()
    alertas: list[str] = []

    # ── Score: Identidad (20%) ───────────────────────────────────
    score_identidad = 50
    cedula_limpia = re.sub(r"[.\-\s]", "", data.cedula)
    formato_valido = bool(re.match(r"^\d{6,12}$", cedula_limpia))

    if formato_valido:
        score_identidad = 70  # Formato OK, pendiente verificación manual
    else:
        score_identidad = 0
        alertas.append("[CRITICO] Formato de cédula inválido")

    # ── Score: Inmueble (15%) ────────────────────────────────────
    score_inmueble = 50
    if data.ciudad:
        ciudad_norm = _normalizar(data.ciudad)
        en_cobertura = any(ciudad_norm.find(_normalizar(c)) >= 0 for c in CIUDADES_COBERTURA)
        tipo_ok = (
            any(t in (data.tipo_inmueble or "").lower() for t in TIPOS_ACEPTADOS)
            if data.tipo_inmueble
            else True
        )
        valor_ok = data.valor_inmueble >= VALOR_MINIMO if data.valor_inmueble > 0 else True

        if en_cobertura and tipo_ok and valor_ok:
            score_inmueble = 100
        else:
            score_inmueble = 0
            if not en_cobertura:
                alertas.append(f"[CRITICO] '{data.ciudad}' fuera de cobertura")
            if not tipo_ok:
                alertas.append(f"[CRITICO] Tipo '{data.tipo_inmueble}' no aceptado")
            if not valor_ok:
                alertas.append("[CRITICO] Valor inmueble por debajo del mínimo")

    # ── Score: CTL (20%) ─────────────────────────────────────────
    score_ctl = 50
    if data.matricula:
        formato_mat = bool(re.match(r"^[A-Z0-9]{2,4}-[0-9]{1,10}$", data.matricula, re.I))
        if not formato_mat:
            score_ctl = 30
            alertas.append("[ALERTA] Formato de matrícula inválido")
        else:
            score_ctl = 70  # Formato OK, pendiente análisis real

            if data.texto_ctl and len(data.texto_ctl) > 50:
                tiene_embargo = bool(re.search(r"EMBARGO|SECUESTRO", data.texto_ctl, re.I))
                tiene_falsa = bool(re.search(r"FALSA\s*TRADICI", data.texto_ctl, re.I))

                if tiene_falsa:
                    score_ctl = 0
                    alertas.append("[CRITICO] BLOQUEANTE: Falsa tradición en CTL")
                elif tiene_embargo:
                    score_ctl = 10
                    alertas.append("[CRITICO] BLOQUEANTE: Embargo sobre el inmueble")
                else:
                    score_ctl = 100

    # ── Score: Financiero (25%) ──────────────────────────────────
    score_financiero = 50
    ratio = 0.0
    dti = 0.0

    if data.ingresos_mensuales > 0 and data.valor_inmueble > 0:
        es_vis = data.valor_inmueble <= VIS_LIMITE
        tasa_ea = 11.5 if es_vis else 12.5
        ltv_max = LTV_VIS if es_vis else LTV_NO_VIS
        monto = data.valor_inmueble * ltv_max
        tasa_m = (1 + tasa_ea / 100) ** (1 / 12) - 1

        cuota = _calcular_cuota(monto, tasa_m, data.plazo_meses)
        ratio = cuota / data.ingresos_mensuales if data.ingresos_mensuales > 0 else 1
        deuda_total = cuota + data.cuotas_vigentes
        dti = deuda_total / data.ingresos_mensuales if data.ingresos_mensuales > 0 else 1

        viable = ratio <= CUOTA_INGRESO_MAX and dti <= DTI_MAX

        if viable:
            score_financiero = 90
        elif ratio <= 0.35 and dti <= 0.45:
            score_financiero = 60
            alertas.append(f"[ALERTA] Ratio cuota/ingreso ({ratio * 100:.1f}%) cercano al límite")
        else:
            score_financiero = 20
            alertas.append(
                f"[CRITICO] Capacidad financiera insuficiente "
                f"(cuota/ingreso: {ratio * 100:.1f}%, DTI: {dti * 100:.1f}%)"
            )

    # ── Score: LTV (10%) ─────────────────────────────────────────
    score_ltv = 50
    if data.valor_inmueble > 0 and data.ingresos_mensuales > 0:
        es_vis = data.valor_inmueble <= VIS_LIMITE
        score_ltv = 80 if es_vis else 70

    # ── Score: Cuota/Ingreso (10%) ───────────────────────────────
    score_cuota_ingreso = 50
    if data.ingresos_mensuales > 0 and data.valor_inmueble > 0:
        if ratio <= 0.25:
            score_cuota_ingreso = 100
        elif ratio <= 0.30:
            score_cuota_ingreso = 80
        elif ratio <= 0.35:
            score_cuota_ingreso = 50
        else:
            score_cuota_ingreso = 20

    # ── Score total ponderado ────────────────────────────────────
    scores_parciales = {
        "identidad": {
            "score": score_identidad,
            "peso": PESOS["identidad"],
            "ponderado": round(score_identidad * PESOS["identidad"], 1),
        },
        "inmueble": {
            "score": score_inmueble,
            "peso": PESOS["inmueble"],
            "ponderado": round(score_inmueble * PESOS["inmueble"], 1),
        },
        "ctl": {
            "score": score_ctl,
            "peso": PESOS["ctl"],
            "ponderado": round(score_ctl * PESOS["ctl"], 1),
        },
        "financiero": {
            "score": score_financiero,
            "peso": PESOS["financiero"],
            "ponderado": round(score_financiero * PESOS["financiero"], 1),
        },
        "ltv": {
            "score": score_ltv,
            "peso": PESOS["ltv"],
            "ponderado": round(score_ltv * PESOS["ltv"], 1),
        },
        "cuota_ingreso": {
            "score": score_cuota_ingreso,
            "peso": PESOS["cuota_ingreso"],
            "ponderado": round(score_cuota_ingreso * PESOS["cuota_ingreso"], 1),
        },
    }

    score_total = round(sum(s["ponderado"] for s in scores_parciales.values()))

    # ── Clasificación ────────────────────────────────────────────
    if score_total >= 80:
        nivel_riesgo, semaforo, clasificacion = "bajo", "VERDE", "ALTAMENTE_VIABLE"
    elif score_total >= 60:
        nivel_riesgo, semaforo, clasificacion = "medio", "AMARILLO", "VIABLE"
    elif score_total >= 40:
        nivel_riesgo, semaforo, clasificacion = "alto", "ROJO", "NO_VIABLE"
    else:
        nivel_riesgo, semaforo, clasificacion = "critico", "ROJO", "NO_VIABLE"

    # Bloqueantes fuerzan rechazo
    if any("BLOQUEANTE" in a for a in alertas):
        nivel_riesgo, semaforo, clasificacion = "critico", "ROJO", "NO_VIABLE"

    alertas_unicas = list(dict.fromkeys(alertas))

    return {
        "estado": "completado",
        "score_total": score_total,
        "nivel_riesgo": nivel_riesgo,
        "semaforo": semaforo,
        "clasificacion": clasificacion,
        "scores_parciales": scores_parciales,
        "alertas": alertas_unicas,
        "fecha_analisis": ahora,
    }
