"""
Policy Engine - Motor de Politicas Sureti
=========================================
Evalua reglas de negocio y genera la clasificacion final del lead.
Recibe datos planos minimos, sin depender de otros endpoints.

Politicas:
  - Cedula: formato valido
  - Edad: min 18, fin credito max 75
  - Inmueble: cobertura, tipo (casa/apartamento), valor min $100M
  - Financiera: cuota/ingreso <= 30%, DTI <= 40%
  - CTL: sin embargos ni falsa tradicion

Clasificacion del lead:
  NO_VIABLE         -> >= 1 rechazo
  VIABLE            -> 0 rechazos y >= 1 nota
  ALTAMENTE_VIABLE  -> 0 rechazos y 0 notas
"""

import re
import unicodedata
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# Constantes regulatorias
SMMLV = 1_300_000

REGLAS = {
    "edad_minima": 18,
    "edad_maxima_fin_credito": 75,
    "ciudades_cobertura": [
        "bogota",
        "medellin",
        "cali",
        "barranquilla",
        "cartagena",
        "bucaramanga",
        "pereira",
        "manizales",
        "santa marta",
        "ibague",
    ],
    "tipos_inmueble_aceptados": ["casa", "apartamento", "apto", "apt"],
    "valor_minimo_inmueble": 100_000_000,
    "cuota_ingreso_maximo": 0.30,
    "dti_maximo": 0.40,
    "ltv_vis": 0.70,
    "ltv_no_vis": 0.80,
    "vis_tope": 150 * SMMLV,
    "vip_tope": 90 * SMMLV,
}


class PolicyRequest(BaseModel):
    lead_id: str = ""
    celular: str = ""
    cedula: str
    fecha_nacimiento: Optional[str] = None
    ciudad: Optional[str] = None
    tipo_inmueble: Optional[str] = None
    valor_inmueble: float = 0
    matricula: Optional[str] = None
    texto_ctl: Optional[str] = None
    ingresos_mensuales: float = 0
    egresos_mensuales: float = 0
    cuotas_vigentes: float = 0
    plazo_meses: int = 240


def _normalizar(texto: str) -> str:
    t = texto.lower().strip()
    t = unicodedata.normalize("NFD", t)
    t = re.sub(r"[\u0300-\u036f]", "", t)
    return t


