import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView

from buyer_space.constants import PAKMATH_DISCLAIMER, SERVICE_LABELS
from buyer_space.catalogue_models import SuiteEnsemble, SuiteTaxon
from buyer_space.models import (
    BuyerProduct,
    LibraryProduct,
    LibrarySector,
    PackagingItem,
    PackagingKit,
    PackagingProject,
    PaksuitesComponent,
    PaksuitesOption,
    ProjectCatalogProduct,
    ProjectSupplier,
    ServiceAvailability,
    ServiceRequest,
    ServiceRequestDeliverable,
    ServiceRequestFile,
    ServiceRequestMessage,
    ServiceRequestMessageFile,
)
from buyer_space.project_assembly import assemble_project
from buyer_space.quotes import QuoteGenerationError, generate_quotes_for_option
from buyer_space.schemas import PayloadError, clean_payload, schema_version
from buyer_space.transitions import TransitionError, transition_status
from buyer_space.uploads import clean_request_files
from buyer_space.wallet import (
    WalletError,
    debit_for_service,
    ensure_buyer_account,
    remaining_for,
    service_charge_amount,
)
from user.models import Product, Supplier


def _error(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({"error": message}, status=code)


def _page(request, queryset):
    try:
        page = max(1, int(request.query_params.get("page", 1)))
        page_size = min(50, max(1, int(request.query_params.get("pageSize", 20))))
    except (TypeError, ValueError):
        page, page_size = 1, 20
    total = queryset.count()
    start = (page - 1) * page_size
    meta = {
        "count": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": max(1, (total + page_size - 1) // page_size),
    }
    return queryset[start : start + page_size], meta


def _buyer_account(request):
    user = request.user
    if getattr(user, "supplier_id", None):
        return None, _error("Compte acheteur requis.", status.HTTP_403_FORBIDDEN)
    return ensure_buyer_account(user), None


def _parse_date(value):
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise PayloadError("La date souhaitée doit être au format AAAA-MM-JJ.")


def _money(value):
    if value is None:
        return None
    return str(value)


def _project_data(project, include_links=False):
    data = {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "need": project.need,
        "constraints": project.constraints,
        "objective": project.objective,
        "status": project.status,
        "buyerProductId": project.buyer_product_id,
        "targetQuantity": project.target_quantity,
        "dueOn": project.due_on.isoformat() if project.due_on else None,
        "createdAt": project.created_at.isoformat(),
        "updatedAt": project.updated_at.isoformat(),
        **_project_contents(project),
    }
    if include_links:
        data["catalogProducts"] = [
            {"id": link.product_id, "name": link.product.name}
            for link in project.catalog_products.select_related("product")
        ]
        data["catalogSuppliers"] = [
            {"id": link.supplier_id, "name": link.supplier.company_name}
            for link in project.suppliers.select_related("supplier")
        ]
        data["products"] = [
            {
                "id": link.id,
                "name": link.name,
                "libraryProductId": link.library_product_id,
                "buyerProductId": link.buyer_product_id,
                "imageUrl": (
                    link.library_product.file.url
                    if link.library_product_id and link.library_product.file
                    else None
                ),
                "varieties": [
                    {
                        "id": variety.id,
                        "name": variety.name,
                        "labelText": variety.label_text,
                        "capColor": variety.cap_color,
                    }
                    for variety in link.varieties.all()
                ],
            }
            for link in project.product_links.select_related("library_product")
        ]
        data["suiteKits"] = [
            {
                "id": kit.id,
                "code": kit.ensemble.code,
                "slug": kit.ensemble.slug,
                "name": kit.ensemble.name,
            }
            for kit in project.suite_kits.select_related("ensemble")
        ]
        data["items"] = [_item_data(item) for item in project.items.all()]
    return data


def _media_url(file_field) -> str:
    if not file_field:
        return ""
    try:
        return file_field.url
    except ValueError:
        return ""


def _suite_image_url(ensemble) -> str:
    if ensemble is None:
        return ""
    images = [image for image in ensemble.images.all() if image.file]
    overview = next((image for image in images if (image.view_type or "") == "overview"), None)
    chosen = overview or (images[0] if images else None)
    return _media_url(chosen.file) if chosen is not None else ""


def _project_contents(project):
    product = next(iter(project.product_links.all()), None)
    library = product.library_product if product is not None and product.library_product_id else None
    kit = next(iter(project.suite_kits.all()), None)
    ensemble = kit.ensemble if kit is not None and kit.ensemble_id else None
    quote_count = getattr(project, "quote_demand_count", None)
    if quote_count is None:
        quote_count = project.quote_demands.count()
    sample_count = getattr(project, "sample_request_count", None)
    if sample_count is None:
        sample_count = project.sample_requests.count()
    return {
        "productName": product.name if product is not None else "",
        "productImageUrl": _media_url(library.file) if library is not None else "",
        "kitName": ensemble.name if ensemble is not None else "",
        "kitCode": ensemble.code if ensemble is not None else "",
        "kitImageUrl": _suite_image_url(ensemble),
        "quoteDemandCount": quote_count,
        "sampleRequestCount": sample_count,
    }


def _parse_int_list(raw) -> list[int]:
    if raw is None:
        return []
    if isinstance(raw, list):
        values = raw
    else:
        values = [raw]
    out: list[int] = []
    for item in values:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _parse_str_list(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    return [text] if text else []


def _parse_varieties(raw) -> list[dict] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return []
    parsed: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:120]
        if not name:
            continue
        parsed.append({
            "name": name,
            "label_text": str(item.get("labelText") or item.get("label_text") or "").strip()[:200],
            "cap_color": str(item.get("capColor") or item.get("cap_color") or "").strip()[:80],
        })
    return parsed


def _apply_assembly(project, account, data):
    qty = data.get("targetQuantity", data.get("target_quantity"))
    target_quantity = None
    if qty not in (None, ""):
        try:
            target_quantity = max(0, int(qty))
        except (TypeError, ValueError):
            raise PayloadError("La quantité approximative est invalide.")
    return assemble_project(
        project=project,
        account=account,
        library_product_ids=_parse_int_list(data.get("libraryProductIds")),
        packaging_type_codes=_parse_str_list(data.get("packagingTypeCodes")),
        suite_codes=_parse_str_list(data.get("suiteCodes")),
        varieties=_parse_varieties(data.get("varieties")),
        target_quantity=target_quantity,
    )


def _item_data(item):
    return {
        "id": item.id,
        "projectId": item.project_id,
        "name": item.name,
        "packagingType": item.packaging_type,
        "material": item.material,
        "length": _money(item.length),
        "width": _money(item.width),
        "height": _money(item.height),
        "dimensionUnit": item.dimension_unit,
        "capacity": _money(item.capacity),
        "capacityUnit": item.capacity_unit,
        "foodContact": item.food_contact,
        "printing": item.printing,
        "targetQuantity": item.target_quantity,
        "notes": item.notes,
        "specVersion": item.spec_version,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }


def _product_data(product):
    return {
        "id": product.id,
        "name": product.name,
        "categoryId": product.category_id,
        "categoryLabel": product.category_label,
        "attributes": product.attributes,
        "createdAt": product.created_at.isoformat(),
    }


def _file_data(uploaded, request):
    url = uploaded.file.url if uploaded.file else ""
    if url and request is not None:
        url = request.build_absolute_uri(url)
    return {"id": uploaded.id, "name": uploaded.original_name, "url": url}


def _request_summary(service_request):
    return {
        "id": service_request.id,
        "reference": service_request.reference,
        "serviceType": service_request.service_type,
        "serviceLabel": SERVICE_LABELS.get(service_request.service_type, service_request.service_type),
        "status": service_request.status,
        "title": service_request.title,
        "projectId": service_request.project_id,
        "packagingItemId": service_request.packaging_item_id,
        "desiredOn": service_request.desired_on.isoformat() if service_request.desired_on else None,
        "quotaCost": _money(service_request.quota_cost),
        "createdAt": service_request.created_at.isoformat(),
        "updatedAt": service_request.updated_at.isoformat(),
    }


def _request_detail(service_request, request):
    data = _request_summary(service_request)
    data.update(
        {
            "description": service_request.description,
            "payload": service_request.payload,
            "payloadSchemaVersion": service_request.payload_schema_version,
            "assignedToId": service_request.assigned_to_id,
            "files": [_file_data(item, request) for item in service_request.files.all()],
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "body": message.body,
                    "authorId": message.author_id,
                    "createdAt": message.created_at.isoformat(),
                    "files": [_file_data(item, request) for item in message.files.all()],
                }
                for message in service_request.messages.all()
            ],
            "deliverables": [
                _deliverable_data(item, request) for item in service_request.deliverables.all()
            ],
            "events": [
                {
                    "id": event.id,
                    "fromStatus": event.from_status,
                    "toStatus": event.to_status,
                    "actorId": event.actor_id,
                    "createdAt": event.created_at.isoformat(),
                }
                for event in service_request.events.all()
            ],
            "paksuitesOptions": [
                {
                    "id": option.id,
                    "title": option.title,
                    "components": [
                        {
                            "id": component.id,
                            "packagingType": component.packaging_type,
                            "description": component.description,
                            "specification": component.specification,
                            "suggestedSupplierId": component.suggested_supplier_id,
                            "quoteDemandId": getattr(component, "quote_demand_id", None),
                        }
                        for component in option.components.all()
                    ],
                }
                for option in service_request.paksuites_options.all()
            ],
        }
    )
    if service_request.service_type == ServiceRequest.ServiceType.PAKMATH:
        data["disclaimer"] = PAKMATH_DISCLAIMER
    return data


