from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # --- PANEL PRINCIPAL Y AUTENTICACIÓN ---
    path('', views.dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='core/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('config/exchange-rate/', views.manage_exchange_rate, name='manage_exchange_rate'),

    # --- INVENTARIO (Uso interno, se mantiene con ID secuencial) ---
    path('ingredients/', views.ingredient_list, name='ingredient_list'),
    path('ingredients/create/', views.create_ingredient, name='create_ingredient'),
    path('ingredients/<int:pk>/edit/', views.edit_ingredient, name='edit_ingredient'),
    path('ingredients/<int:pk>/delete/', views.delete_ingredient, name='delete_ingredient'),

    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.create_product, name='create_product'),
    path('products/<int:pk>/edit/', views.edit_recipe, name='edit_product'),
    path('products/<int:pk>/delete/', views.delete_product, name='delete_product'),
    path('categories/create/', views.create_category, name='create_category'),

    # --- ÓRDENES (Blindadas con UUID) ---
    path('orders/', views.order_list, name='order_list'),
    path('orders/create/', views.create_order, name='create_order'),
    path('orders/<uuid:public_id>/', views.order_detail, name='order_detail'),
    path('orders/<uuid:public_id>/invoice/', views.order_invoice, name='order_invoice'),
    path('orders/<uuid:public_id>/quick-cash/', views.quick_cash_payment, name='quick_cash_payment'),
    path('orders/<uuid:public_id>/payments/verify/', views.verify_order_payments, name='verify_order_payments'),
    path('orders/<uuid:public_id>/status/<str:new_status>/', views.update_order_status, name='update_order_status'),
    path('orders/<uuid:public_id>/send-link-whatsapp/', views.send_payment_link_whatsapp, name='send_payment_link_whatsapp'),
    
    # --- CLIENTES (Blindados con UUID) ---
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/create/', views.create_customer, name='create_customer'),
    path('customers/<uuid:public_id>/edit/', views.edit_customer, name='edit_customer'),
    path('customers/<uuid:public_id>/delete/', views.delete_customer, name='delete_customer'),
    path('customer/<uuid:public_id>/send-bulk-whatsapp/', views.send_customer_bulk_whatsapp, name='send_customer_bulk_whatsapp'),

    # --- ENLACES PÚBLICOS DE PAGO (Ultra blindados con UUID) ---
    path('p/<uuid:public_id>/', views.public_payment_link, name='public_payment_link'),
    path('p/c/<uuid:public_id>/', views.customer_bulk_payment, name='customer_bulk_payment'),
    
    # --- PAGOS Y WEBHOOKS ---
    path('payments/pending/', views.pending_payments_list, name='pending_payments_list'),
    path('payments/<int:payment_id>/resend-telegram/', views.resend_telegram_receipt, name='resend_telegram_receipt'),

    # --- CONFIGURACIONES ---
    path('config/payments/', views.payment_config_list, name='payment_config_list'),
    path('config/payments/<int:pk>/toggle/', views.toggle_payment_destination, name='toggle_payment_destination'),
    path('config/store/', views.store_settings_view, name='store_settings'),
    path('api/whatsapp/status/', views.whatsapp_status_api, name='whatsapp_status_api'),
    path('api/whatsapp/disconnect/', views.whatsapp_disconnect_api, name='whatsapp_disconnect_api'),

    # URL Secreta para el Webhook de Telegram
    path('tg-webhook-crumbcore-9x8z7y/', views.telegram_webhook, name='telegram_webhook'),
]