def evaluar_politicas(data: PolicyRequest) -> dict:
    ahora = datetime.now().isoformat()

    politicas_evaluadas: list[dict] = []
    rechazos: list[str] = []
    notas: list[str] = []

    # 1) Politica de cedula
    cedula_limpia = re.sub(r"[.\-\s]", "", data.cedula)
    formato_valido = bool(re.match(r"^\d{6,12}$", cedula_limpia))

    pol_cedula = {"nombre": "cedula", "cumple": formato_valido, "motivo": ""}
    if not formato_valido:
        pol_cedula["motivo"] = f"Formato de cedula invalido: {data.cedula}"
        rechazos.append("[RECHAZO] Formato de cedula invalido")
    else:
        pol_cedula["motivo"] = f"Cedula {cedula_limpia} con formato valido [OK]"
    politicas_evaluadas.append(pol_cedula)

    # 2) Politica de edad
    pol_edad = {"nombre": "edad", "cumple": False, "motivo": ""}

    if not data.fecha_nacimiento:
        pol_edad["motivo"] = "Fecha de nacimiento no proporcionada"
        notas.append("[ALERTA] Falta fecha de nacimiento para validar edad")
    else:
        try:
            nac = datetime.fromisoformat(data.fecha_nacimiento)
            hoy = datetime.now()
            edad = hoy.year - nac.year - ((hoy.month, hoy.day) < (nac.month, nac.day))
            plazo_anios = (data.plazo_meses // 12) + (1 if data.plazo_meses % 12 else 0)
            edad_fin = edad + plazo_anios

            if edad < REGLAS["edad_minima"]:
                pol_edad["motivo"] = f"Edad {edad} < minima {REGLAS['edad_minima']}"
                rechazos.append(f"[RECHAZO] {pol_edad['motivo']}")
            elif edad_fin > REGLAS["edad_maxima_fin_credito"]:
                pol_edad["motivo"] = (
                    f"Edad al fin del credito {edad_fin} > maxima {REGLAS['edad_maxima_fin_credito']}"
                )
                rechazos.append(f"[RECHAZO] {pol_edad['motivo']}")
            else:
                pol_edad["cumple"] = True
                pol_edad["motivo"] = f"Edad {edad}, fin credito {edad_fin} [OK]"
        except ValueError:
            pol_edad["motivo"] = "Formato de fecha de nacimiento invalido"
            notas.append("[ALERTA] Formato de fecha de nacimiento invalido")

    politicas_evaluadas.append(pol_edad)

    # 3) Politica de inmueble
    pol_inmueble = {"nombre": "inmueble", "cumple": False, "motivo": ""}
    motivos_inm: list[str] = []
    cumple_inm = True

    if data.ciudad:
        ciudad_norm = _normalizar(data.ciudad)
        if not any(ciudad_norm.find(_normalizar(c)) >= 0 for c in REGLAS["ciudades_cobertura"]):
            cumple_inm = False
            motivos_inm.append(f'"{data.ciudad}" fuera de cobertura')
            rechazos.append("[RECHAZO] Inmueble fuera de zona de cobertura")
    else:
        notas.append("[ALERTA] Ciudad no proporcionada")

    if data.tipo_inmueble:
        tipo = data.tipo_inmueble.lower().strip()
        if tipo not in REGLAS["tipos_inmueble_aceptados"]:
            cumple_inm = False
            motivos_inm.append(f'Tipo "{data.tipo_inmueble}" no aceptado')
            rechazos.append("[RECHAZO] Tipo de inmueble no elegible")
    else:
        notas.append("[ALERTA] Tipo de inmueble no proporcionado")

    if data.valor_inmueble > 0 and data.valor_inmueble < REGLAS["valor_minimo_inmueble"]:
        cumple_inm = False
        motivos_inm.append(
            f"Valor ${data.valor_inmueble:,.0f} < minimo ${REGLAS['valor_minimo_inmueble']:,.0f}"
        )
        rechazos.append("[RECHAZO] Valor del inmueble por debajo del minimo")

    pol_inmueble["cumple"] = cumple_inm
    pol_inmueble["motivo"] = "Inmueble cumple politicas [OK]" if cumple_inm else "; ".join(motivos_inm)
    politicas_evaluadas.append(pol_inmueble)

    # 4) Politica financiera
    pol_fin = {"nombre": "financiera", "cumple": False, "motivo": ""}
    motivos_fin: list[str] = []
    cumple_fin = True

    if data.ingresos_mensuales > 0 and data.valor_inmueble > 0:
        es_vis = data.valor_inmueble <= REGLAS["vis_tope"]
        tasa_ea = 11.5 if es_vis else 12.5
        ltv_max = REGLAS["ltv_vis"] if es_vis else REGLAS["ltv_no_vis"]
        monto = data.valor_inmueble * ltv_max
        tasa_m = (1 + tasa_ea / 100) ** (1 / 12) - 1

        cuota = 0.0
        if data.plazo_meses > 0 and tasa_m > 0:
            cuota = (
                monto
                * tasa_m
                * (1 + tasa_m) ** data.plazo_meses
                / ((1 + tasa_m) ** data.plazo_meses - 1)
            )

        ratio = cuota / data.ingresos_mensuales
        deuda = cuota + data.cuotas_vigentes
        dti = deuda / data.ingresos_mensuales

        if ratio > REGLAS["cuota_ingreso_maximo"]:
            cumple_fin = False
            motivos_fin.append(
                f"Cuota/ingreso {ratio * 100:.1f}% > {REGLAS['cuota_ingreso_maximo'] * 100}%"
            )
            rechazos.append("[RECHAZO] Ratio cuota/ingreso excede el maximo")

        if dti > REGLAS["dti_maximo"]:
            cumple_fin = False
            motivos_fin.append(f"DTI {dti * 100:.1f}% > {REGLAS['dti_maximo'] * 100}%")
            rechazos.append("[RECHAZO] DTI excede el maximo")
    else:
        notas.append("[ALERTA] Datos financieros insuficientes para evaluar")

    pol_fin["cumple"] = cumple_fin
    pol_fin["motivo"] = "Perfil financiero cumple [OK]" if cumple_fin else "; ".join(motivos_fin)
    politicas_evaluadas.append(pol_fin)

    # 5) Politica CTL
    pol_ctl = {"nombre": "ctl", "cumple": True, "motivo": "Sin datos de CTL - pendiente"}

    if data.texto_ctl and len(data.texto_ctl) > 50:
        tiene_embargo = bool(re.search(r"EMBARGO|SECUESTRO", data.texto_ctl, re.I))
        tiene_falsa = bool(re.search(r"FALSA\s*TRADICI", data.texto_ctl, re.I))

        if tiene_falsa:
            pol_ctl["cumple"] = False
            pol_ctl["motivo"] = "Falsa tradicion detectada"
            rechazos.append("[RECHAZO] BLOQUEANTE: Falsa tradicion en CTL")
        elif tiene_embargo:
            pol_ctl["cumple"] = False
            pol_ctl["motivo"] = "Embargo vigente detectado"
            rechazos.append("[RECHAZO] BLOQUEANTE: Embargo en CTL")
        else:
            pol_ctl["motivo"] = "CTL sin anotaciones bloqueantes [OK]"
    elif data.matricula:
        notas.append("[ALERTA] Se proporciono matricula pero no texto CTL - comprar en VUR")

    politicas_evaluadas.append(pol_ctl)

    # Clasificacion final del lead
    rechazos_unicos = list(dict.fromkeys(rechazos))
    notas_unicas = list(dict.fromkeys(notas))

    if rechazos_unicos:
        clasificacion_lead = "NO_VIABLE"
        motivo_clasificacion = " | ".join(rechazos_unicos)
    elif notas_unicas:
        clasificacion_lead = "VIABLE"
        motivo_clasificacion = f"Viable con {len(notas_unicas)} observacion(es)"
    else:
        clasificacion_lead = "ALTAMENTE_VIABLE"
        motivo_clasificacion = "Cumple todas las politicas sin observaciones"

    # Clasificacion de inmueble
    clasificacion_inmueble = "no_vis"
    if data.valor_inmueble > 0:
        if data.valor_inmueble <= REGLAS["vip_tope"]:
            clasificacion_inmueble = "vip"
        elif data.valor_inmueble <= REGLAS["vis_tope"]:
            clasificacion_inmueble = "vis"

    return {
        "estado": "completado",
        "clasificacion_lead": clasificacion_lead,
        "motivo_clasificacion": motivo_clasificacion,
        "politicas_evaluadas": politicas_evaluadas,
        "rechazos": rechazos_unicos,
        "notas": notas_unicas,
        "clasificacion_inmueble": clasificacion_inmueble,
        "constantes_aplicadas": {
            "smmlv": SMMLV,
            "ley": "546/1999",
            "cuota_ingreso_max": "30%",
            "dti_max": "40%",
            "ltv_vis": "70%",
            "ltv_no_vis": "80%",
            "valor_minimo_inmueble": REGLAS["valor_minimo_inmueble"],
            "vis_tope": REGLAS["vis_tope"],
            "vip_tope": REGLAS["vip_tope"],
        },
        "fecha_evaluacion": ahora,
    }