def _deliverable_data(item, request):
    url = ""
    if item.file:
        url = item.file.url
        if request is not None:
            url = request.build_absolute_uri(url)
    data = {
        "id": item.id,
        "label": item.label,
        "url": url or None,
        "result": item.result,
        "version": item.version,
        "isIndicative": item.is_indicative,
        "createdAt": item.created_at.isoformat(),
    }
    if item.request.service_type == ServiceRequest.ServiceType.PAKMATH:
        data["disclaimer"] = PAKMATH_DISCLAIMER
    return data


def _owned_project(account, project_id):
    return PackagingProject.objects.filter(account=account, pk=project_id).first()


def _load_payload(request):
    raw = request.data.get("payload", {})
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raise PayloadError("Le payload n'est pas un JSON valide.")
    return raw


class BuyerAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        return Response(
            {
                "id": account.id,
                "balance": _money(account.balance),
                "serviceBalances": {
                    service_type: _money(remaining_for(account, service_type))
                    for service_type in ServiceRequest.ServiceType.values
                },
                "companyGrantServices": ["DESIGN", "ANALYTICS"],
            }
        )


class BuyerPragmadinarEntryListView(APIView):
    """Append-only journal. Balance is the sum of the entries."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        qs = account.entries.select_related("service_request").order_by("-created_at", "-id")
        page, meta = _page(request, qs)
        return Response(
            {
                **meta,
                "balance": _money(account.balance),
                "results": [
                    {
                        "id": entry.id,
                        "amount": _money(entry.amount),
                        "entryType": entry.entry_type,
                        "reason": entry.reason,
                        "eligibleServices": entry.eligible_services or [],
                        "serviceRequestId": entry.service_request_id,
                        "serviceRequestReference": (
                            entry.service_request.reference
                            if entry.service_request_id
                            else None
                        ),
                        "createdAt": entry.created_at.isoformat(),
                    }
                    for entry in page
                ],
            }
        )


class ProjectListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        qs = (
            PackagingProject.objects.filter(account=account)
            .annotate(
                quote_demand_count=Count("quote_demands", distinct=True),
                sample_request_count=Count("sample_requests", distinct=True),
            )
            .prefetch_related(
                "product_links__library_product",
                "suite_kits__ensemble__images",
            )
        )
        status_filter = (request.query_params.get("status") or "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)
        page, meta = _page(request, qs)
        return Response({**meta, "results": [_project_data(item) for item in page]})

    def post(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        name = str(request.data.get("name") or "").strip()
        if not name:
            return _error("Le nom du projet est obligatoire.")
        product = None
        product_id = request.data.get("buyerProductId")
        if product_id:
            product = BuyerProduct.objects.filter(account=account, pk=product_id).first()
            if product is None:
                return _error("Produit introuvable.", status.HTTP_404_NOT_FOUND)
        try:
            due_on = _parse_date(request.data.get("dueOn"))
        except PayloadError as exc:
            return _error(exc.message)
        status_value = request.data.get("status") or PackagingProject.Status.DRAFT
        if status_value not in PackagingProject.Status.values:
            return _error("Statut de projet inconnu.")
        qty_raw = request.data.get("targetQuantity")
        target_quantity = None
        if qty_raw not in (None, ""):
            try:
                target_quantity = max(0, int(qty_raw))
            except (TypeError, ValueError):
                return _error("La quantité approximative est invalide.")
        project = PackagingProject.objects.create(
            account=account,
            name=name,
            description=str(request.data.get("description") or ""),
            need=str(request.data.get("need") or ""),
            constraints=str(request.data.get("constraints") or ""),
            objective=str(request.data.get("objective") or ""),
            status=status_value,
            buyer_product=product,
            target_quantity=target_quantity,
            due_on=due_on,
            created_by=request.user,
        )
        try:
            project = _apply_assembly(project, account, request.data)
        except PayloadError as exc:
            return _error(exc.message)
        return Response(
            _project_data(project, include_links=True), status=status.HTTP_201_CREATED
        )


class ProjectDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        account, error = _buyer_account(request)
        if error:
            return error
        project = _owned_project(account, pk)
        if project is None:
            return _error("Projet introuvable.", status.HTTP_404_NOT_FOUND)
        return Response(_project_data(project, include_links=True))

    def patch(self, request, pk):
        account, error = _buyer_account(request)
        if error:
            return error
        project = _owned_project(account, pk)
        if project is None:
            return _error("Projet introuvable.", status.HTTP_404_NOT_FOUND)
        if "name" in request.data:
            name = str(request.data.get("name") or "").strip()
            if not name:
                return _error("Le nom du projet est obligatoire.")
            project.name = name
        if "description" in request.data:
            project.description = str(request.data.get("description") or "")
        for source, target in (("need", "need"), ("constraints", "constraints"), ("objective", "objective")):
            if source in request.data:
                setattr(project, target, str(request.data.get(source) or ""))
        if "status" in request.data:
            if request.data["status"] not in PackagingProject.Status.values:
                return _error("Statut de projet inconnu.")
            project.status = request.data["status"]
        if "buyerProductId" in request.data:
            product_id = request.data.get("buyerProductId")
            if not product_id:
                project.buyer_product = None
            else:
                product = BuyerProduct.objects.filter(account=account, pk=product_id).first()
                if product is None:
                    return _error("Produit introuvable.", status.HTTP_404_NOT_FOUND)
                project.buyer_product = product
        if "dueOn" in request.data:
            try:
                project.due_on = _parse_date(request.data.get("dueOn"))
            except PayloadError as exc:
                return _error(exc.message)
        if "targetQuantity" in request.data:
            qty_raw = request.data.get("targetQuantity")
            if qty_raw in (None, ""):
                project.target_quantity = None
            else:
                try:
                    project.target_quantity = max(0, int(qty_raw))
                except (TypeError, ValueError):
                    return _error("La quantité approximative est invalide.")
        project.save()
        if any(
            key in request.data
            for key in ("libraryProductIds", "packagingTypeCodes", "suiteCodes", "varieties")
        ):
            try:
                project = _apply_assembly(project, account, request.data)
            except PayloadError as exc:
                return _error(exc.message)
        return Response(_project_data(project, include_links=True))

    def delete(self, request, pk):
        """Archive. Les devis liés restent en place (clés étrangères nullables)."""
        account, error = _buyer_account(request)
        if error:
            return error
        project = _owned_project(account, pk)
        if project is None:
            return _error("Projet introuvable.", status.HTTP_404_NOT_FOUND)
        project.status = PackagingProject.Status.ARCHIVED
        project.save(update_fields=["status", "updated_at"])
        return Response(_project_data(project))


class PackagingItemListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        qs = PackagingItem.objects.filter(project__account=account).select_related("project")
        project_id = request.query_params.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)
        page, meta = _page(request, qs)
        return Response({**meta, "results": [_item_data(item) for item in page]})

    def post(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        project = _owned_project(account, request.data.get("projectId"))
        if project is None:
            return _error("Projet introuvable.", status.HTTP_404_NOT_FOUND)
        name = str(request.data.get("name") or "").strip()
        if not name:
            return _error("Le nom de l'emballage est obligatoire.")
        item = PackagingItem(project=project, name=name)
        try:
            _apply_item_fields(item, request.data)
        except PayloadError as exc:
            return _error(exc.message)
        item.save()
        return Response(_item_data(item), status=status.HTTP_201_CREATED)


class PackagingItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, account, pk):
        return PackagingItem.objects.filter(project__account=account, pk=pk).first()

    def get(self, request, pk):
        account, error = _buyer_account(request)
        if error:
            return error
        item = self._get(account, pk)
        if item is None:
            return _error("Emballage introuvable.", status.HTTP_404_NOT_FOUND)
        return Response(_item_data(item))

    def patch(self, request, pk):
        account, error = _buyer_account(request)
        if error:
            return error
        item = self._get(account, pk)
        if item is None:
            return _error("Emballage introuvable.", status.HTTP_404_NOT_FOUND)
        if "name" in request.data:
            name = str(request.data.get("name") or "").strip()
            if not name:
                return _error("Le nom de l'emballage est obligatoire.")
            item.name = name
        try:
            _apply_item_fields(item, request.data)
        except PayloadError as exc:
            return _error(exc.message)
        if "notes" in request.data:
            item.notes = str(request.data.get("notes") or "")
        item.save()
        return Response(_item_data(item))


def _decimal(value, label):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PayloadError(f"« {label} » doit être un nombre.")


def _apply_item_fields(item, data):
    mapping = {
        "packagingType": "packaging_type",
        "material": "material",
        "dimensionUnit": "dimension_unit",
        "capacityUnit": "capacity_unit",
        "printing": "printing",
    }
    for source, target in mapping.items():
        if source in data:
            setattr(item, target, str(data.get(source) or ""))
    for source, target in (("length", "length"), ("width", "width"), ("height", "height"), ("capacity", "capacity")):
        if source in data:
            setattr(item, target, _decimal(data.get(source), source))
    if "foodContact" in data:
        value = data.get("foodContact")
        if isinstance(value, str):
            item.food_contact = value.strip().lower() in {"1", "true", "oui", "yes"}
        else:
            item.food_contact = bool(value)
    if "targetQuantity" in data:
        raw = data.get("targetQuantity")
        if raw in (None, ""):
            item.target_quantity = None
        else:
            try:
                item.target_quantity = int(raw)
            except (TypeError, ValueError):
                raise PayloadError("« targetQuantity » doit être un entier.")
    if "notes" in data and not item.pk:
        item.notes = str(data.get("notes") or "")


class BuyerProductListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        page, meta = _page(request, BuyerProduct.objects.filter(account=account))
        return Response({**meta, "results": [_product_data(item) for item in page]})

    def post(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        name = str(request.data.get("name") or "").strip()
        if not name:
            return _error("Le nom du produit est obligatoire.")
        attributes = request.data.get("attributes") or {}
        if not isinstance(attributes, dict):
            return _error("« attributes » doit être un objet.")
        product = BuyerProduct.objects.create(
            account=account,
            name=name,
            category_label=str(request.data.get("categoryLabel") or ""),
            attributes=attributes,
        )
        return Response(_product_data(product), status=status.HTTP_201_CREATED)


class BuyerProductDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        account, error = _buyer_account(request)
        if error:
            return error
        product = BuyerProduct.objects.filter(account=account, pk=pk).first()
        if product is None:
            return _error("Produit introuvable.", status.HTTP_404_NOT_FOUND)
        if "name" in request.data:
            name = str(request.data.get("name") or "").strip()
            if not name:
                return _error("Le nom du produit est obligatoire.")
            product.name = name
        if "categoryLabel" in request.data:
            product.category_label = str(request.data.get("categoryLabel") or "")
        if "attributes" in request.data:
            if not isinstance(request.data.get("attributes"), dict):
                return _error("« attributes » doit être un objet.")
            product.attributes = request.data["attributes"]
        product.save()
        return Response(_product_data(product))


class ProjectLinkView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, kind):
        account, error = _buyer_account(request)
        if error:
            return error
        project = _owned_project(account, pk)
        if project is None:
            return _error("Projet introuvable.", status.HTTP_404_NOT_FOUND)
        if kind not in ("products", "suppliers"):
            return _error("Lien inconnu.", status.HTTP_404_NOT_FOUND)
        if kind == "products":
            catalog_product = Product.objects.filter(
                pk=request.data.get("productId"), is_active=True
            ).first()
            if catalog_product is None:
                return _error("Produit catalogue introuvable.", status.HTTP_404_NOT_FOUND)
            link, created = ProjectCatalogProduct.objects.get_or_create(
                project=project, product=catalog_product
            )
            return Response(
                {"id": link.id, "projectId": project.id, "productId": catalog_product.id},
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
        supplier = Supplier.objects.filter(
            pk=request.data.get("supplierId"), is_active=True, is_ghost=False
        ).first()
        if supplier is None:
            return _error("Fournisseur introuvable.", status.HTTP_404_NOT_FOUND)
        link, created = ProjectSupplier.objects.get_or_create(project=project, supplier=supplier)
        return Response(
            {"id": link.id, "projectId": project.id, "supplierId": supplier.id},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _availability_payload(row, account=None):
    used = 0
    if account is not None:
        used = ServiceRequest.objects.filter(
            account=account, service_type=row.service_type
        ).count()
    free_remaining = None
    if row.free_quota is not None:
        free_remaining = max(0, row.free_quota - used)
    return {
        "serviceType": row.service_type,
        "label": row.label,
        "state": row.state,
        "message": row.message,
        "freeQuota": row.free_quota,
        "freeRemaining": free_remaining,
        "unitCost": _money(row.unit_cost),
    }


class ServiceAvailabilityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account = None
        if not getattr(request.user, "supplier_id", None):
            account = ensure_buyer_account(request.user)
        rows = ServiceAvailability.objects.order_by("service_type")
        return Response([_availability_payload(row, account) for row in rows])

    def patch(self, request):
        if not request.user.is_staff:
            return _error("Réservé à l'équipe Paknomik.", status.HTTP_403_FORBIDDEN)
        service_type = request.data.get("serviceType")
        row = ServiceAvailability.objects.filter(service_type=service_type).first()
        if row is None:
            return _error("Service introuvable.", status.HTTP_404_NOT_FOUND)
        if "state" in request.data:
            if request.data["state"] not in ServiceAvailability.State.values:
                return _error("État de disponibilité inconnu.")
            row.state = request.data["state"]
        if "message" in request.data:
            row.message = str(request.data.get("message") or "")
        if "freeQuota" in request.data:
            raw = request.data.get("freeQuota")
            if raw in (None, ""):
                row.free_quota = None
            else:
                try:
                    row.free_quota = int(raw)
                except (TypeError, ValueError):
                    return _error("Le quota inclus doit être un nombre.")
                if row.free_quota < 0:
                    return _error("Le quota inclus doit être positif.")
        if "unitCost" in request.data:
            raw = request.data.get("unitCost")
            if raw in (None, ""):
                row.unit_cost = None
            else:
                try:
                    row.unit_cost = Decimal(str(raw))
                except (InvalidOperation, ValueError):
                    return _error("Le barème doit être un montant.")
                if row.unit_cost < 0:
                    return _error("Le barème doit être positif.")
        row.save()
        return Response(_availability_payload(row))


def _request_queryset(request, account):
    if request.user.is_staff and request.query_params.get("scope") == "team":
        qs = ServiceRequest.objects.all()
    else:
        qs = ServiceRequest.objects.filter(account=account)
    return qs.select_related("project", "packaging_item")


class ServiceRequestListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        account, error = _buyer_account(request)
        if error and not request.user.is_staff:
            return error
        if account is None and request.user.is_staff:
            qs = ServiceRequest.objects.all()
        else:
            qs = _request_queryset(request, account)
        service_type = (request.query_params.get("serviceType") or "").strip()
        status_filter = (request.query_params.get("status") or "").strip()
        project_id = (request.query_params.get("project") or "").strip()
        if service_type:
            qs = qs.filter(service_type=service_type)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if project_id:
            qs = qs.filter(project_id=project_id)
        page, meta = _page(request, qs)
        return Response({**meta, "results": [_request_summary(item) for item in page]})

    def post(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        service_type = str(request.data.get("serviceType") or "").strip()
        if service_type not in ServiceRequest.ServiceType.values:
            return _error("Type de service inconnu.")
        availability = ServiceAvailability.objects.filter(service_type=service_type).first()
        if availability is None or availability.state != ServiceAvailability.State.AVAILABLE_ON_REQUEST:
            message = availability.message if availability and availability.message else (
                "Ce service n'est pas disponible."
            )
            return _error(message, status.HTTP_403_FORBIDDEN)
        title = str(request.data.get("title") or "").strip()
        if not title:
            return _error("Le titre est obligatoire.")
        try:
            payload = clean_payload(service_type, _load_payload(request))
            desired_on = _parse_date(request.data.get("desiredOn"))
        except PayloadError as exc:
            return _error(exc.message)
        project = None
        project_id = request.data.get("projectId")
        if project_id:
            project = _owned_project(account, project_id)
            if project is None:
                return _error("Projet introuvable.", status.HTTP_404_NOT_FOUND)
        packaging_item = None
        item_id = request.data.get("packagingItemId")
        if item_id:
            packaging_item = PackagingItem.objects.filter(
                pk=item_id, project__account=account
            ).first()
            if packaging_item is None:
                return _error("Emballage introuvable.", status.HTTP_404_NOT_FOUND)
            if project and packaging_item.project_id != project.id:
                return _error("L'emballage n'appartient pas à ce projet.")
            project = project or packaging_item.project
        uploads = request.FILES.getlist("files")
        file_error = clean_request_files(uploads)
        if file_error:
            return _error(file_error)
        charge = service_charge_amount(account, service_type, availability)
        try:
            with transaction.atomic():
                service_request = ServiceRequest.objects.create(
                    account=account,
                    requested_by=request.user,
                    service_type=service_type,
                    project=project,
                    packaging_item=packaging_item,
                    title=title,
                    description=str(request.data.get("description") or ""),
                    payload=payload,
                    payload_schema_version=schema_version(),
                    desired_on=desired_on,
                    quota_cost=charge,
                )
                service_request.reference = f"S-{service_request.pk:04d}"
                service_request.save(update_fields=["reference"])
                if charge is not None and charge > 0:
                    debit_for_service(
                        account,
                        service_type,
                        charge,
                        reason=f"{availability.label} — {service_request.reference}",
                        idempotency_key=f"service-request-{service_request.pk}",
                        service_request=service_request,
                    )
                for upload in uploads:
                    ServiceRequestFile.objects.create(
                        request=service_request,
                        file=upload,
                        original_name=upload.name,
                        uploaded_by=request.user,
                    )
        except WalletError as exc:
            return _error(exc.message)
        return Response(
            _request_summary(service_request),
            status=status.HTTP_201_CREATED,
        )


def _visible_request(request, account, pk):
    qs = ServiceRequest.objects.filter(pk=pk)
    if not (request.user.is_staff and request.query_params.get("scope") == "team"):
        qs = qs.filter(Q(account=account) | Q(requested_by=request.user))
    return (
        qs.prefetch_related(
            "files",
            "messages__files",
            "deliverables",
            "events",
            "paksuites_options__components",
        )
        .first()
    )


class ServiceRequestDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        account, error = _buyer_account(request)
        if error and not request.user.is_staff:
            return error
        service_request = _visible_request(request, account, pk)
        if service_request is None:
            return _error("Demande introuvable.", status.HTTP_404_NOT_FOUND)
        for option in service_request.paksuites_options.all():
            for component in option.components.all():
                demand = component.quote_demands.first()
                component.quote_demand_id = demand.id if demand else None
        return Response(_request_detail(service_request, request))

    def patch(self, request, pk):
        """Rattache la demande à un projet de l'acheteur. Le statut ne change pas."""
        account, error = _buyer_account(request)
        if error:
            return error
        service_request = _visible_request(request, account, pk)
        if service_request is None:
            return _error("Demande introuvable.", status.HTTP_404_NOT_FOUND)
        if "projectId" not in request.data:
            return _error("Indiquez le projet à enregistrer.")
        raw = request.data.get("projectId")
        if raw in (None, ""):
            service_request.project = None
        else:
            project = _owned_project(account, raw)
            if project is None:
                return _error("Projet introuvable.", status.HTTP_404_NOT_FOUND)
            item = service_request.packaging_item
            if item is not None and item.project_id != project.id:
                return _error("L'emballage n'appartient pas à ce projet.")
            service_request.project = project
        service_request.save(update_fields=["project", "updated_at"])
        return Response(_request_detail(service_request, request))


