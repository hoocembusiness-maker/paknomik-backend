from django.shortcuts import render

from user.models import Category, Supplier, Product


from django.db.models import Count


from django.shortcuts import render
from django.db.models import Count
from user.models import Category, Supplier, Product

def home(request):
    # Top 4 categories with the most products
    top_categories_qs = (
        Category.objects
        .annotate(product_count=Count('products'))
        .order_by('-product_count')[:4]
    )

    # Convert to a list of (name_fr, product_count) tuples
    top_categories = [(cat.name_fr, cat.product_count) for cat in top_categories_qs]

    # If you need exactly two groups of two (for layout), pad missing entries
    # (but it's often cleaner to handle this in the template)
    # Example grouping:
    grouped = [top_categories[i:i+2] for i in range(0, len(top_categories), 2)]
    # Ensure we always have two groups (even if empty)
    while len(grouped) < 2:
        grouped.append([])

    categories = Category.objects.filter(parent__isnull=True).order_by(
        "sort_order", "name_en"
    )
    suppliers = Supplier.objects.filter(is_active=True).order_by('-is_featured', 'company_name')

    context = {
        'categories': categories,
        'suppliers': suppliers,
        'supplier_count': suppliers.count(),
        'total_categories': Category.objects.count(),
        'total_suppliers': Supplier.objects.count(),
        'total_products': Product.objects.count(),   # if needed in template
        'top_categories': grouped,   # now a list of two lists, each up to 2 items
    }
    return render(request, 'home.html', context)

def catego(request, id):
    category = Category.objects.filter(id=id).first()
    categories = Category.objects.all()
    context = {
        'category': category,
        'catego_name': category.name_en if category else 'Category',
        'categories': categories,
        'last_catego': categories.last(),
    }
    return render(request, 'components/catego.html', context)


def signup(request):
    return render(request, 'components/signup.html')


def code_confirmation(request):
    email = request.GET.get('email_signup', '')
    crypted_email = email[:3] + '****@' + email.split('@')[1] if '@' in email else email
    return render(
        request,
        'components/code_confirmation.html',
        {'email': email, 'crypted_email': crypted_email},
    )
