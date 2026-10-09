"""
HTML form definitions for classic Django pages (signup, products,
supplier registration). Validates user input before saving to the DB.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import Category, Material, Product, SearchTag, Supplier

User = get_user_model()


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'auth-input',
            'placeholder': 'example@company.com',
        }),
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')
        widgets = {
            'username': forms.TextInput(attrs={
                'class': 'auth-input',
                'placeholder': 'Username',
            }),
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.is_verified_client = False
        if commit:
            user.save()
        return user


class VerifyClientForm(forms.ModelForm):
    class Meta:
        model = User
        fields = (
            'company_name',
            'nature_of_activity',
            'annual_purchase_volume',
            'wilaya',
            'address',
            'phone_number',
        )
        widgets = {
            'company_name': forms.TextInput(attrs={'class': 'auth-input'}),
            'nature_of_activity': forms.TextInput(attrs={'class': 'auth-input'}),
            'annual_purchase_volume': forms.TextInput(attrs={'class': 'auth-input'}),
            'wilaya': forms.TextInput(attrs={'class': 'auth-input'}),
            'address': forms.Textarea(attrs={'class': 'auth-textarea', 'rows': 3}),
            'phone_number': forms.TextInput(attrs={'class': 'auth-input'}),
        }


class UserProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('username', 'phone_number', 'company_name', 'address', 'wilaya')
        widgets = {
            'username': forms.TextInput(attrs={'class': 'auth-input'}),
            'phone_number': forms.TextInput(attrs={'class': 'auth-input'}),
            'company_name': forms.TextInput(attrs={'class': 'auth-input'}),
            'address': forms.Textarea(attrs={'class': 'auth-textarea', 'rows': 2}),
            'wilaya': forms.TextInput(attrs={'class': 'auth-input'}),
        }


class SupplierRegistrationForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = (
            'company_name',
            'email',
            'whatsapp_number',
            'wilaya',
            'address',
            'website',
            'presentation',
            'interested_categories',
            'interested_subcategories',
        )
        widgets = {
            'company_name': forms.TextInput(attrs={'class': 'auth-input'}),
            'email': forms.EmailInput(attrs={'class': 'auth-input'}),
            'whatsapp_number': forms.TextInput(attrs={'class': 'auth-input'}),
            'wilaya': forms.Select(attrs={'class': 'auth-select'}),
            'address': forms.Textarea(attrs={'class': 'auth-textarea', 'rows': 3}),
            'website': forms.URLInput(attrs={'class': 'auth-input'}),
            'presentation': forms.Textarea(attrs={'class': 'auth-textarea', 'rows': 5}),
            'interested_categories': forms.CheckboxSelectMultiple(),
            'interested_subcategories': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user and user.email and not self.initial.get('email'):
            self.initial['email'] = user.email
        self.fields['interested_categories'].queryset = Category.objects.filter(
            parent__isnull=True
        ).order_by('sort_order', 'name_en')
        subcategories = Category.objects.filter(
            parent__isnull=False
        ).select_related('parent').order_by('parent__sort_order', 'sort_order')
        self.fields['interested_subcategories'].queryset = subcategories
        self.fields['interested_subcategories'].label_from_instance = (
            lambda obj: f'{obj.parent.name_en} › {obj.name_en}'
        )


class SupplierProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = (
            'company_name',
            'address',
            'website',
            'whatsapp_number',
            'wilaya',
            'presentation',
            'interested_categories',
            'interested_subcategories',
        )
        widgets = {
            'company_name': forms.TextInput(attrs={'class': 'auth-input'}),
            'address': forms.Textarea(attrs={'class': 'auth-textarea', 'rows': 2}),
            'website': forms.URLInput(attrs={'class': 'auth-input'}),
            'whatsapp_number': forms.TextInput(attrs={'class': 'auth-input'}),
            'wilaya': forms.Select(attrs={'class': 'auth-select'}),
            'presentation': forms.Textarea(attrs={'class': 'auth-textarea', 'rows': 4}),
            'interested_categories': forms.CheckboxSelectMultiple(),
            'interested_subcategories': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['interested_categories'].queryset = Category.objects.filter(
            parent__isnull=True
        ).order_by('sort_order', 'name_en')
        subcategories = Category.objects.filter(
            parent__isnull=False
        ).select_related('parent').order_by('parent__sort_order', 'sort_order')
        self.fields['interested_subcategories'].queryset = subcategories
        self.fields['interested_subcategories'].label_from_instance = (
            lambda obj: f'{obj.parent.name_en} › {obj.name_en}'
        )


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = (
            'name',
            'name_ar',
            'description',
            'category',
            'subcategory',
            'material',
            'search_tags',
            'price',
            'min_order_quantity',
            'lead_time_days',
            'is_active',
            'is_featured',
        )
        widgets = {
            'name': forms.TextInput(attrs={'class': 'auth-input'}),
            'name_ar': forms.TextInput(attrs={'class': 'auth-input'}),
            'description': forms.Textarea(attrs={'class': 'auth-textarea', 'rows': 4}),
            'category': forms.Select(attrs={'class': 'auth-select'}),
            'subcategory': forms.Select(attrs={'class': 'auth-select'}),
            'material': forms.Select(attrs={'class': 'auth-select'}),
            'search_tags': forms.SelectMultiple(attrs={'class': 'auth-select', 'size': 8}),
            'price': forms.NumberInput(attrs={'class': 'auth-input', 'step': '0.01'}),
            'min_order_quantity': forms.NumberInput(attrs={'class': 'auth-input'}),
            'lead_time_days': forms.NumberInput(attrs={'class': 'auth-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_featured': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['search_tags'].queryset = SearchTag.objects.order_by(
            '-search_weight', 'tag'
        )
        self.fields['search_tags'].label_from_instance = (
            lambda obj: obj.tag_fr or obj.tag
        )
        self.fields['subcategory'].queryset = Category.objects.filter(
            parent__isnull=False
        ).select_related('parent').order_by('parent__sort_order', 'sort_order')
        self.fields['subcategory'].label_from_instance = (
            lambda obj: f'{obj.parent.name_en} › {obj.name_en}'
        )


class SearchForm(forms.Form):
    query = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'search-input',
            'placeholder': 'Search products...',
        }),
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'filter-select'}),
    )
    material_type = forms.ModelChoiceField(
        queryset=Material.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'filter-select'}),
    )
    min_price = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'filter-input', 'placeholder': 'Min price'}),
    )
    max_price = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'filter-input', 'placeholder': 'Max price'}),
    )
    SORT_CHOICES = (
        ('relevance', 'Relevance'),
        ('price_asc', 'Price: Low to High'),
        ('price_desc', 'Price: High to Low'),
        ('newest', 'Newest First'),
    )
    sort_by = forms.ChoiceField(
        choices=SORT_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'sort-select'}),
    )


class SupplierFilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'search-input',
            'placeholder': 'Search suppliers...',
        }),
    )
    wilaya = forms.ChoiceField(
        choices=[('', 'All wilayas')] + list(Supplier.WILAYA_CHOICES),
        required=False,
        widget=forms.Select(attrs={'class': 'filter-select'}),
    )
    SORT_CHOICES = (
        ('featured', 'Featured'),
        ('newest', 'Newest'),
        ('name', 'Name A-Z'),
    )
    sort_by = forms.ChoiceField(
        choices=SORT_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'sort-select'}),
    )