class ServiceRequestMessageView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request, pk):
        account, error = _buyer_account(request)
        if error and not request.user.is_staff:
            return error
        service_request = _visible_request(request, account, pk)
        if service_request is None:
            return _error("Demande introuvable.", status.HTTP_404_NOT_FOUND)
        body = str(request.data.get("body") or "").strip()
        uploads = request.FILES.getlist("files")
        if not body and not uploads:
            return _error("Le message est vide.")
        file_error = clean_request_files(uploads)
        if file_error:
            return _error(file_error)
        role = (
            ServiceRequestMessage.Role.TEAM
            if request.user.is_staff and request.user.id != service_request.requested_by_id
            else ServiceRequestMessage.Role.BUYER
        )
        message = ServiceRequestMessage.objects.create(
            request=service_request,
            author=request.user,
            role=role,
            body=body,
        )
        for upload in uploads:
            ServiceRequestMessageFile.objects.create(
                message=message,
                file=upload,
                original_name=upload.name,
            )
        return Response(
            {"id": message.id, "role": message.role, "body": message.body},
            status=status.HTTP_201_CREATED,
        )


class ServiceRequestTransitionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not request.user.is_staff:
            return _error("Réservé à l'équipe Paknomik.", status.HTTP_403_FORBIDDEN)
        service_request = get_object_or_404(ServiceRequest, pk=pk)
        to_status = str(request.data.get("status") or "").strip()
        try:
            transition_status(service_request, to_status, request.user)
        except TransitionError as exc:
            return _error(exc.message, status.HTTP_409_CONFLICT)
        return Response(_request_summary(service_request))


