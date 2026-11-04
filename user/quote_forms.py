"""Resolve, customize, validate and snapshot packaging quote forms."""

from __future__ import annotations

import json
import os
import re

from django.db import transaction
from django.utils.text import slugify

from user.models import (
    Category,
    QuoteFormField,
    QuoteFormTemplate,
    RFQRequestEvent,
    SupplierQuoteForm,
    SupplierQuoteFormField,
)
from user.quote_form_catalog import FAMILY_FORMS, TYPE_FORMS, generic_spec
from user.taxonomy_v4 import FAMILIES

MAX_EXTRA_QUESTIONS = 8
MAX_FILES = 3
MAX_FILE_BYTES = 8 * 1024 * 1024
EXTRA_KEY_RE = re.compile(r"^extra_[a-z0-9_]{1,40}$")
FIELD_TYPES = {choice for choice, _label in QuoteFormField.FieldType.choices}
CUSTOMIZATION = {
    "included": "Incluse",
    "paid": "Payante",
    "negotiate": "À négocier sur devis",
}
SUPPLIER_STATUS_TRANSITIONS = {
    "submitted": {"in_review", "rejected", "cancelled"},
    "in_review": {"rejected", "cancelled"},
    "quoted": {"rejected", "cancelled"},
    "accepted": set(),
    "rejected": set(),
    "cancelled": set(),
}


class QuoteFormError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def seed_quote_form_templates():
    """Create or update platform forms for the current taxonomy. Idempotent."""
    count = 0
    for slug, spec in _catalog_items():
        category = Category.objects.filter(slug=slug).first()
        if category is None:
            continue
        _upsert_template(category, spec)
        count += 1
    return count


def _catalog_items():
    seen = set()
    for slug, spec in {**FAMILY_FORMS, **TYPE_FORMS}.items():
        seen.add(slug)
        yield slug, spec
    for family in FAMILIES:
        if family.slug in seen or family.slug == "a-reviser":
            continue
        yield family.slug, generic_spec(family.name_fr)


def _field_signature(field):
    return (
        field.label_fr,
        field.label_en,
        field.field_type,
        field.unit_label,
        field.required,
        field.locked,
        field.positive,
        tuple(field.options or []),
        json.dumps(field.condition, sort_keys=True, ensure_ascii=False),
        field.group,
        field.help_fr,
        field.sort_order,
    )


def _spec_signature(spec, index):
    condition = spec.get("condition")
    return (
        spec["label_fr"],
        spec.get("label_en") or spec["label_fr"],
        spec["field_type"],
        spec.get("unit_label") or "",
        bool(spec.get("required")),
        bool(spec.get("locked")),
        bool(spec.get("positive")),
        tuple(spec.get("options") or []),
        json.dumps(condition, sort_keys=True, ensure_ascii=False) if condition else "null",
        spec.get("group") or "",
        spec.get("help_fr") or "",
        index,
    )


@transaction.atomic
def _upsert_template(category, spec):
    template, created = QuoteFormTemplate.objects.get_or_create(
        category=category,
        defaults={
            "title_fr": spec["title_fr"],
            "title_en": spec.get("title_en") or spec["title_fr"],
            "buyer_instructions": spec.get("instructions") or "",
            "quantity_unit": spec.get("quantity_unit") or "pièces",
        },
    )
    header_changed = False
    if not created:
        header_changed = any([
            template.title_fr != spec["title_fr"],
            template.title_en != (spec.get("title_en") or spec["title_fr"]),
            template.buyer_instructions != (spec.get("instructions") or ""),
            template.quantity_unit != (spec.get("quantity_unit") or "pièces"),
        ])
        template.title_fr = spec["title_fr"]
        template.title_en = spec.get("title_en") or spec["title_fr"]
        template.buyer_instructions = spec.get("instructions") or ""
        template.quantity_unit = spec.get("quantity_unit") or "pièces"

    existing = {field.key: field for field in template.fields.all()}
    new_keys = []
    fields_changed = header_changed or created
    for index, field_spec in enumerate(spec["fields"]):
        key = field_spec["key"]
        new_keys.append(key)
        current = existing.get(key)
        if current is None or _field_signature(current) != _spec_signature(field_spec, index):
            fields_changed = True
        QuoteFormField.objects.update_or_create(
            template=template,
            key=key,
            defaults={
                "label_fr": field_spec["label_fr"],
                "label_en": field_spec.get("label_en") or field_spec["label_fr"],
                "field_type": field_spec["field_type"],
                "unit_label": field_spec.get("unit_label") or "",
                "required": bool(field_spec.get("required")),
                "locked": bool(field_spec.get("locked")),
                "positive": bool(field_spec.get("positive")),
                "options": list(field_spec.get("options") or []),
                "condition": field_spec.get("condition"),
                "group": field_spec.get("group") or "",
                "help_fr": field_spec.get("help_fr") or "",
                "sort_order": index,
            },
        )
    removed, _rows = template.fields.exclude(key__in=new_keys).delete()
    if removed:
        fields_changed = True
    if fields_changed and not created:
        template.version += 1
    template.save()


