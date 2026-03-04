"""
Sureti - API Unificada de Validaciones Hipotecarias
===================================================
6 endpoints de evaluacion + endpoints de leads para dashboard.

Objetivo operacional:
- Devolver el resultado por API.
- Persistir SIEMPRE en BD (Mongo) por `lead_id` (telefono).
- Permitir que el dashboard lea desde BD y refresque datos.
"""

from __future__ import annotations

import os
import re
from copy import deepcopy
from datetime import datetime
from typing import Any, Optional

from fastapi import Body, FastAPI, Query
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware

from validators.credit import CreditRequest, evaluar_credito
from validators.ctl import CTLRequest, validar_ctl
from validators.identity import IdentityRequest, validar_identidad
from validators.policy import PolicyRequest, evaluar_politicas
from validators.property import PropertyRequest, validar_inmueble
from validators.risk import RiskRequest, analizar_riesgo

try:
    from pymongo import MongoClient
except Exception:  # pragma: no cover
    MongoClient = None


MONGO_URL = os.getenv("MONGO_URL", "")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "sureti")
MONGO_COLLECTION_LEADS = os.getenv("MONGO_COLLECTION_LEADS", "leads")

_mongo_client = None
_mongo_collection = None


app = FastAPI(
    title="Sureti - API de Validaciones Hipotecarias",
    description=(
        "API con endpoints de validacion y persistencia en Mongo. "
        "Clasificacion final: NO_VIABLE, VIABLE, ALTAMENTE_VIABLE."
    ),
    version="3.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now() -> datetime:
    return datetime.utcnow()


def _now_iso() -> str:
    return _now().isoformat()


def _normalize_phone(value: Any) -> str:
    if value is None:
        return ""
    cleaned = re.sub(r"\D", "", str(value))
    return cleaned.strip()


def _pick_str(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        elif isinstance(value, (int, float)):
            return str(value)
    return ""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_leads_collection():
    global _mongo_client, _mongo_collection
    if _mongo_collection is not None:
        return _mongo_collection
    if not MONGO_URL or MongoClient is None:
        return None
    try:
        _mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        _mongo_collection = _mongo_client[MONGO_DB_NAME][MONGO_COLLECTION_LEADS]
        return _mongo_collection
    except Exception:
        _mongo_client = None
        _mongo_collection = None
        return None


def _default_lead_document(
    lead_id: str,
    celular: str,
    nombre_contacto: str = "",
    origen: str = "whatsapp",
) -> dict[str, Any]:
    now = _now()
    return {
        "lead_id": lead_id,
        "origen": origen or "whatsapp",
        "contacto": {
            "nombre": nombre_contacto,
            "celular": celular,
            "email": "",
            "es_el_titular": True,
        },
        "persona": {
            "cedula": "",
            "nombre_completo": "",
            "primer_nombre": "",
            "segundo_nombre": "",
            "primer_apellido": "",
            "segundo_apellido": "",
            "fecha_nacimiento": None,
            "edad": None,
            "telefono": "",
            "email": "",
            "direccion_residencia": "",
            "ciudad_residencia": "",
        },
        "financiero": {
            "ingresos_mensuales": 0,
            "egresos_mensuales": 0,
            "cuotas_creditos_vigentes": 0,
            "ocupacion": "",
            "empresa": "",
            "antiguedad_laboral_meses": 0,
            "otros_ingresos": 0,
        },
        "inmueble": {
            "ciudad": "",
            "departamento": "",
            "tipo_inmueble": "",
            "direccion": "",
            "valor_inmueble": 0,
            "matricula_inmobiliaria": "",
            "chip": "",
            "estrato": 0,
            "area_m2": 0,
            "antiguedad_anos": 0,
        },
        "credito": {
            "monto_solicitado": 0,
            "plazo_meses": 0,
            "tipo_credito": "",
            "tipo_tasa": "",
            "destino": "",
        },
        "estado": "nuevo",
        "etapa": "contacto_inicial",
        "prioridad": "media",
        "puntaje_lead": 0,
        "validaciones": {
            "identidad": {"estado": "pendiente"},
            "inmueble_validacion": {"estado": "pendiente"},
            "ctl": {"estado": "pendiente"},
            "titularidad": {"estado": "pendiente"},
            "perfil_financiero": {"estado": "pendiente"},
            "riesgo": {"estado": "pendiente"},
            "politicas": {"estado": "pendiente"},
        },
        "decision_sureti": None,
        "documentos": [],
        "historial": [
            {
                "estado": "nuevo",
                "accion": "Lead recibido por API",
                "detalle": f"contacto: {celular} ({nombre_contacto or 'sin nombre'})",
                "timestamp": now,
            }
        ],
        "created_at": now,
        "updated_at": now,
    }


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _resolve_lead_id_from_payload(payload: dict[str, Any]) -> tuple[str, str]:
    contacto = _as_dict(payload.get("contacto"))
    lead_id = _pick_str(
        payload.get("lead_id"),
        payload.get("from"),
        payload.get("id"),
        contacto.get("celular"),
        payload.get("celular"),
    )
    celular = _pick_str(contacto.get("celular"), payload.get("celular"), payload.get("from"), lead_id)
    lead_id = _normalize_phone(lead_id)
    celular = _normalize_phone(celular)
    if not lead_id and celular:
        lead_id = celular
    if not celular and lead_id:
        celular = lead_id
    return lead_id, celular


def _resolve_lead_id_from_request(data: Any) -> tuple[str, str]:
    lead_id = _normalize_phone(_pick_str(getattr(data, "lead_id", ""), getattr(data, "celular", "")))
    celular = _normalize_phone(_pick_str(getattr(data, "celular", ""), getattr(data, "lead_id", "")))
    if not lead_id and celular:
        lead_id = celular
    if not celular and lead_id:
        celular = lead_id
    return lead_id, celular


def _build_policy_request_from_lead_doc(lead: dict[str, Any]) -> PolicyRequest:
    persona = _as_dict(lead.get("persona"))
    financiero = _as_dict(lead.get("financiero"))
    inmueble = _as_dict(lead.get("inmueble"))
    credito = _as_dict(lead.get("credito"))

    fecha_nacimiento = persona.get("fecha_nacimiento")
    if isinstance(fecha_nacimiento, datetime):
        fecha_nacimiento = fecha_nacimiento.date().isoformat()
    else:
        fecha_nacimiento = _pick_str(fecha_nacimiento) or None

    return PolicyRequest(
        lead_id=_pick_str(lead.get("lead_id")),
        celular=_pick_str(_as_dict(lead.get("contacto")).get("celular"), lead.get("lead_id")),
        cedula=_pick_str(persona.get("cedula"), lead.get("cedula")),
        fecha_nacimiento=fecha_nacimiento,
        ciudad=_pick_str(inmueble.get("ciudad"), persona.get("ciudad_residencia")) or None,
        tipo_inmueble=_pick_str(inmueble.get("tipo_inmueble")) or None,
        valor_inmueble=_as_float(inmueble.get("valor_inmueble"), 0),
        matricula=_pick_str(inmueble.get("matricula_inmobiliaria")) or None,
        texto_ctl=_pick_str(_as_dict(_as_dict(lead.get("validaciones")).get("ctl")).get("texto_ctl")) or None,
        ingresos_mensuales=_as_float(financiero.get("ingresos_mensuales"), 0),
        egresos_mensuales=_as_float(financiero.get("egresos_mensuales"), 0),
        cuotas_vigentes=_as_float(financiero.get("cuotas_creditos_vigentes"), 0),
        plazo_meses=_as_int(credito.get("plazo_meses"), 240),
    )


def _build_provisional_policy_result(reason: str) -> dict[str, Any]:
    now = _now_iso()
    return {
        "estado": "completado",
        "clasificacion_lead": "VIABLE",
        "motivo_clasificacion": reason,
        "politicas_evaluadas": [
            {"nombre": "cedula", "cumple": False, "motivo": reason},
            {"nombre": "edad", "cumple": False, "motivo": "Pendiente por datos incompletos"},
            {"nombre": "inmueble", "cumple": False, "motivo": "Pendiente por datos incompletos"},
            {"nombre": "financiera", "cumple": False, "motivo": "Pendiente por datos incompletos"},
            {"nombre": "ctl", "cumple": False, "motivo": "Pendiente por datos incompletos"},
        ],
        "rechazos": [],
        "notas": [f"[ALERTA] {reason}"],
        "fecha_evaluacion": now,
        "es_provisional": True,
    }


def _classification_meta(clasificacion: str) -> tuple[str, int, str]:
    normalized = (clasificacion or "").upper()
    if normalized == "ALTAMENTE_VIABLE":
        return "VERDE", 90, "alta"
    if normalized == "VIABLE":
        return "AMARILLO", 65, "media"
    return "ROJO", 25, "alta"


def _policy_result_to_documents(policy_result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    clasificacion = _pick_str(policy_result.get("clasificacion_lead"), "VIABLE").upper()
    motivo = _pick_str(policy_result.get("motivo_clasificacion"), "Clasificacion calculada por politica")
    semaforo, score, prioridad = _classification_meta(clasificacion)

    politicas_evaluadas = policy_result.get("politicas_evaluadas", [])
    politicas_map = {
        item.get("nombre"): item
        for item in politicas_evaluadas
        if isinstance(item, dict) and item.get("nombre")
    }
    rechazos = [x for x in policy_result.get("rechazos", []) if isinstance(x, str)]
    notas = [x for x in policy_result.get("notas", []) if isinstance(x, str)]
    alertas = rechazos + notas

    criterios_evaluados = [
        {
            "aspecto": "identidad",
            "descripcion": "Formato de cedula y restricciones de identidad",
            "resultado": "cumple" if _as_dict(politicas_map.get("cedula")).get("cumple") else "pendiente",
        },
        {
            "aspecto": "edad",
            "descripcion": "Edad minima y edad maxima al finalizar credito",
            "resultado": "cumple" if _as_dict(politicas_map.get("edad")).get("cumple") else "pendiente",
        },
        {
            "aspecto": "inmueble",
            "descripcion": "Cobertura, tipo de inmueble y valor minimo",
            "resultado": "cumple" if _as_dict(politicas_map.get("inmueble")).get("cumple") else "pendiente",
        },
        {
            "aspecto": "financiero",
            "descripcion": "Cuota/ingreso y DTI segun politica",
            "resultado": "cumple" if _as_dict(politicas_map.get("financiera")).get("cumple") else "pendiente",
        },
        {
            "aspecto": "ctl",
            "descripcion": "Riesgos registrales y anotaciones en CTL",
            "resultado": "cumple" if _as_dict(politicas_map.get("ctl")).get("cumple") else "pendiente",
        },
    ]

    politicas_doc = {
        "estado": "completado",
        "decision": clasificacion,
        "clasificacion": clasificacion,
        "semaforo": semaforo,
        "accion_recomendada": motivo,
        "politica_edad": politicas_map.get("edad"),
        "politica_inmueble": politicas_map.get("inmueble"),
        "politica_financiera": politicas_map.get("financiera"),
        "politica_antecedentes": politicas_map.get("ctl"),
        "traza": politicas_evaluadas,
        "rechazos": rechazos,
        "notas": notas,
        "alertas": alertas,
        "total_criticas": len(rechazos),
        "total_moderadas": len(notas),
        "todas_politicas_cumplen": clasificacion == "ALTAMENTE_VIABLE",
        "tiene_bloqueante": clasificacion == "NO_VIABLE",
        "criterios_evaluados": criterios_evaluados,
        "tipo_evaluacion": "provisional" if policy_result.get("es_provisional") else "final",
        "fecha_evaluacion": _now(),
    }

    decision_doc = {
        "decision": clasificacion,
        "clasificacion": clasificacion,
        "semaforo": semaforo,
        "score_riesgo": score,
        "accion": motivo,
        "asignado_a": "",
        "comentarios": "Generado automaticamente por endpoint de politicas",
        "fecha_decision": _now(),
    }

    return politicas_doc, decision_doc, prioridad


def _apply_policy_on_lead(lead: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    policy_request = _build_policy_request_from_lead_doc(lead)
    if not _pick_str(policy_request.cedula):
        policy_result = _build_provisional_policy_result(
            "Falta cedula. Evaluacion provisional con datos parciales."
        )
    else:
        policy_result = evaluar_politicas(policy_request)
    return _policy_result_to_documents(policy_result)


def _append_history(
    action_state: str,
    action: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "estado": action_state,
        "accion": action,
        "detalle": detail,
        "timestamp": _now(),
    }


def _should_refresh_identity(lead: dict[str, Any]) -> bool:
    validaciones = _as_dict(lead.get("validaciones"))
    identidad = _as_dict(validaciones.get("identidad"))
    estado = _pick_str(identidad.get("estado")).lower()

    # Si no se ha ejecutado o quedo pendiente/error, intentar de nuevo.
    if estado in {"", "pendiente", "error"}:
        return True
    if estado != "completado":
        return True

    # Si no hay evidencia de consulta real OFAC/ONU, volver a consultar.
    listas = _as_dict(identidad.get("listas_restrictivas"))
    ofac = _as_dict(listas.get("ofac"))
    onu = _as_dict(listas.get("onu"))
    ofac_estado = _pick_str(ofac.get("estado")).lower()
    onu_estado = _pick_str(onu.get("estado")).lower()

    return not (ofac_estado == "consultado" and onu_estado == "consultado")


def _persist_validation_result(
    lead_id: str,
    celular: str,
    validation_key: str,
    validation_result: dict[str, Any],
    action: str,
    detail: str,
    extra_set: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    collection = _get_leads_collection()
    updated_paths = [f"validaciones.{validation_key}", "lead_id", "contacto.celular", "updated_at"]
    if extra_set:
        updated_paths.extend(list(extra_set.keys()))

    if collection is None:
        return {"persistido": False, "campos_actualizados": updated_paths, "error": "Mongo no configurado"}

    if not lead_id:
        return {
            "persistido": False,
            "campos_actualizados": updated_paths,
            "error": "Falta lead_id/celular para ubicar el documento",
        }

    celular_resuelto = _pick_str(celular, lead_id)
    default_doc = _default_lead_document(lead_id, celular_resuelto or lead_id)
    set_payload: dict[str, Any] = {
        f"validaciones.{validation_key}": validation_result,
        "lead_id": lead_id,
        "contacto.celular": celular_resuelto or lead_id,
        "updated_at": _now(),
    }
    if extra_set:
        set_payload.update(extra_set)

    collection.update_one(
        {"lead_id": lead_id},
        {
            "$setOnInsert": default_doc,
            "$set": set_payload,
            "$push": {
                "historial": _append_history("validando", action, detail)
            },
        },
        upsert=True,
    )
    return {"persistido": True, "campos_actualizados": updated_paths}


def _ensure_lead_for_upsert(payload: dict[str, Any]) -> tuple[Optional[dict[str, Any]], str]:
    lead_id, celular = _resolve_lead_id_from_payload(payload)
    if not lead_id:
        return None, "No se pudo resolver lead_id/telefono"

    collection = _get_leads_collection()
    if collection is None:
        return None, "Mongo no configurado"

    existing = collection.find_one({"lead_id": lead_id})
    if existing:
        existing.pop("_id", None)
        base_doc = existing
    else:
        contacto = _as_dict(payload.get("contacto"))
        base_doc = _default_lead_document(
            lead_id=lead_id,
            celular=celular,
            nombre_contacto=_pick_str(contacto.get("nombre"), payload.get("pushName")),
            origen=_pick_str(payload.get("origen"), "whatsapp"),
        )

    merged = deepcopy(base_doc)
    _deep_merge(merged, payload)
    merged["lead_id"] = lead_id
    merged["updated_at"] = _now()
    merged.setdefault("created_at", _now())
    merged.setdefault("origen", "whatsapp")
    merged.setdefault("contacto", {})
    merged["contacto"]["celular"] = _pick_str(merged["contacto"].get("celular"), celular, lead_id)

    merged.setdefault("historial", [])
    merged["historial"].append(
        _append_history(
            action_state="nuevo" if not existing else "en_proceso",
            action="Upsert lead por API",
            detail=f"Lead actualizado por telefono {merged['contacto']['celular']}",
        )
    )

    collection.replace_one({"lead_id": lead_id}, merged, upsert=True)
    persisted = collection.find_one({"lead_id": lead_id})
    if persisted:
        persisted.pop("_id", None)
    return persisted, ""


@app.get("/health")
async def health():
    collection = _get_leads_collection()
    return {
        "status": "ok",
        "service": "sureti-validaciones-api",
        "version": "3.0.0",
        "mongo_configurado": bool(MONGO_URL),
        "mongo_conectado": collection is not None,
    }


@app.post("/api/v1/leads/upsert")
async def upsert_lead(payload: dict[str, Any] = Body(...)):
    lead_payload = _as_dict(payload.get("lead")) if isinstance(payload.get("lead"), dict) else payload
    persisted, error = _ensure_lead_for_upsert(lead_payload)
    if not persisted:
        return {"estado": "error", "error": error}

    politicas_doc, decision_doc, prioridad = _apply_policy_on_lead(persisted)
    lead_id = _pick_str(persisted.get("lead_id"))
    celular = _pick_str(_as_dict(persisted.get("contacto")).get("celular"), lead_id)

    persist_result = _persist_validation_result(
        lead_id=lead_id,
        celular=celular,
        validation_key="politicas",
        validation_result=politicas_doc,
        action="Evaluacion politicas tras upsert",
        detail=f"Clasificacion={decision_doc.get('clasificacion')}",
        extra_set={
            "decision_sureti": decision_doc,
            "estado": "validado",
            "etapa": "evaluacion_politicas",
            "prioridad": prioridad,
            "puntaje_lead": decision_doc.get("score_riesgo", 0),
        },
    )

    return {
        "estado": "completado",
        "lead_id": lead_id,
        "clasificacion": decision_doc.get("clasificacion"),
        "persistencia": persist_result,
    }


@app.get("/api/leads")
@app.get("/api/v1/leads")
async def listar_leads(
    limit: int = Query(default=200, ge=1, le=1000),
    refresh: bool = Query(default=False),
):
    collection = _get_leads_collection()
    if collection is None:
        return {
            "items": [],
            "meta": {
                "total": 0,
                "refresh": refresh,
                "warning": "Mongo no configurado",
                "timestamp": _now_iso(),
            },
        }

    docs = list(collection.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit))
    identity_refreshed = 0
    policies_refreshed = 0
    if refresh:
        for doc in docs:
            lead_id = _pick_str(doc.get("lead_id"))
            celular = _pick_str(_as_dict(doc.get("contacto")).get("celular"), lead_id)
            if not lead_id:
                continue

            persona = _as_dict(doc.get("persona"))
            cedula = _pick_str(persona.get("cedula"), doc.get("cedula"), doc.get("documento"))

            if cedula and _should_refresh_identity(doc):
                identity_result = validar_identidad(
                    IdentityRequest(
                        lead_id=lead_id,
                        celular=celular,
                        cedula=cedula,
                    )
                )
                _persist_validation_result(
                    lead_id=lead_id,
                    celular=celular,
                    validation_key="identidad",
                    validation_result=identity_result,
                    action="Refresh identidad por consulta de dashboard",
                    detail=f"resultado={identity_result.get('resultado', identity_result.get('estado'))}",
                    extra_set={
                        "persona.cedula": cedula,
                        "etapa": "validacion_identidad",
                    },
                )
                validaciones = _as_dict(doc.get("validaciones"))
                validaciones["identidad"] = identity_result
                doc["validaciones"] = validaciones
                identity_refreshed += 1

            politicas_doc, decision_doc, prioridad = _apply_policy_on_lead(doc)
            _persist_validation_result(
                lead_id=lead_id,
                celular=celular,
                validation_key="politicas",
                validation_result=politicas_doc,
                action="Refresh politicas por consulta de dashboard",
                detail=f"Clasificacion={decision_doc.get('clasificacion')}",
                extra_set={
                    "decision_sureti": decision_doc,
                    "estado": "validado",
                    "etapa": "evaluacion_politicas",
                    "prioridad": prioridad,
                    "puntaje_lead": decision_doc.get("score_riesgo", 0),
                },
            )
            policies_refreshed += 1
        docs = list(collection.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit))

    return {
        "items": jsonable_encoder(docs),
        "meta": {
            "total": len(docs),
            "refresh": refresh,
            "identity_refreshed": identity_refreshed,
            "policies_refreshed": policies_refreshed,
            "timestamp": _now_iso(),
            "estructura": "produccion_whatsapp_v1",
        },
    }


@app.post("/api/v1/validar-identidad")
async def endpoint_identidad(data: IdentityRequest):
    result = validar_identidad(data)
    lead_id, celular = _resolve_lead_id_from_request(data)
    persistencia = _persist_validation_result(
        lead_id=lead_id,
        celular=celular,
        validation_key="identidad",
        validation_result=result,
        action="Validacion identidad",
        detail=f"resultado={result.get('resultado', result.get('estado'))}",
        extra_set={
            "persona.cedula": _pick_str(data.cedula),
            "etapa": "validacion_identidad",
        },
    )
    result["persistencia_bd"] = persistencia
    result["lead_id"] = lead_id
    result["celular"] = celular
    return result


@app.post("/api/v1/validar-inmueble")
async def endpoint_inmueble(data: PropertyRequest):
    result = validar_inmueble(data)
    lead_id, celular = _resolve_lead_id_from_request(data)
    persistencia = _persist_validation_result(
        lead_id=lead_id,
        celular=celular,
        validation_key="inmueble_validacion",
        validation_result=result,
        action="Validacion inmueble",
        detail=f"es_viable={result.get('es_viable')}",
        extra_set={
            "inmueble.ciudad": _pick_str(data.ciudad),
            "inmueble.tipo_inmueble": _pick_str(data.tipo_inmueble),
            "inmueble.valor_inmueble": _as_float(data.valor_inmueble, 0),
            "inmueble.direccion": _pick_str(data.direccion),
            "inmueble.chip": _pick_str(data.chip),
            "inmueble.matricula_inmobiliaria": _pick_str(data.matricula),
            "etapa": "validacion_inmueble",
        },
    )
    result["persistencia_bd"] = persistencia
    result["lead_id"] = lead_id
    result["celular"] = celular
    return result


@app.post("/api/v1/validar-ctl")
async def endpoint_ctl(data: CTLRequest):
    result = validar_ctl(data)
    lead_id, celular = _resolve_lead_id_from_request(data)
    persistencia = _persist_validation_result(
        lead_id=lead_id,
        celular=celular,
        validation_key="ctl",
        validation_result=result,
        action="Validacion CTL",
        detail=f"matricula={_pick_str(data.matricula)}",
        extra_set={
            "inmueble.matricula_inmobiliaria": _pick_str(data.matricula),
            "inmueble.ciudad": _pick_str(data.ciudad),
            "etapa": "validacion_ctl",
        },
    )
    result["persistencia_bd"] = persistencia
    result["lead_id"] = lead_id
    result["celular"] = celular
    return result


@app.post("/api/v1/evaluar-credito")
async def endpoint_credito(data: CreditRequest):
    result = evaluar_credito(data)
    lead_id, celular = _resolve_lead_id_from_request(data)
    persistencia = _persist_validation_result(
        lead_id=lead_id,
        celular=celular,
        validation_key="perfil_financiero",
        validation_result=result,
        action="Evaluacion credito",
        detail=f"score={_pick_str(result.get('score_interno'))}",
        extra_set={
            "persona.cedula": _pick_str(data.cedula),
            "persona.nombre_completo": _pick_str(data.nombre),
            "financiero.ingresos_mensuales": _as_float(data.ingresos_mensuales, 0),
            "financiero.egresos_mensuales": _as_float(data.egresos_mensuales, 0),
            "financiero.cuotas_creditos_vigentes": _as_float(data.cuotas_vigentes, 0),
            "inmueble.valor_inmueble": _as_float(data.valor_inmueble, 0),
            "credito.plazo_meses": _as_int(data.plazo_meses, 240),
            "etapa": "evaluacion_credito",
        },
    )
    result["persistencia_bd"] = persistencia
    result["lead_id"] = lead_id
    result["celular"] = celular
    return result


@app.post("/api/v1/analizar-riesgo")
async def endpoint_riesgo(data: RiskRequest):
    result = analizar_riesgo(data)
    lead_id, celular = _resolve_lead_id_from_request(data)
    persistencia = _persist_validation_result(
        lead_id=lead_id,
        celular=celular,
        validation_key="riesgo",
        validation_result=result,
        action="Analisis riesgo",
        detail=f"clasificacion={_pick_str(result.get('clasificacion'))}",
        extra_set={
            "persona.cedula": _pick_str(data.cedula),
            "inmueble.ciudad": _pick_str(data.ciudad),
            "inmueble.tipo_inmueble": _pick_str(data.tipo_inmueble),
            "inmueble.valor_inmueble": _as_float(data.valor_inmueble, 0),
            "inmueble.matricula_inmobiliaria": _pick_str(data.matricula),
            "financiero.ingresos_mensuales": _as_float(data.ingresos_mensuales, 0),
            "financiero.egresos_mensuales": _as_float(data.egresos_mensuales, 0),
            "financiero.cuotas_creditos_vigentes": _as_float(data.cuotas_vigentes, 0),
            "credito.plazo_meses": _as_int(data.plazo_meses, 240),
            "etapa": "analisis_riesgo",
        },
    )
    result["persistencia_bd"] = persistencia
    result["lead_id"] = lead_id
    result["celular"] = celular
    return result


@app.post("/api/v1/evaluar-politicas")
async def endpoint_politicas(data: PolicyRequest):
    raw_policy = (
        _build_provisional_policy_result("Falta cedula. Evaluacion provisional con datos parciales.")
        if not _pick_str(data.cedula)
        else evaluar_politicas(data)
    )
    politicas_doc, decision_doc, prioridad = _policy_result_to_documents(raw_policy)

    lead_id, celular = _resolve_lead_id_from_request(data)
    persistencia = _persist_validation_result(
        lead_id=lead_id,
        celular=celular,
        validation_key="politicas",
        validation_result=politicas_doc,
        action="Evaluacion politicas",
        detail=f"clasificacion={decision_doc.get('clasificacion')}",
        extra_set={
            "persona.cedula": _pick_str(data.cedula),
            "inmueble.ciudad": _pick_str(data.ciudad),
            "inmueble.tipo_inmueble": _pick_str(data.tipo_inmueble),
            "inmueble.valor_inmueble": _as_float(data.valor_inmueble, 0),
            "inmueble.matricula_inmobiliaria": _pick_str(data.matricula),
            "financiero.ingresos_mensuales": _as_float(data.ingresos_mensuales, 0),
            "financiero.egresos_mensuales": _as_float(data.egresos_mensuales, 0),
            "financiero.cuotas_creditos_vigentes": _as_float(data.cuotas_vigentes, 0),
            "credito.plazo_meses": _as_int(data.plazo_meses, 240),
            "decision_sureti": decision_doc,
            "estado": "validado",
            "etapa": "evaluacion_politicas",
            "prioridad": prioridad,
            "puntaje_lead": decision_doc.get("score_riesgo", 0),
        },
    )

    response = deepcopy(raw_policy)
    response["persistencia_bd"] = persistencia
    response["lead_id"] = lead_id
    response["celular"] = celular
    response["decision_sureti"] = decision_doc
    response["campos_actualizados_principales"] = [
        "validaciones.politicas",
        "decision_sureti",
        "estado",
        "etapa",
        "prioridad",
        "puntaje_lead",
        "updated_at",
    ]
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
