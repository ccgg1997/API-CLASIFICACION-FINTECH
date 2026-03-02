"""
Credit Score / Perfil Financiero
==================================
Evalúa perfil financiero con cálculos de capacidad de endeudamiento.
Basado en: Ley 546/1999, Res. Ext. 3 BanRep.

Fuentes manuales:
  - DIAN MUISCA: https://muisca.dian.gov.co
  - Rama Judicial: https://procesos.ramajudicial.gov.co
  - RUES: https://www.rues.org.co

Nota: Para score formal se requiere DataCrédito/TransUnion (de pago).
"""

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# ─── Constantes regulatorias ────────────────────────────────────

SMMLV = 1_300_000
CUOTA_INGRESO_MAX = 0.30   # 30% Ley 546
DTI_MAX = 0.40              # 40%
VIS_LIMITE = 150 * SMMLV    # 150 SMMLV
LTV_VIS = 0.70
LTV_NO_VIS = 0.80


# ─── Modelos de entrada ─────────────────────────────────────────

class CreditRequest(BaseModel):
    lead_id: str = ""
    celular: str = ""
    cedula: str = ""
    nombre: Optional[str] = None
    ingresos_mensuales: float = 0
    egresos_mensuales: float = 0
    cuotas_vigentes: float = 0
    valor_inmueble: float = 0
    plazo_meses: int = 240  # 20 años default


# ─── Lógica de validación ───────────────────────────────────────

def evaluar_credito(data: CreditRequest) -> dict:
    ahora = datetime.now().isoformat()
    alertas: list[str] = []

    cedula = data.cedula
    if not cedula:
        return {"estado": "error", "error": "No se proporcionó cédula"}

    cedula_limpia = re.sub(r"[.\-\s]", "", cedula)

    ingresos = data.ingresos_mensuales
    egresos = data.egresos_mensuales
    cuotas_vigentes = data.cuotas_vigentes
    valor_inmueble = data.valor_inmueble
    plazo_meses = data.plazo_meses

    # ── Fuentes manuales ─────────────────────────────────────────
    fuentes_manuales = [
        {
            "fuente": "DIAN_MUISCA",
            "url": "https://muisca.dian.gov.co/WebRutMuisca/DefConsultaEstadoRUT.faces",
            "cedula": cedula_limpia,
            "estado": "consulta_manual_requerida",
            "instrucciones": f"Consultar RUT de cédula {cedula_limpia} en https://muisca.dian.gov.co",
            "fecha_consulta": ahora,
        },
        {
            "fuente": "RAMA_JUDICIAL_EJECUTIVOS",
            "url": "https://procesos.ramajudicial.gov.co",
            "cedula": cedula_limpia,
            "estado": "consulta_manual_requerida",
            "instrucciones": "Consultar procesos ejecutivos en https://procesos.ramajudicial.gov.co",
            "fecha_consulta": ahora,
        },
        {
            "fuente": "RUES",
            "url": "https://www.rues.org.co",
            "cedula": cedula_limpia,
            "estado": "consulta_manual_requerida",
            "instrucciones": "Consultar registro mercantil en https://www.rues.org.co",
            "fecha_consulta": ahora,
        },
    ]

    # ── Cálculos de capacidad ────────────────────────────────────
    capacidad = None
    score_interno = "pendiente"

    if ingresos > 0 and valor_inmueble > 0:
        es_vis = valor_inmueble <= VIS_LIMITE
        tipo_vivienda = "VIS" if es_vis else "No VIS"
        tasa_ea = 11.5 if es_vis else 12.5
        ltv_max = LTV_VIS if es_vis else LTV_NO_VIS
        monto_prestamo = valor_inmueble * ltv_max

        # Tasa mensual desde EA
        tasa_mensual = (1 + tasa_ea / 100) ** (1 / 12) - 1

        # Cuota estimada (amortización francesa)
        cuota_estimada = 0.0
        if plazo_meses > 0 and tasa_mensual > 0:
            cuota_estimada = (
                monto_prestamo
                * tasa_mensual
                * (1 + tasa_mensual) ** plazo_meses
                / ((1 + tasa_mensual) ** plazo_meses - 1)
            )

        # Cuota máxima por ingreso (30%)
        cuota_max_30 = ingresos * CUOTA_INGRESO_MAX

        # DTI
        deuda_total = cuota_estimada + cuotas_vigentes
        dti = deuda_total / ingresos if ingresos > 0 else 1.0

        # Cuota máxima real
        cuota_max_dti = (ingresos * DTI_MAX) - cuotas_vigentes
        cuota_max_real = max(0, min(cuota_max_30, cuota_max_dti))

        # Monto financiable estimado
        monto_financiable = 0.0
        if tasa_mensual > 0 and plazo_meses > 0:
            factor = (
                ((1 + tasa_mensual) ** plazo_meses - 1)
                / (tasa_mensual * (1 + tasa_mensual) ** plazo_meses)
            )
            monto_financiable = cuota_max_real * factor

        ratio_cuota = cuota_estimada / ingresos if ingresos > 0 else 1.0
        viable = ratio_cuota <= CUOTA_INGRESO_MAX and dti <= DTI_MAX

        if not viable:
            if ratio_cuota > CUOTA_INGRESO_MAX:
                alertas.append(
                    f"[CRITICO] Cuota/Ingreso ({ratio_cuota * 100:.1f}%) supera máximo (30%)"
                )
            if dti > DTI_MAX:
                alertas.append(f"[CRITICO] DTI ({dti * 100:.1f}%) supera máximo (40%)")

        # Score interno
        if viable and not alertas:
            score_interno = "bueno"
        elif viable:
            score_interno = "regular"
        else:
            score_interno = "deficiente"

        if ingresos < 2 * SMMLV:
            alertas.append(
                f"[ALERTA] Ingresos (${ingresos:,.0f}) menor a 2 SMMLV (${2 * SMMLV:,.0f})"
            )

        capacidad = {
            "viable": viable,
            "ingresos_mensuales": ingresos,
            "egresos_mensuales": egresos,
            "cuotas_creditos_vigentes": cuotas_vigentes,
            "cuota_maxima_vivienda_30pct": round(cuota_max_30),
            "capacidad_total_deuda_40pct": round(ingresos * DTI_MAX),
            "cuota_real_maxima": round(cuota_max_real),
            "monto_financiable_estimado": round(monto_financiable),
            "cuota_estimada": round(cuota_estimada),
            "plazo_meses": plazo_meses,
            "tasa_referencia_ea": f"{tasa_ea}%",
            "tipo_vivienda": tipo_vivienda,
            "ltv_maximo": f"{ltv_max * 100:.0f}%",
            "ratio_cuota_ingreso": f"{ratio_cuota * 100:.1f}%",
            "dti": f"{dti * 100:.1f}%",
            "regulacion": "Ley 546/1999, Res. Ext. 3 BanRep",
        }
    else:
        alertas.append("[ALERTA] Datos financieros incompletos para calcular capacidad")

    return {
        "estado": "completado",
        "score_interno": score_interno,
        "fuentes_manuales": fuentes_manuales,
        "capacidad_endeudamiento": capacidad,
        "alertas": alertas,
        "nota": "Este perfil NO reemplaza consulta a DataCrédito/TransUnion. Para score formal: https://www.datacredito.com.co",
        "fecha_evaluacion": ahora,
    }
