"""
Server-rendered page views (HTML templates) for buyers and suppliers.
The React frontend uses mostly `user/api/` instead, but some legacy
pages still live here.
"""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    CustomUserCreationForm,
    ProductForm,
    SearchForm,
    SupplierFilterForm,
    SupplierRegistrationForm,
    UserProfileUpdateForm,
    VerifyClientForm,
)
from .product_search import filter_products_by_text
from .search_suggestions import build_search_suggestions
from .events import log_seller_event
from .models import Event, Product, Supplier
from .permissions import supplier_required, verified_client_required


def _attach_primary_image(product):
    product.primary_image = product.get_display_image()
    return product


def _attach_primary_images(products):
    for product in products:
        _attach_primary_image(product)
    return products


# ========== AUTHENTICATION ==========


def signup_view(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(
                request,
                'Account created successfully! Please complete your profile to become a Verified Client.',
            )
            return redirect('complete_profile')
    else:
        form = CustomUserCreationForm()

    return render(request, 'user/signup.html', {'form': form})


@login_required
def complete_profile(request):
    if request.user.is_verified_client:
        messages.info(request, 'You are already a Verified Client!')
        return redirect('client_dashboard')

    if request.method == 'POST':
        form = VerifyClientForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            request.user.is_verified_client = True
            request.user.save(update_fields=['is_verified_client'])
            messages.success(request, 'Congratulations! You are now a Verified Client.')
            return redirect('client_dashboard')
    else:
        form = VerifyClientForm(instance=request.user)

    return render(request, 'user/complete_profile.html', {'form': form})


@login_required
def user_dashboard(request):
    if request.user.supplier_id:
        return redirect('supplier_dashboard')
    return redirect('client_dashboard')


# ========== CLIENT ==========


@login_required
def client_dashboard(request):
    if request.user.supplier_id:
        return redirect('supplier_dashboard')

    context = {
        'is_verified': request.user.is_verified_client,
    }
    return render(request, 'user/client_dashboard.html', context)


@login_required
def edit_profile(request):
    if request.method == 'POST':
        form = UserProfileUpdateForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated successfully!')
            return redirect('user_dashboard')
    else:
        form = UserProfileUpdateForm(instance=request.user)

    return render(request, 'user/edit_profile.html', {'form': form})


# ========== SUPPLIER ==========


@login_required
def become_supplier(request):
    if request.user.supplier_id:
        messages.info(request, 'You are already registered as a supplier.')
        return redirect('supplier_dashboard')

    if request.method == 'POST':
        form = SupplierRegistrationForm(request.POST, user=request.user)
        if form.is_valid():
            supplier = form.save()
            request.user.supplier = supplier
            request.user.save(update_fields=['supplier'])
            messages.success(request, 'Your supplier account has been created!')
            return redirect('supplier_dashboard')
    else:
        form = SupplierRegistrationForm(user=request.user)

    return render(request, 'user/become_supplier.html', {'form': form})


@login_required
@supplier_required
def supplier_dashboard(request):
    supplier = request.user.supplier
    products = supplier.products.all()
    log_seller_event(
        request.user,
        Event.EventType.SELLER_DASHBOARD_VIEW,
        {"supplier_id": supplier.id},
    )
    context = {
        'supplier': supplier,
        'total_products': products.count(),
        'active_products': products.filter(is_active=True).count(),
        'recent_products': products.order_by('-created_at')[:10],
    }
    return render(request, 'user/supplier_dashboard.html', context)


@login_required
@supplier_required
def supplier_products(request):
    supplier = request.user.supplier
    products = supplier.products.all().order_by('-created_at')
    paginator = Paginator(products, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'user/supplier_products.html', {'page_obj': page_obj})


@login_required
@supplier_required
def add_product(request):
    supplier = request.user.supplier

    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save(commit=False)
            product.supplier = supplier
            product.save()
            messages.success(request, 'Product added successfully!')
            return redirect('supplier_products')
    else:
        form = ProductForm()

    return render(request, 'user/add_product.html', {'form': form})


@login_required
@supplier_required
def edit_product(request, product_id):
    supplier = request.user.supplier
    product = get_object_or_404(Product, id=product_id, supplier=supplier)

    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, 'Product updated successfully!')
            return redirect('supplier_products')
    else:
        form = ProductForm(instance=product)

    return render(request, 'user/edit_product.html', {'form': form, 'product': product})


