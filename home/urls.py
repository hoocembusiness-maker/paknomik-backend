from django.urls import path
from .views import home,  signup,code_confirmation,catego

urlpatterns = [
    path('',home,name='home'),
  #  path('login',login,name='login'),
    path('signup',signup,name='signup'),
    path('confirmation',code_confirmation,name="conformation"),
    path('catego/<int:id>/', catego, name="catego"),
]