class ServiceRequestDeliverableView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request, pk):
        if not request.user.is_staff:
            return _error("Réservé à l'équipe Paknomik.", status.HTTP_403_FORBIDDEN)
        service_request = get_object_or_404(ServiceRequest, pk=pk)
        label = str(request.data.get("label") or "").strip()
        if not label:
            return _error("Le libellé du livrable est obligatoire.")
        result = request.data.get("result")
        if isinstance(result, str) and result.strip():
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return _error("Le résultat n'est pas un JSON valide.")
        if result in ("", None):
            result = None
        elif not isinstance(result, (dict, list)):
            return _error("Le résultat doit être un objet ou une liste JSON.")
        upload = request.FILES.get("file")
        if upload:
            file_error = clean_request_files([upload])
            if file_error:
                return _error(file_error)
        is_indicative = request.data.get("isIndicative", True)
        if isinstance(is_indicative, str):
            is_indicative = is_indicative.lower() not in ("false", "0", "non")
        else:
            is_indicative = bool(is_indicative)
        if service_request.service_type == ServiceRequest.ServiceType.PAKMATH:
            is_indicative = True
        version = service_request.deliverables.count() + 1
        deliverable = ServiceRequestDeliverable.objects.create(
            request=service_request,
            label=label,
            file=upload or "",
            result=result,
            version=version,
            is_indicative=is_indicative,
            created_by=request.user,
        )
        return Response(_deliverable_data(deliverable, request), status=status.HTTP_201_CREATED)


class PaksuitesOptionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not request.user.is_staff:
            return _error("Réservé à l'équipe Paknomik.", status.HTTP_403_FORBIDDEN)
        service_request = get_object_or_404(ServiceRequest, pk=pk)
        if service_request.service_type != ServiceRequest.ServiceType.PAKSUITES:
            return _error("Les options PakSuites concernent une demande PakSuites.")
        title = str(request.data.get("title") or "").strip()
        if not title:
            return _error("Le titre de la combinaison est obligatoire.")
        components = request.data.get("components") or []
        if not isinstance(components, list):
            return _error("« components » doit être une liste.")
        prepared = []
        for raw in components:
            if not isinstance(raw, dict):
                return _error("Chaque composant doit être un objet.")
            packaging_type = str(raw.get("packagingType") or "").strip()
            if not packaging_type:
                return _error("Le type d'emballage du composant est obligatoire.")
            supplier = None
            supplier_id = raw.get("suggestedSupplierId")
            if supplier_id:
                supplier = Supplier.objects.filter(pk=supplier_id, is_active=True).first()
                if supplier is None:
                    return _error("Fournisseur suggéré introuvable.", status.HTTP_404_NOT_FOUND)
            prepared.append((packaging_type, raw, supplier))
        with transaction.atomic():
            option = PaksuitesOption.objects.create(service_request=service_request, title=title)
            created = []
            for index, (packaging_type, raw, supplier) in enumerate(prepared):
                component = PaksuitesComponent.objects.create(
                    option=option,
                    packaging_type=packaging_type,
                    description=str(raw.get("description") or ""),
                    specification=str(raw.get("specification") or ""),
                    suggested_supplier=supplier,
                    sort_order=index,
                )
                created.append(component.id)
        return Response(
            {"id": option.id, "title": option.title, "componentIds": created},
            status=status.HTTP_201_CREATED,
        )


class PaksuitesQuoteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        account, error = _buyer_account(request)
        if error:
            return error
        option = (
            PaksuitesOption.objects.select_related(
                "service_request", "service_request__packaging_item", "service_request__project"
            )
            .filter(pk=pk, service_request__account=account)
            .first()
        )
        if option is None:
            return _error("Option PakSuites introuvable.", status.HTTP_404_NOT_FOUND)
        try:
            demands = generate_quotes_for_option(option, request.user)
        except QuoteGenerationError as exc:
            return _error(exc.message, status.HTTP_409_CONFLICT)
        return Response(
            {
                "optionId": option.id,
                "quoteDemandIds": [demand.id for demand in demands],
                "createdCount": len(demands),
            }
        )


class LibraryProductListView(APIView):
    """Bibliothèque produits classée par secteur (templates illustratifs)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        qs = LibraryProduct.objects.filter(is_active=True).select_related(
            "sector", "recommended_suite"
        ).prefetch_related(
            "recommended_suite__images",
            "kit_assignments__kit__packaging_kit",
        )
        sector = (request.query_params.get("sector") or "").strip()
        if sector:
            qs = qs.filter(Q(sector__code=sector) | Q(sector__label_fr__icontains=sector))
        query = (request.query_params.get("q") or "").strip()
        if query:
            qs = qs.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(sector__label_fr__icontains=query)
            )
        page, meta = _page(request, qs.order_by("sector__sort_order", "sort_order", "name"))
        sectors = [
            {"code": item.code, "label": item.label_fr}
            for item in LibrarySector.objects.all()
        ]
        results = []
        for item in page:
            suite = item.recommended_suite
            recommended = None
            if suite is not None:
                overview = next(
                    (
                        img
                        for img in suite.images.all()
                        if img.view_type == "overview"
                        and img.file
                        and img.rights == "allowed"
                    ),
                    None,
                )
                recommended = {
                    "code": suite.code,
                    "slug": suite.slug,
                    "name": suite.name,
                    "imageUrl": overview.file.url if overview else None,
                }
            assignments = []
            for link in item.kit_assignments.all():
                try:
                    sheet = link.kit.packaging_kit
                except PackagingKit.DoesNotExist:
                    sheet = None
                assignments.append(
                    {
                        "role": link.role,
                        "code": link.kit.code,
                        "name": link.kit.name,
                        "status": sheet.status_label if sheet else "",
                        "components": sheet.components_label if sheet else "",
                        "componentCount": sheet.component_count if sheet else 0,
                    }
                )
            assignments.sort(key=lambda row: (row["role"] != "Principal", row["code"]))
            results.append(
                {
                    "id": item.id,
                    "code": item.code,
                    "name": item.name,
                    "description": item.description,
                    "sectorCode": item.sector.code,
                    "sectorLabel": item.sector.label_fr,
                    "imageUrl": item.file.url if item.file else None,
                    "illustrative": item.illustrative,
                    "recommendedSuite": recommended,
                    "kitAssignments": assignments,
                }
            )
        return Response({**meta, "sectors": sectors, "results": results})


class PackagingTypePickerView(APIView):
    """Articles d'emballage (types taxonomie) pour l'assemblage projet."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        qs = SuiteTaxon.objects.filter(kind=SuiteTaxon.Kind.TYPE).select_related(
            "parent"
        ).prefetch_related("images")
        family = (request.query_params.get("family") or "").strip()
        if family:
            qs = qs.filter(Q(parent__code=family) | Q(parent__label_fr__icontains=family))
        query = (request.query_params.get("q") or "").strip()
        if query:
            qs = qs.filter(Q(label_fr__icontains=query) | Q(code__icontains=query))
        page, meta = _page(request, qs.order_by("parent__code", "label_fr"))
        families = [
            {"code": item.code, "label": item.label_fr}
            for item in SuiteTaxon.objects.filter(kind=SuiteTaxon.Kind.FAMILY).exclude(
                code="F-X"
            )
        ]
        results = []
        for item in page:
            image = next(
                (
                    img
                    for img in item.images.all()
                    if img.file and img.rights == "allowed"
                ),
                None,
            )
            results.append(
                {
                    "code": item.code,
                    "label": item.label_fr,
                    "familyCode": item.parent.code if item.parent_id else "",
                    "familyLabel": item.parent.label_fr if item.parent_id else "",
                    "imageUrl": image.file.url if image else None,
                }
            )
        return Response({**meta, "families": families, "results": results})