# ========== SEARCH & BROWSE ==========


def search_products(request):
    form = SearchForm(request.GET)
    products = Product.objects.filter(
        is_active=True, supplier__is_active=True, supplier__is_ghost=False
    ).select_related(
        'supplier', 'category', 'subcategory', 'material'
    ).prefetch_related('images', 'search_tags')

    if form.is_valid():
        query = form.cleaned_data.get('query')
        if query:
            products = filter_products_by_text(
                products, query, include_supplier_name=True
            )

        category = form.cleaned_data.get('category')
        if category:
            products = products.filter(category=category)

        material = form.cleaned_data.get('material_type')
        if material:
            products = products.filter(material=material)

        min_price = form.cleaned_data.get('min_price')
        if min_price is not None:
            products = products.filter(
                Q(price_max__gte=min_price) | Q(price_max__isnull=True, price__gte=min_price)
            )

        max_price = form.cleaned_data.get('max_price')
        if max_price is not None:
            products = products.filter(price__lte=max_price)

        sort_by = form.cleaned_data.get('sort_by') or 'relevance'
        if sort_by == 'price_asc':
            products = products.order_by('price')
        elif sort_by == 'price_desc':
            products = products.order_by('-price')
        elif not (query and sort_by == 'relevance'):
            products = products.order_by('-created_at')

    paginator = Paginator(products, 24)
    page_obj = paginator.get_page(request.GET.get('page'))
    _attach_primary_images(page_obj.object_list)

    query = form.cleaned_data.get('query') if form.is_valid() else None
    search_suggestions = None
    if query and not page_obj.object_list:
        search_suggestions = build_search_suggestions(query)

    context = {
        'page_obj': page_obj,
        'form': form,
        'total_results': products.count(),
        'search_suggestions': search_suggestions,
    }
    return render(request, 'user/search_results.html', context)