def category_label(category):
    return category.name_fr or category.name_en


def offered_categories(supplier):
    """Product types this supplier can receive a typed quote for."""
    type_ids = set(supplier.interested_subcategories.values_list("pk", flat=True))
    product_rows = supplier.products.filter(is_active=True).values_list(
        "category_id", "subcategory_id"
    )
    family_ids = set(supplier.interested_categories.values_list("pk", flat=True))
    for category_id, subcategory_id in product_rows:
        if subcategory_id:
            type_ids.add(subcategory_id)
        elif category_id:
            family_ids.add(category_id)
    if type_ids:
        return list(
            Category.objects.filter(pk__in=type_ids, parent__isnull=False)
            .select_related("parent")
            .order_by("sort_order", "name_fr")
        )
    return list(
        Category.objects.filter(pk__in=family_ids, parent__isnull=True)
        .order_by("sort_order", "name_fr")
    )


def template_for(category):
    direct = (
        QuoteFormTemplate.objects.filter(category=category)
        .prefetch_related("fields")
        .first()
    )
    if direct:
        return direct
    if category.parent_id:
        return (
            QuoteFormTemplate.objects.filter(category_id=category.parent_id)
            .prefetch_related("fields")
            .first()
        )
    return None


def offered_category_payloads(supplier):
    rows = []
    for category in offered_categories(supplier):
        template = template_for(category)
        if template is None:
            continue
        rows.append({
            "id": category.id,
            "name": category_label(category),
            "slug": category.slug or "",
            "familyName": category_label(category.parent) if category.parent_id else "",
            "quantityUnit": template.quantity_unit,
        })
    return rows


def _supplier_form(supplier, template):
    return (
        SupplierQuoteForm.objects.filter(supplier=supplier, template=template)
        .prefetch_related("field_settings")
        .first()
    )


def _setting_map(supplier_form):
    if supplier_form is None:
        return {}
    return {row.field_id: row for row in supplier_form.field_settings.all()}


def resolve_buyer_form(supplier, category):
    if not supplier.is_active or supplier.is_ghost:
        raise QuoteFormError("Fournisseur introuvable.", 404)
    if category.id not in {row.id for row in offered_categories(supplier)}:
        raise QuoteFormError("Ce fournisseur ne propose pas ce type d'emballage.", 404)
    template = template_for(category)
    if template is None:
        raise QuoteFormError("Aucun formulaire pour ce type d'emballage.", 404)
    supplier_form = _supplier_form(supplier, template)
    settings = _setting_map(supplier_form)
    mode = (
        supplier_form.physical_customization
        if supplier_form
        else SupplierQuoteForm.PhysicalCustomization.NEGOTIATE
    )
    instructions = template.buyer_instructions
    if supplier_form and supplier_form.buyer_instructions.strip():
        instructions = supplier_form.buyer_instructions.strip()
    fields = []
    for field in template.fields.all():
        row = _resolved_field(field, settings.get(field.id))
        if row["enabled"]:
            fields.append(row)
    for extra in (supplier_form.extra_questions if supplier_form else []) or []:
        fields.append(_public_extra(extra))
    return {
        "supplierId": supplier.id,
        "category": {
            "id": category.id,
            "name": category_label(category),
            "slug": category.slug or "",
        },
        "templateId": template.id,
        "templateVersion": template.version,
        "title": template.title_fr,
        "quantityUnit": template.quantity_unit,
        "physicalCustomization": mode,
        "physicalCustomizationLabel": CUSTOMIZATION.get(mode, mode),
        "buyerInstructions": instructions,
        "fields": fields,
    }