class SuiteKitPickerView(APIView):
    """Kits PakSuits publiés pour l'assemblage projet."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, error = _buyer_account(request)
        if error:
            return error
        qs = SuiteEnsemble.objects.filter(
            editorial_status=SuiteEnsemble.Status.PUBLISHED
        ).prefetch_related("images", "sectors", "lines")
        query = (request.query_params.get("q") or "").strip()
        if query:
            qs = qs.filter(
                Q(name__icontains=query)
                | Q(code__icontains=query)
                | Q(description__icontains=query)
            )
        sector = (request.query_params.get("sector") or "").strip()
        if sector:
            qs = qs.filter(
                Q(sectors__code=sector) | Q(sectors__label_fr__icontains=sector)
            ).distinct()
        page, meta = _page(request, qs.order_by("priority", "code"))
        sectors = [
            {"code": item.code, "label": item.label_fr}
            for item in SuiteTaxon.objects.filter(kind=SuiteTaxon.Kind.SECTOR).order_by(
                "label_fr"
            )
        ]
        results = []
        for item in page:
            overview = next(
                (
                    img
                    for img in item.images.all()
                    if img.view_type == "overview" and img.file and img.rights == "allowed"
                ),
                None,
            )
            if overview is None:
                overview = next(
                    (
                        img
                        for img in item.images.all()
                        if img.file and img.rights == "allowed"
                    ),
                    None,
                )
            results.append(
                {
                    "code": item.code,
                    "slug": item.slug,
                    "name": item.name,
                    "description": item.description,
                    "componentCount": item.lines.count(),
                    "sectors": [s.label_fr for s in item.sectors.all()],
                    "imageUrl": overview.file.url if overview else None,
                }
            )
        return Response({**meta, "sectors": sectors, "results": results})