def suppliers_list(request):
    form = SupplierFilterForm(request.GET)
    suppliers = Supplier.objects.filter(is_active=True, is_ghost=False)
    supplier_count = suppliers.count()

    if form.is_valid():
        search = form.cleaned_data.get('search')
        if search:
            suppliers = suppliers.filter(
                Q(company_name__icontains=search) | Q(presentation__icontains=search)
            )

        wilaya = form.cleaned_data.get('wilaya')
        if wilaya:
            suppliers = suppliers.filter(wilaya=wilaya)

        sort_by = form.cleaned_data.get('sort_by') or 'featured'
        if sort_by == 'newest':
            suppliers = suppliers.order_by('-created_at')
        elif sort_by == 'name':
            suppliers = suppliers.order_by('company_name')
        else:
            suppliers = suppliers.order_by('-is_featured', 'company_name')

    paginator = Paginator(suppliers, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    suppliers_on_page = list(page_obj)
    paired_suppliers = [
        suppliers_on_page[i:i + 4] for i in range(0, len(suppliers_on_page), 4)
    ]

    context = {
        'page_obj': page_obj,
        'form': form,
        'supplier_count': supplier_count,
        'paired_suppliers': paired_suppliers,
    }
    return render(request, 'user/suppliers_list.html', context)


def supplier_detail(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id, is_active=True, is_ghost=False)
    products = supplier.products.filter(is_active=True).select_related(
        'category', 'material'
    ).prefetch_related('images')[:12]
    _attach_primary_images(products)

    context = {
        'supplier': supplier,
        'products': products,
        'product_count': supplier.products.filter(is_active=True).count(),
        'quote_categories': _quote_categories(supplier),
    }
    return render(request, 'user/supplier_detail.html', context)


def product_detail(request, product_id):
    product = get_object_or_404(
        Product.objects.select_related(
            'supplier', 'category', 'subcategory', 'material'
        ).prefetch_related(
            'images', 'search_tags', 'attribute_values__attribute'
        ),
        id=product_id,
        is_active=True,
        supplier__is_ghost=False,
    )
    _attach_primary_image(product)

    similar_products = Product.objects.filter(
        subcategory=product.subcategory,
        is_active=True,
        supplier__is_ghost=False,
    ).exclude(id=product.id).select_related(
        'subcategory', 'category'
    ).prefetch_related('images')[:6] if product.subcategory_id else Product.objects.none()
    _attach_primary_images(similar_products)

    context = {
        'product': product,
        'similar_products': similar_products,
        'attributes': product.attribute_values.select_related('attribute').all(),
    }
    return render(request, 'user/product_detail.html', context)


@verified_client_required
def contact_supplier(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id, is_active=True, is_ghost=False)
    product = None
    product_id = request.GET.get('product_id')
    if product_id:
        product = get_object_or_404(Product, id=product_id, supplier=supplier)

    if supplier.whatsapp_number:
        message = ''
        if product:
            message = f' Inquiry about: {product.name}'
        return redirect(f'https://wa.me/{supplier.whatsapp_number}?text={message.strip()}')

    if supplier.email:
        return redirect(f'mailto:{supplier.email}')

    messages.info(request, 'This supplier has no contact details yet.')
    return redirect('supplier_detail', supplier_id=supplier.id)


def _quote_categories(supplier):
    from .quote_forms import offered_category_payloads

    return offered_category_payloads(supplier)


@verified_client_required
def request_quote(request, supplier_id):
    """Buyer form tied to one supplier and one packaging type."""
    from .models import Category, RFQRequest, RFQRequestFile, RFQRequestPhoto
    from .photo_uploads import clean_photos, sanitized_photos, save_photos
    from .quote_forms import (
        QuoteFormError,
        apply_snapshot,
        clean_technical_files,
        prepare_specs,
        resolve_buyer_form,
    )
    from .scoring import handle_rfq_created

    supplier = get_object_or_404(Supplier, id=supplier_id, is_active=True, is_ghost=False)
    categories = _quote_categories(supplier)
    selected_id = request.POST.get('category') or request.GET.get('category')
    category = None
    form = None
    if selected_id and str(selected_id).isdigit():
        category = Category.objects.filter(pk=int(selected_id)).first()
    if category is not None:
        try:
            form = resolve_buyer_form(supplier, category)
        except QuoteFormError:
            form = None
            category = None

    if request.method == 'POST' and form is not None:
        specs = {
            field['key']: request.POST.get(f"spec_{field['key']}", '')
            for field in form['fields']
        }
        name = (request.POST.get('name') or '').strip()
        phone = (request.POST.get('phone') or request.user.phone_number or '').strip()
        email = request.user.email
        try:
            quantity = int(request.POST.get('quantity') or 0)
        except ValueError:
            quantity = 0
        if not name or quantity < 1:
            messages.error(request, 'Le nom et une quantité positive sont requis.')
        else:
            try:
                snapshot = prepare_specs(supplier, category, specs)
            except QuoteFormError as exc:
                messages.error(request, exc.message)
            else:
                photos = request.FILES.getlist('photos')
                photo_error = clean_photos(photos)
                file_error = clean_technical_files(request.FILES.getlist('files'))
                if photo_error or file_error:
                    messages.error(request, photo_error or file_error)
                else:
                    rfq = RFQRequest.objects.create(
                        buyer=request.user,
                        supplier=supplier,
                        category=category,
                        title=form['title'],
                        name=name,
                        company=getattr(request.user, 'company_name', '') or '',
                        email=email,
                        phone=phone,
                        quantity_estimate=quantity,
                        message=(request.POST.get('message') or '').strip(),
                        status=RFQRequest.Status.SUBMITTED,
                    )
                    save_photos(RFQRequestPhoto, 'rfq', rfq, sanitized_photos(photos))
                    for upload in request.FILES.getlist('files'):
                        RFQRequestFile.objects.create(
                            rfq=rfq, file=upload, original_name=upload.name
                        )
                    apply_snapshot(rfq, category, snapshot, actor=request.user)
                    handle_rfq_created(supplier.id, rfq.id, buyer=request.user)
                    messages.success(
                        request,
                        f'Votre demande de devis a été envoyée à {supplier.company_name}.',
                    )
                    return redirect('supplier_detail', supplier_id=supplier.id)

    return render(request, 'user/request_quote.html', {
        'supplier': supplier,
        'categories': categories,
        'category': category,
        'form': form,
        'name': request.POST.get('name') or request.user.get_full_name() or request.user.username,
    })


@supplier_required
def supplier_quote_forms(request):
    supplier = request.user.supplier
    return render(request, 'user/supplier_quote_forms.html', {
        'supplier': supplier,
        'categories': _quote_categories(supplier),
    })


@supplier_required
def supplier_quote_form_edit(request, category_id):
    from .models import Category
    from .quote_forms import QuoteFormError, save_supplier_form, supplier_form_payload

    supplier = request.user.supplier
    category = get_object_or_404(Category, pk=category_id)
    try:
        payload = supplier_form_payload(supplier, category)
    except QuoteFormError as exc:
        messages.error(request, exc.message)
        return redirect('supplier_quote_forms')

    if request.method == 'POST':
        fields = []
        for field in payload['fields']:
            key = field['key']
            fields.append({
                'key': key,
                'enabled': field['locked'] or request.POST.get(f'enabled_{key}') == '1',
                'required': field['platformRequired'] and field['locked'] or request.POST.get(
                    f'required_{key}'
                ) == '1',
                'options': request.POST.getlist(f'options_{key}') or field['platformOptions'],
            })
        extras = []
        for index in range(1, 4):
            label = (request.POST.get(f'extra_label_{index}') or '').strip()
            if not label:
                continue
            raw_options = request.POST.get(f'extra_options_{index}') or ''
            options = [part.strip() for part in raw_options.split(',') if part.strip()]
            extras.append({
                'label': label,
                'fieldType': request.POST.get(f'extra_type_{index}') or 'text',
                'required': request.POST.get(f'extra_required_{index}') == '1',
                'options': options,
            })
        try:
            payload = save_supplier_form(supplier, category, {
                'buyerInstructions': request.POST.get('buyer_instructions') or '',
                'physicalCustomization': request.POST.get('physical_customization'),
                'fields': fields,
                'extraQuestions': extras,
            })
        except QuoteFormError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, 'Formulaire de devis enregistré.')
            return redirect('supplier_quote_form_edit', category_id=category.id)

    slots = list(payload.get('extraQuestions') or [])[:3]
    while len(slots) < 3:
        slots.append({'label': '', 'fieldType': 'text', 'required': False, 'options': []})

    return render(request, 'user/supplier_quote_form_edit.html', {
        'supplier': supplier,
        'payload': payload,
        'extra_slots': [
            {
                **slot,
                'index': index,
                'options_text': ', '.join(slot.get('options') or []),
            }
            for index, slot in enumerate(slots, start=1)
        ],
        'extra_types': [
            ('text', 'Texte'),
            ('number', 'Nombre'),
            ('textarea', 'Commentaire'),
            ('choice', 'Liste'),
            ('boolean', 'Oui / non'),
        ],
    })


def compare_products(request):
    product_ids = request.GET.getlist('ids')
    products = Product.objects.filter(
        id__in=product_ids,
        is_active=True,
        supplier__is_ghost=False,
    ).select_related(
        'supplier', 'category', 'subcategory', 'material'
    ).prefetch_related('images', 'search_tags')
    _attach_primary_images(products)
    return render(request, 'user/compare_products.html', {'products': products})


# ========== API ==========


def api_top_suppliers(request):
    limit = int(request.GET.get('limit', 10))
    suppliers = Supplier.objects.filter(is_active=True, is_ghost=False).order_by(
        '-is_featured', 'company_name'
    )[:limit]

    data = [
        {
            'id': supplier.id,
            'name': supplier.company_name,
            'featured': supplier.is_featured,
            'wilaya': supplier.get_wilaya_display() if supplier.wilaya else None,
        }
        for supplier in suppliers
    ]
    return JsonResponse({'suppliers': data})


def api_search_suggestions(request):
    query = request.GET.get('q', '')
    return JsonResponse(build_search_suggestions(query))