def supplier_form_payload(supplier, category):
    if category.id not in {row.id for row in offered_categories(supplier)}:
        raise QuoteFormError("Catégorie non proposée par ce fournisseur.", 404)
    template = template_for(category)
    if template is None:
        raise QuoteFormError("Aucun formulaire pour ce type d'emballage.", 404)
    supplier_form = _supplier_form(supplier, template)
    settings = _setting_map(supplier_form)
    mode = (
        supplier_form.physical_customization
        if supplier_form
        else SupplierQuoteForm.PhysicalCustomization.NEGOTIATE
    )
    fields = []
    for field in template.fields.all():
        setting = settings.get(field.id)
        enabled = True if setting is None else setting.enabled
        if setting is None or setting.required is None:
            required = field.required
        else:
            required = setting.required
        options = list(field.options or [])
        if setting is not None and setting.options is not None:
            options = list(setting.options)
        if field.locked:
            enabled = True
            if field.required:
                required = True
        fields.append({
            "key": field.key,
            "label": field.label_fr,
            "fieldType": field.field_type,
            "unit": field.unit_label,
            "group": field.group,
            "help": field.help_fr,
            "platformRequired": field.required,
            "locked": field.locked,
            "enabled": enabled,
            "required": required,
            "platformOptions": list(field.options or []),
            "options": options,
            "condition": field.condition,
        })
    return {
        "category": {
            "id": category.id,
            "name": category_label(category),
            "slug": category.slug or "",
        },
        "templateId": template.id,
        "templateVersion": template.version,
        "quantityUnit": template.quantity_unit,
        "platformInstructions": template.buyer_instructions,
        "buyerInstructions": supplier_form.buyer_instructions if supplier_form else "",
        "physicalCustomization": mode,
        "fields": fields,
        "extraQuestions": list(supplier_form.extra_questions or []) if supplier_form else [],
    }


@transaction.atomic
def save_supplier_form(supplier, category, payload):
    current = supplier_form_payload(supplier, category)
    template = QuoteFormTemplate.objects.get(pk=current["templateId"])
    mode = payload.get("physicalCustomization") or current["physicalCustomization"]
    if mode not in CUSTOMIZATION:
        raise QuoteFormError("Mode de personnalisation physique invalide.")
    instructions = (payload.get("buyerInstructions") or "").strip()
    if len(instructions) > 2000:
        raise QuoteFormError("Les instructions dépassent 2000 caractères.")
    by_key = {field.key: field for field in template.fields.all()}
    incoming = payload.get("fields") or []
    if not isinstance(incoming, list):
        raise QuoteFormError("La liste des champs est invalide.")
    seen = set()
    prepared = []
    for item in incoming:
        if not isinstance(item, dict):
            raise QuoteFormError("Un champ de configuration est invalide.")
        key = item.get("key")
        field = by_key.get(key)
        if field is None:
            raise QuoteFormError(f"Champ inconnu : {key}.")
        seen.add(key)
        enabled = bool(item.get("enabled", True))
        required = bool(item.get("required", field.required))
        if field.locked and not enabled:
            raise QuoteFormError(f"Le champ « {field.label_fr} » ne peut pas être désactivé.")
        if field.locked and field.required and not required:
            raise QuoteFormError(f"Le champ « {field.label_fr} » reste obligatoire.")
        options = _clean_option_subset(field, item.get("options"))
        prepared.append((field, enabled, required, options))
    missing = set(by_key) - seen
    if missing:
        raise QuoteFormError("Tous les champs du formulaire doivent être envoyés.")
    extras = _clean_extra_questions(payload.get("extraQuestions") or [], set(by_key))
    form, _created = SupplierQuoteForm.objects.update_or_create(
        supplier=supplier,
        template=template,
        defaults={
            "buyer_instructions": instructions,
            "physical_customization": mode,
            "extra_questions": extras,
        },
    )
    SupplierQuoteFormField.objects.filter(form=form).delete()
    SupplierQuoteFormField.objects.bulk_create([
        SupplierQuoteFormField(
            form=form,
            field=field,
            enabled=enabled,
            required=required,
            options=options,
        )
        for field, enabled, required, options in prepared
    ])
    return supplier_form_payload(supplier, category)


