from user.models import Category


def navigation_categories(request):
    categories = list(Category.objects.all())
    last_catego = categories[-1] if categories else None
    return {
        'categories': categories,
        'last_catego': last_catego,
    }
