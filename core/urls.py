from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='core/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('config/exchange-rate/', views.manage_exchange_rate, name='manage_exchange_rate'),

    path('ingredients/', views.ingredient_list, name='ingredient_list'),
    path('ingredients/create/', views.create_ingredient, name='create_ingredient'),
    path('ingredients/<int:pk>/edit/', views.edit_ingredient, name='edit_ingredient'),
    path('ingredients/<int:pk>/delete/', views.delete_ingredient, name='delete_ingredient'),

    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.create_product, name='create_product'),
    path('products/<int:pk>/edit/', views.edit_recipe, name='edit_product'),
    path('products/<int:pk>/delete/', views.delete_product, name='delete_product'),

    path('categories/create/', views.create_category, name='create_category'),

    path('orders/', views.order_list, name='order_list'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('orders/<int:pk>/invoice/', views.order_invoice, name='order_invoice'),
    path('orders/create/', views.create_order, name='create_order'),
    path('orders/<int:pk>/quick-cash/', views.quick_cash_payment, name='quick_cash_payment'),
    path('orders/<int:pk>/payments/verify/', views.verify_order_payments, name='verify_order_payments'),
    path('orders/<int:pk>/status/<str:new_status>/', views.update_order_status, name='update_order_status'),
    
    path('p/<int:pk>/', views.public_payment_link, name='public_payment_link'),
    path('p/c/<int:customer_id>/', views.customer_bulk_payment, name='customer_bulk_payment'),

    path('customers/create/', views.create_customer, name='create_customer'),
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/<int:pk>/edit/', views.edit_customer, name='edit_customer'),
    path('customers/<int:pk>/delete/', views.delete_customer, name='delete_customer'),

    path('config/payments/', views.payment_config_list, name='payment_config_list'),
    path('config/payments/<int:pk>/toggle/', views.toggle_payment_destination, name='toggle_payment_destination'),

    # URL Secreta para el Webhook de Telegram
    path('tg-webhook-crumbcore-9x8z7y/', views.telegram_webhook, name='telegram_webhook'),
]