def prepare_specs(supplier, category, raw_specs):
    """Validate buyer answers and return a frozen snapshot dict."""
    if not isinstance(raw_specs, dict):
        raise QuoteFormError("Les spécifications doivent être un objet.")
    form = resolve_buyer_form(supplier, category)
    cleaned = []
    errors = []
    for field in form["fields"]:
        visible = _condition_met(field.get("condition"), raw_specs)
        if not visible:
            continue
        value, error = _clean_value(field, raw_specs.get(field["key"]))
        if error:
            errors.append(error)
            continue
        if value is None or value == "":
            continue
        cleaned.append({
            "key": field["key"],
            "label": field["label"],
            "type": field["fieldType"],
            "value": value,
            "unit": field.get("unit") or "",
            "display": _display(value, field.get("unit") or ""),
        })
    if errors:
        raise QuoteFormError(" ".join(errors))
    return {
        "template_id": form["templateId"],
        "template_version": form["templateVersion"],
        "category_id": category.id,
        "category_name": form["category"]["name"],
        "quantity_unit": form["quantityUnit"],
        "physical_customization": form["physicalCustomization"],
        "physical_customization_label": form["physicalCustomizationLabel"],
        "buyer_instructions": form["buyerInstructions"],
        "fields": cleaned,
    }


def apply_snapshot(rfq, category, snapshot, actor=None):
    rfq.category = category
    rfq.quantity_unit = snapshot.get("quantity_unit") or ""
    snapshot = {**snapshot, "quantity": rfq.quantity_estimate}
    rfq.specs_snapshot = snapshot
    rfq.save(update_fields=["category", "quantity_unit", "specs_snapshot", "updated_at"])
    record_rfq_event(
        rfq,
        RFQRequestEvent.Action.SUBMITTED,
        actor=actor,
        snapshot=snapshot,
    )


def record_rfq_event(rfq, action, actor=None, comment="", snapshot=None):
    return RFQRequestEvent.objects.create(
        rfq=rfq,
        action=action,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        comment=comment or "",
        snapshot=snapshot or {},
    )


def change_rfq_status(rfq, new_status, actor, comment=""):
    allowed = SUPPLIER_STATUS_TRANSITIONS.get(rfq.status, set())
    if new_status not in allowed:
        raise QuoteFormError("Ce changement de statut n'est pas autorisé.", 409)
    previous = rfq.status
    rfq.status = new_status
    update_fields = ["status", "updated_at"]
    if rfq.responded_at is None and new_status == "in_review":
        from django.utils import timezone

        rfq.responded_at = timezone.now()
        update_fields.append("responded_at")
    rfq.save(update_fields=update_fields)
    record_rfq_event(
        rfq,
        RFQRequestEvent.Action.STATUS_CHANGED,
        actor=actor,
        comment=comment,
        snapshot={"from": previous, "to": new_status},
    )
    return rfq


def clean_technical_files(files):
    if len(files) > MAX_FILES:
        return f"{MAX_FILES} fichiers maximum par demande."
    for upload in files:
        if upload.size > MAX_FILE_BYTES:
            return "Un fichier dépasse 8 Mo."
        name = os.path.basename(upload.name or "")
        if not name.lower().endswith(".pdf"):
            return "Seuls les fichiers PDF sont acceptés en pièce jointe technique."
    return ""


def parse_specs(raw):
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise QuoteFormError("Les spécifications ne sont pas un JSON valide.") from exc
        if not isinstance(parsed, dict):
            raise QuoteFormError("Les spécifications doivent être un objet.")
        return parsed
    raise QuoteFormError("Les spécifications doivent être un objet.")


def _resolved_field(field, setting):
    enabled = True if setting is None else setting.enabled
    if setting is None or setting.required is None:
        required = field.required
    else:
        required = setting.required
    options = list(field.options or [])
    if setting is not None and setting.options is not None:
        options = list(setting.options)
    if field.locked:
        enabled = True
        if field.required:
            required = True
    if not enabled:
        required = False
    return {
        "key": field.key,
        "label": field.label_fr,
        "fieldType": field.field_type,
        "unit": field.unit_label,
        "group": field.group,
        "help": field.help_fr,
        "required": required and enabled,
        "enabled": enabled,
        "options": options,
        "condition": field.condition,
    }


