"""Versioned service-request payloads. Unknown keys are rejected."""

from __future__ import annotations

from buyer_space.constants import PAYLOAD_SCHEMA_VERSION


class PayloadError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _string(value, key):
    if not isinstance(value, str):
        raise PayloadError(f"« {key} » doit être un texte.")
    return value.strip()


def _optional_string(value, key):
    if value in (None, ""):
        return ""
    return _string(value, key)


def _string_list(value, key):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PayloadError(f"« {key} » doit être une liste de textes.")
    return [item.strip() for item in value if item.strip()]


def _number(value, key):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError(f"« {key} » doit être un nombre.")
    return value


def _object(value, key):
    if not isinstance(value, dict):
        raise PayloadError(f"« {key} » doit être un objet.")
    return value


def _check_keys(payload, allowed):
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise PayloadError("Champ inconnu : " + ", ".join(unknown) + ".")


def _dimensions(value):
    data = _object(value, "unitDimensions")
    _check_keys(data, {"length", "width", "height", "unit"})
    cleaned = {}
    for key in ("length", "width", "height"):
        if key in data:
            cleaned[key] = _number(data[key], f"unitDimensions.{key}")
    if "unit" in data:
        cleaned["unit"] = _string(data["unit"], "unitDimensions.unit")
    return cleaned


def _clean_design(payload):
    _check_keys(payload, {"product", "style", "colors", "visualReferences", "packagingFormat"})
    cleaned = {}
    if "product" in payload:
        cleaned["product"] = _optional_string(payload["product"], "product")
    if "style" in payload:
        cleaned["style"] = _optional_string(payload["style"], "style")
    if "colors" in payload:
        cleaned["colors"] = _string_list(payload["colors"], "colors")
    if "visualReferences" in payload:
        cleaned["visualReferences"] = _optional_string(payload["visualReferences"], "visualReferences")
    if "packagingFormat" in payload:
        cleaned["packagingFormat"] = _optional_string(payload["packagingFormat"], "packagingFormat")
    return cleaned


def _clean_analytics(payload):
    _check_keys(payload, {"businessQuestion", "datasetNotes", "period", "deliverableFormat"})
    cleaned = {}
    for key in ("businessQuestion", "datasetNotes", "period", "deliverableFormat"):
        if key in payload:
            cleaned[key] = _optional_string(payload[key], key)
    return cleaned


def _clean_paksuites(payload):
    _check_keys(payload, {"product", "constraints", "indicativeBudget", "quantities"})
    cleaned = {}
    if "product" in payload:
        cleaned["product"] = _optional_string(payload["product"], "product")
    if "constraints" in payload:
        constraints = _object(payload["constraints"], "constraints")
        _check_keys(constraints, {"conservation", "protection", "foodContact"})
        cleaned_constraints = {}
        for key in ("conservation", "protection"):
            if key in constraints:
                cleaned_constraints[key] = _optional_string(constraints[key], f"constraints.{key}")
        if "foodContact" in constraints:
            if not isinstance(constraints["foodContact"], bool):
                raise PayloadError("« constraints.foodContact » doit être un booléen.")
            cleaned_constraints["foodContact"] = constraints["foodContact"]
        cleaned["constraints"] = cleaned_constraints
    if "indicativeBudget" in payload and payload["indicativeBudget"] is not None:
        cleaned["indicativeBudget"] = _number(payload["indicativeBudget"], "indicativeBudget")
    if "quantities" in payload:
        cleaned["quantities"] = _optional_string(payload["quantities"], "quantities")
    return cleaned


def _clean_pakmath(payload):
    _check_keys(payload, {"unitDimensions", "cartonConstraints", "palletization"})
    cleaned = {}
    if "unitDimensions" in payload:
        cleaned["unitDimensions"] = _dimensions(payload["unitDimensions"])
    if "cartonConstraints" in payload:
        cleaned["cartonConstraints"] = _optional_string(payload["cartonConstraints"], "cartonConstraints")
    if "palletization" in payload and payload["palletization"] is not None:
        pallet = _object(payload["palletization"], "palletization")
        _check_keys(pallet, {"enabled", "notes"})
        cleaned_pallet = {}
        if "enabled" in pallet:
            if not isinstance(pallet["enabled"], bool):
                raise PayloadError("« palletization.enabled » doit être un booléen.")
            cleaned_pallet["enabled"] = pallet["enabled"]
        if "notes" in pallet:
            cleaned_pallet["notes"] = _optional_string(pallet["notes"], "palletization.notes")
        cleaned["palletization"] = cleaned_pallet
    return cleaned


CLEANERS = {
    "DESIGN": _clean_design,
    "ANALYTICS": _clean_analytics,
    "PAKSUITES": _clean_paksuites,
    "PAKMATH": _clean_pakmath,
}


def clean_payload(service_type: str, payload) -> dict:
    if payload in (None, ""):
        payload = {}
    if not isinstance(payload, dict):
        raise PayloadError("Le payload doit être un objet JSON.")
    cleaner = CLEANERS.get(service_type)
    if cleaner is None:
        raise PayloadError("Type de service inconnu.")
    return cleaner(payload)


def schema_version() -> int:
    return PAYLOAD_SCHEMA_VERSION
