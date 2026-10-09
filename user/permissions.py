"""
Decorators that protect views: only verified clients or suppliers
can open certain pages. Redirects others with a message.
"""

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def verified_client_required(view_func):
    """Restrict access to verified clients only."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.warning(request, 'Please login to continue.')
            return redirect('account_login')

        if not request.user.is_verified_client:
            messages.warning(
                request,
                'Please complete your company profile to contact suppliers and request quotations.',
            )
            return redirect('complete_profile')

        return view_func(request, *args, **kwargs)
    return wrapper


def supplier_required(view_func):
    """Restrict access to users linked to a supplier profile."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.warning(request, 'Please login to continue.')
            return redirect('account_login')

        if not request.user.supplier_id:
            messages.warning(request, 'You are not registered as a supplier.')
            return redirect('become_supplier')

        return view_func(request, *args, **kwargs)
    return wrapper