def _public_extra(extra):
    return {
        "key": extra["key"],
        "label": extra["label"],
        "fieldType": extra["fieldType"],
        "unit": extra.get("unit") or "",
        "group": "complement",
        "help": "",
        "required": bool(extra.get("required")),
        "enabled": True,
        "options": list(extra.get("options") or []),
        "condition": None,
    }


def _clean_option_subset(field, raw_options):
    platform = list(field.options or [])
    if field.field_type != QuoteFormField.FieldType.CHOICE:
        return None
    if raw_options is None:
        return platform
    if not isinstance(raw_options, list) or not raw_options:
        raise QuoteFormError(f"Choisissez au moins une option pour « {field.label_fr} ».")
    unknown = [option for option in raw_options if option not in platform]
    if unknown:
        raise QuoteFormError(
            f"Options non autorisées pour « {field.label_fr} »."
        )
    return list(dict.fromkeys(raw_options))


def _clean_extra_questions(raw, platform_keys):
    if not isinstance(raw, list):
        raise QuoteFormError("Les questions complémentaires sont invalides.")
    if len(raw) > MAX_EXTRA_QUESTIONS:
        raise QuoteFormError(f"{MAX_EXTRA_QUESTIONS} questions complémentaires maximum.")
    cleaned = []
    used = set(platform_keys)
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise QuoteFormError("Une question complémentaire est invalide.")
        label = (item.get("label") or item.get("label_fr") or "").strip()
        if not label or len(label) > 200:
            raise QuoteFormError("Chaque question complémentaire doit avoir un libellé.")
        field_type = item.get("fieldType") or item.get("field_type") or "text"
        if field_type not in FIELD_TYPES:
            raise QuoteFormError("Type de question complémentaire invalide.")
        raw_key = (item.get("key") or "").strip().lower().replace("-", "_")
        if not raw_key:
            raw_key = slugify(label).replace("-", "_") or str(index)
        key = raw_key if raw_key.startswith("extra_") else f"extra_{raw_key}"
        if not EXTRA_KEY_RE.match(key) or key in used:
            raise QuoteFormError("Identifiant de question complémentaire invalide.")
        used.add(key)
        options = item.get("options") or []
        if field_type == "choice":
            if not isinstance(options, list) or not options:
                raise QuoteFormError(f"La question « {label} » doit proposer des choix.")
            options = [str(option).strip() for option in options if str(option).strip()]
            if len(options) < 2:
                raise QuoteFormError(f"La question « {label} » doit proposer au moins deux choix.")
        else:
            options = []
        cleaned.append({
            "key": key,
            "label": label,
            "fieldType": field_type,
            "required": bool(item.get("required")),
            "options": options,
            "unit": (item.get("unit") or "")[:40],
        })
    return cleaned


def _condition_met(condition, answers):
    if not condition:
        return True
    raw = answers.get(condition.get("field"))
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return False
    actual = _norm(raw)
    if "equals" in condition:
        return actual == _norm(condition["equals"])
    if "not_equals" in condition:
        return actual != _norm(condition["not_equals"])
    if "in" in condition:
        allowed = {_norm(value) for value in condition["in"]}
        return actual in allowed
    return True


def _norm(value):
    if value is None:
        return ""
    return str(value).strip()


def _clean_value(field, raw):
    label = field["label"]
    required = field["required"]
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            return None, f"« {label} » est obligatoire."
        return None, None
    field_type = field["fieldType"]
    if field_type == "number":
        try:
            number = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            return None, f"« {label} » doit être un nombre."
        if field.get("positive") or field.get("unit"):
            if number <= 0:
                return None, f"« {label} » doit être supérieur à 0."
        if number.is_integer():
            number = int(number)
        return number, None
    if field_type == "boolean":
        if isinstance(raw, bool):
            return raw, None
        token = str(raw).strip().lower()
        if token in {"1", "true", "oui", "yes"}:
            return True, None
        if token in {"0", "false", "non", "no"}:
            return False, None
        return None, f"« {label} » doit être oui ou non."
    if field_type == "choice":
        value = str(raw).strip()
        if value not in set(field.get("options") or []):
            return None, f"« {label} » : choix non proposé."
        return value, None
    value = str(raw).strip()
    if len(value) > 2000:
        return None, f"« {label} » est trop long."
    return value, None


def _display(value, unit):
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    if unit:
        return f"{value} {unit}"
    return str(value)
