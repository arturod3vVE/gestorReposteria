import uuid

from django.shortcuts import render, redirect
from decimal import Decimal
from .models import Ingredient, PaymentDestination, Product, Category, RecipeItem, Order, OrderItem, Customer, Payment, ExchangeRate, StoreSettings
from django.db.models import ProtectedError, Sum, Count, Exists, OuterRef
from django.utils import timezone
from datetime import datetime
import requests
import json
from datetime import timedelta
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages 
from django.contrib.auth.decorators import user_passes_test, login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from .services import process_payment_action, process_telegram_command
from .utils import enviar_whatsapp_background, send_telegram_receipt_async
from django.views.decorators.http import require_POST
from django.conf import settings
from django.urls import reverse
from django.http import HttpResponse

@login_required
def dashboard(request):
    hoy = timezone.now().date()
    mes_actual = hoy.month
    anio_actual = hoy.year
    user = request.user
    
    ventas_hoy = Order.objects.filter(user=user, created_at__date=hoy).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    ventas_mes = Order.objects.filter(user=user, created_at__month=mes_actual, created_at__year=anio_actual).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    
    total_facturado = Order.objects.filter(user=user).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    
    total_pagado = Payment.objects.filter(order__user=user, is_verified=True).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    deuda_total = total_facturado - total_pagado
    
    entregas_hoy_count = Order.objects.filter(user=user, expected_delivery_date__date=hoy, status__in=['PENDING', 'PREPARING']).count()
    pagos_por_verificar = Payment.objects.filter(order__user=user, is_verified=False).count()
    
    ultima_tasa = ExchangeRate.objects.filter(user=user).order_by('-created_at').first()
    tasa_dia = ultima_tasa.rate if ultima_tasa else Decimal('1.00')

    ordenes_pendientes = Order.objects.filter(user=user).exclude(payment_status='PAID').select_related('customer')
    deudores = []
    for order in ordenes_pendientes:
        if order.balance_due_calculated > 0:
            deudores.append({
                'cliente': order.customer.full_name if order.customer else "Venta de Mostrador",
                'telefono': order.customer.phone if order.customer else '',
                'monto': order.balance_due_calculated,
                'fecha_entrega': order.expected_delivery_date,
                'public_id': order.public_id,
                'customer_public_id': order.customer.public_id if order.customer else None,
            })
    deudores = sorted(deudores, key=lambda x: x['monto'], reverse=True)[:5]

    proximas_entregas = Order.objects.filter(
        user=user,
        expected_delivery_date__date__gte=hoy,
        status__in=['PENDING', 'PREPARING']
    ).select_related('customer').order_by('expected_delivery_date')[:5]

    alertas_stock_ing = Ingredient.objects.filter(user=user, track_stock=True, stock_quantity__lt=5).order_by('stock_quantity')
    alertas_stock_prod = Product.objects.filter(user=user, track_stock=True, stock_quantity__lt=5).order_by('stock_quantity')

    context = {
        'ventas_hoy': ventas_hoy,
        'ventas_mes': ventas_mes,
        'deuda_total': deuda_total,
        'entregas_hoy_count': entregas_hoy_count,
        'pagos_por_verificar': pagos_por_verificar,
        'tasa_dia': tasa_dia,
        'deudores': deudores,
        'proximas_entregas': proximas_entregas,
        'alertas_stock_ing': alertas_stock_ing,
        'alertas_stock_prod': alertas_stock_prod,
    }
    
    return render(request, 'core/dashboard.html', context)


@login_required
def ingredient_list(request):
    ingredients = Ingredient.objects.filter(user=request.user).order_by('name')
    
    context = {
        'ingredients': ingredients
    }
    return render(request, 'core/ingredient_list.html', context)


@login_required
def create_ingredient(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        measurement_unit = request.POST.get('measurement_unit')
        
        cost_per_unit = Decimal(request.POST.get('cost_per_unit', '0'))
        
        track_stock = request.POST.get('track_stock') == 'on'
        
        if track_stock:
            stock_val = request.POST.get('stock_quantity')
            stock_quantity = Decimal(stock_val) if stock_val else Decimal('0.00')
        else:
            stock_quantity = Decimal('0.00')

        Ingredient.objects.create(
            user=request.user, 
            name=name,
            measurement_unit=measurement_unit,
            track_stock=track_stock,
            cost_per_unit=cost_per_unit,
            stock_quantity=stock_quantity
        )

        return redirect('ingredient_list')

    context = {
        'unidades': Ingredient.MEASUREMENT_UNITS
    }

    return render(request, 'core/ingredient_form.html', context)

@login_required
def edit_ingredient(request, pk):
    ingredient = get_object_or_404(Ingredient, pk=pk, user=request.user)

    if request.method == 'POST':
        ingredient.name = request.POST.get('name')
        ingredient.measurement_unit = request.POST.get('measurement_unit')
        cost_str = request.POST.get('cost_per_unit', '0').replace(',', '.')
        ingredient.cost_per_unit = Decimal(cost_str)
        ingredient.track_stock = request.POST.get('track_stock') == 'on'
        if ingredient.track_stock:
            stock_str = request.POST.get('stock_quantity', '0').replace(',', '.')
            ingredient.stock_quantity = Decimal(stock_str)
        else:
            ingredient.stock_quantity = Decimal('0')

        ingredient.save()
        messages.success(request, f'El ingrediente "{ingredient.name}" ha sido actualizado correctamente.')
        return redirect('ingredient_list')

    context = {
        'ingredient': ingredient
    }
    return render(request, 'core/ingredient_edit.html', context)


@login_required
def delete_ingredient(request, pk):
    ingredient = get_object_or_404(Ingredient, pk=pk, user=request.user)
    
    if request.method == 'POST':
        try:
            name = ingredient.name
            ingredient.delete()
            messages.warning(request, f'El ingrediente "{name}" ha sido eliminado de la biblioteca.')
            return redirect('ingredient_list')
        except ProtectedError:
            messages.error(request, f'No se puede eliminar "{ingredient.name}" porque actualmente está siendo utilizado en una o más recetas. Edita el ingrediente en su lugar.')
            return redirect('ingredient_list')
            
    return render(request, 'core/ingredient_confirm_delete.html', {'ingredient': ingredient})


@login_required
def product_list(request):
    products = Product.objects.filter(user=request.user).select_related('category').prefetch_related(
        'recipe_items__ingredient'
    ).order_by('name')
    
    context = {
        'products': products
    }
    
    return render(request, 'core/product_list.html', context)


@login_required
def create_product(request):
    categories = Category.objects.filter(user=request.user).order_by('name')
    ingredients = Ingredient.objects.filter(user=request.user).order_by('name')

    if request.method == 'POST':
        name = request.POST.get('name')
        category_id = request.POST.get('category')
        description = request.POST.get('description', '')
        sale_price = Decimal(request.POST.get('sale_price', '0'))
        is_available = request.POST.get('is_available') == 'on'
        yield_val = request.POST.get('recipe_yield')
        recipe_yield = int(yield_val) if yield_val else 1
        track_stock = request.POST.get('track_stock') == 'on'
        stock_quantity = int(request.POST.get('stock_quantity', '0')) if track_stock else 0

        category_obj = get_object_or_404(Category, id=category_id, user=request.user)

        product = Product.objects.create(
            user=request.user,
            name=name,
            category=category_obj,
            description=description,
            sale_price=sale_price,
            recipe_yield=recipe_yield,
            is_available=is_available,
            track_stock=track_stock,
            stock_quantity=stock_quantity,
        )

        ingredient_ids = request.POST.getlist('ingredient_id[]')
        quantities = request.POST.getlist('quantity_required[]')

        for i in range(len(ingredient_ids)):
            if ingredient_ids[i] and quantities[i]:
                ing_obj = get_object_or_404(Ingredient, id=ingredient_ids[i], user=request.user)
                
                RecipeItem.objects.create(
                    product=product,
                    ingredient=ing_obj,
                    quantity_required=Decimal(quantities[i])
                )

        return redirect('product_list')

    context = {
        'categories': categories,
        'ingredients': ingredients
    }
    return render(request, 'core/product_form.html', context)

@login_required
def create_category(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')

        Category.objects.create(
            user=request.user, 
            name=name,
            description=description
        )

        return redirect('create_product')

    return render(request, 'core/category_form.html')


@login_required
def order_list(request):
    status_filter = request.GET.get('status')
    payment_status_filter = request.GET.get('payment_status')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    pending_payments_filter = request.GET.get('pending_payments') 

    pagos_pendientes_subquery = Payment.objects.filter(
        order=OuterRef('pk'),
        is_verified=False
    )

    orders_list = Order.objects.filter(user=request.user).select_related('customer').prefetch_related(
        'items__product', 
        'payments'
    ).annotate(
        tiene_pagos_pendientes=Exists(pagos_pendientes_subquery)
    ).order_by('-created_at')

    if status_filter:
        orders_list = orders_list.filter(status=status_filter)
    
    if payment_status_filter:
        orders_list = orders_list.filter(payment_status=payment_status_filter)
    
    if start_date:
        orders_list = orders_list.filter(created_at__date__gte=start_date)
    
    if end_date:
        orders_list = orders_list.filter(created_at__date__lte=end_date)
        
    if pending_payments_filter == 'yes':
        orders_list = orders_list.filter(tiene_pagos_pendientes=True)

    paginator = Paginator(orders_list, 10)
    page = request.GET.get('page')

    try:
        orders = paginator.page(page)
    except PageNotAnInteger:
        orders = paginator.page(1)
    except EmptyPage:
        orders = paginator.page(paginator.num_pages)

    context = {
        'orders': orders,
        'status_choices': Order.ORDER_STATUS, 
        'payment_status_choices': Order.PAYMENT_STATUS,
        'current_status': status_filter,
        'current_payment_status': payment_status_filter,
        'current_start': start_date,
        'current_end': end_date,
        'current_pending': pending_payments_filter,
    }
    
    return render(request, 'core/order_list.html', context)


@login_required
def create_order(request):
    customers = Customer.objects.filter(user=request.user).order_by('full_name')
    products = Product.objects.filter(user=request.user, is_available=True).order_by('name')

    if request.method == 'POST':
        product_ids = request.POST.getlist('product_id[]') or request.POST.getlist('product_id')
        quantities = request.POST.getlist('quantity[]') or request.POST.getlist('quantity')

        requested_qtys = {}
        for i in range(len(product_ids)):
            if product_ids[i] and quantities[i]:
                pid = int(product_ids[i])
                qty = int(quantities[i])
                requested_qtys[pid] = requested_qtys.get(pid, 0) + qty

        has_errors = False
        for pid, total_qty in requested_qtys.items():
            prod_obj = get_object_or_404(Product, id=pid, user=request.user)
            if prod_obj.track_stock and total_qty > prod_obj.stock_quantity:
                messages.error(request, f'¡Stock insuficiente! Solicitaste {total_qty} unidades de "{prod_obj.name}", pero solo quedan {prod_obj.stock_quantity} disponibles.')
                has_errors = True

        if has_errors:
            return redirect('create_order')

        customer_id = request.POST.get('customer')
        expected_delivery_date_str = request.POST.get('expected_delivery_date')
        special_notes = request.POST.get('special_notes', '')
        status = request.POST.get('status', 'PENDING')

        expected_delivery_date = None
        if expected_delivery_date_str:
            naive_datetime = datetime.strptime(expected_delivery_date_str, '%Y-%m-%dT%H:%M')
            expected_delivery_date = timezone.make_aware(naive_datetime)

        customer_obj = get_object_or_404(Customer, id=customer_id, user=request.user) if customer_id else None

        order = Order.objects.create(
            user=request.user,
            customer=customer_obj,
            expected_delivery_date=expected_delivery_date,
            special_notes=special_notes,
            status=status
        )

        total_amount = Decimal('0.00')

        for i in range(len(product_ids)):
            if product_ids[i] and quantities[i]:
                prod_obj = Product.objects.get(id=product_ids[i], user=request.user)
                qty = int(quantities[i])
                
                item = OrderItem(
                    order=order,
                    product=prod_obj,
                    quantity=qty,
                    unit_price=prod_obj.sale_price 
                )
                item.save()

                total_amount += (item.unit_price * Decimal(qty))

        order.total_amount = total_amount
        order.save()

        if order.customer and order.customer.phone:
            ruta_relativa = reverse('public_payment_link', args=[order.public_id])
            link_pago = request.build_absolute_uri(ruta_relativa)
            
            detalle_productos = ""
            for item in order.items.all():
                subtotal = item.quantity * item.unit_price
                detalle_productos += f"▫️ {item.quantity}x {item.product.name} = ${subtotal}\n"

            mensaje = (
                f"¡Hola {order.customer.full_name}! 👋\n\n"
                f"Tu orden #{order.id} ha sido registrada con éxito.\n\n"
                f"📦 *Detalle de tu pedido:*\n"
                f"{detalle_productos}\n"
                f"💰 *Total a pagar:* ${order.total_amount}\n\n"
                f"🧾 Puedes ver tu estado de cuenta y reportar tu pago de forma segura en este enlace:\n"
                f"{link_pago}\n\n"
                f"¡Gracias por preferirnos! 🍪"
            )
            
            store_uuid = None
            if hasattr(request.user, 'store_settings'):
                store_uuid = request.user.store_settings.whatsapp_uuid
                
            if store_uuid:
                enviar_whatsapp_background(order.customer.phone, mensaje, store_uuid)
            else:
                print(f"⚠️ Orden {order.id} creada, pero no se envió WhatsApp porque la tienda no tiene UUID configurado.")

        messages.success(request, f'Orden #{order.id} creada exitosamente.')
        return redirect('order_list')

    context = {
        'customers': customers,
        'products': products
    }
    return render(request, 'core/order_form.html', context)

@login_required
def update_order_status(request, public_id, new_status):
    if request.method == 'POST':
        order = get_object_or_404(Order, public_id=public_id, user=request.user)
        
        if new_status == 'CANCELLED':
            if order.status != 'PENDING' or order.payment_status != 'PENDING':
                messages.error(request, f'No puedes cancelar la Orden #{order.id} porque ya tiene pagos registrados o ya ha sido entregada.')
                return redirect(request.META.get('HTTP_REFERER', 'order_list'))

        if new_status == 'CANCELLED' and order.status != 'CANCELLED':
            for item in order.items.all():
                if item.product.track_stock:
                    item.product.stock_quantity += item.quantity
                    item.product.save()
                    
        elif order.status == 'CANCELLED' and new_status != 'CANCELLED':
            for item in order.items.all():
                if item.product.track_stock:
                    item.product.stock_quantity -= item.quantity
                    item.product.save()

        order.status = new_status
        order.save()
        messages.info(request, f'La Orden #{order.id} ahora está marcada como {order.get_status_display()}.')
        
    return redirect(request.META.get('HTTP_REFERER', 'order_list'))

@login_required
def create_customer(request):
    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        phone = request.POST.get('phone')
        email = request.POST.get('email')
        delivery_address = request.POST.get('delivery_address')

        Customer.objects.create(
            user=request.user,
            full_name=full_name,
            phone=phone,
            email=email,
            delivery_address=delivery_address
        )

        return redirect('order_list')

    return render(request, 'core/customer_form.html')

@login_required
def customer_list(request):
    search_query = request.GET.get('search', '')
    
    customers_qs = Customer.objects.filter(user=request.user).annotate(
        total_orders=Count('orders')
    ).order_by('full_name')

    if search_query:
        customers_qs = customers_qs.filter(full_name__icontains=search_query)

    paginator = Paginator(customers_qs, 10)
    page_number = request.GET.get('page')

    try:
        customers = paginator.page(page_number)
    except PageNotAnInteger:
        customers = paginator.page(1)
    except EmptyPage:
        customers = paginator.page(paginator.num_pages)

    context = {
        'customers': customers,
        'search_query': search_query,
    }
    return render(request, 'core/customer_list.html', context)

@login_required
def edit_customer(request, public_id):
    customer = get_object_or_404(Customer, public_id=public_id, user=request.user)
    
    if request.method == 'POST':
        customer.full_name = request.POST.get('full_name')
        customer.phone = request.POST.get('phone')
        customer.email = request.POST.get('email')
        customer.delivery_address = request.POST.get('delivery_address')
        customer.save()
        
        messages.success(request, f'Datos de {customer.full_name} actualizados exitosamente.')
        return redirect('customer_list')
        
    return render(request, 'core/customer_edit.html', {'customer': customer})

@login_required
def delete_customer(request, public_id):
    customer = get_object_or_404(Customer, public_id=public_id, user=request.user)
    
    if request.method == 'POST':
        name = customer.full_name
        customer.delete()
        messages.warning(request, f'El cliente "{name}" ha sido eliminado del directorio. Sus órdenes anteriores se mantendrán en el registro.')
    return redirect('customer_list')

@login_required
def manage_exchange_rate(request):
    if request.method == 'POST':
        nueva_tasa = request.POST.get('rate')
        if nueva_tasa:
            ExchangeRate.objects.create(
                user=request.user,
                rate=Decimal(nueva_tasa.replace(',', '.')) 
            )
        return redirect('dashboard')

    history = ExchangeRate.objects.filter(user=request.user).order_by('-created_at')[:10]
    
    return render(request, 'core/exchange_rate_form.html', {'history': history})

@login_required
def order_detail(request, public_id):
    order = get_object_or_404(
        Order.objects.select_related('customer').prefetch_related(
            'items__product', 
            'payments'
        ), 
        public_id=public_id,
        user=request.user
    )
    
    ultima_tasa = ExchangeRate.objects.filter(user=request.user).order_by('-created_at').first()
    tasa_dia = ultima_tasa.rate if ultima_tasa else Decimal('1.00')

    total_bs = round(order.total_calculated * tasa_dia, 2)
    balance_bs = round(order.balance_due_calculated * tasa_dia, 2)
    has_whatsapp_active = False
    try:
        # Buscamos la configuración de forma segura
        settings_obj = getattr(request.user, 'store_settings', None)
        
        if settings_obj and getattr(settings_obj, 'whatsapp_uuid', None):
            uuid_secreto = str(settings_obj.whatsapp_uuid)
            url = f"{settings.WHATSAPP_API_URL}/session/{uuid_secreto}"
            
            # ⏱️ Timeout súper corto (1.5s) para no ralentizar la página si Railway está dormido
            response = requests.get(url, timeout=1.5) 
            
            if response.status_code == 200 and response.json().get('status') == 'CONNECTED':
                has_whatsapp_active = True
    except Exception as e:
        pass

    context = {
        'order': order,
        'tasa_dia': tasa_dia,
        'total_bs': total_bs,
        'balance_bs': balance_bs,
        'has_whatsapp_active': has_whatsapp_active, # <-- Se lo pasamos al HTML
    }
    
    return render(request, 'core/order_detail.html', context)

@login_required
def order_invoice(request, public_id):
    order = get_object_or_404(
        Order.objects.select_related('customer').prefetch_related(
            'items__product', 
            'payments'
        ), 
        public_id=public_id,
        user=request.user
    )
    
    ultima_tasa = ExchangeRate.objects.filter(user=request.user).order_by('-created_at').first()
    tasa_dia = ultima_tasa.rate if ultima_tasa else Decimal('1.00')

    total_bs = round(order.total_calculated * tasa_dia, 2)
    balance_bs = round(order.balance_due_calculated * tasa_dia, 2)
    
    context = {
        'order': order,
        'tasa_dia': tasa_dia,
        'total_bs': total_bs,
        'balance_bs': balance_bs
    }
    
    return render(request, 'core/order_invoice.html', context)


def public_payment_link(request, public_id):
    # Cambiamos get_object_or_404 por filter().first() para evitar el error 404
    order = Order.objects.filter(public_id=public_id).first()
    
    # Inicializamos variables por defecto por si la orden no existe
    context = {
        'order': order,
    }

    if order:
        tenant = order.user
        amount_pending = order.amount_pending
        max_reportable = order.balance_due_calculated - Decimal(amount_pending)
        if max_reportable < 0:
            max_reportable = Decimal('0.00')

        if request.method == 'POST':
            if order.status == 'CANCELLED':
                messages.error(request, 'Acción denegada: Esta orden ha sido cancelada y no admite nuevos pagos.')
                return redirect('public_payment_link', public_id=order.public_id)

            if max_reportable > 0:
                amount_str = request.POST.get('amount')
                payment_method = request.POST.get('payment_method')
                reference_number = request.POST.get('reference_number')
                receipt_file = request.FILES.get('receipt')
                
                destination_id = request.POST.get('destination_id')
                destination_obj = None
                if destination_id:
                    destination_obj = PaymentDestination.objects.filter(id=destination_id, user=tenant).first()

                client_amount = Decimal(amount_str)
                if client_amount > max_reportable:
                    client_amount = max_reportable

                tiempo_limite = timezone.now() - timedelta(minutes=2)
                
                es_duplicado = Payment.objects.filter(
                    order=order,
                    amount=client_amount,
                    reference_number=reference_number,
                    reported_at__gte=tiempo_limite
                ).exists()

                if es_duplicado:
                    return redirect('public_payment_link', public_id=order.public_id)

                new_payment = Payment.objects.create(
                    order=order,
                    payment_method=payment_method,
                    destination=destination_obj, 
                    amount=client_amount,
                    reference_number=reference_number,
                    receipt=receipt_file,
                    is_verified=False 
                )
                
                send_telegram_receipt_async(new_payment, new_payment.amount, is_bulk=False)
                
                messages.success(request, '¡Tu pago ha sido reportado exitosamente! Lo verificaremos en breve.')
                return redirect('public_payment_link', public_id=order.public_id)
                
        ultima_tasa = ExchangeRate.objects.filter(user=tenant).order_by('-created_at').first()
        tasa_dia = ultima_tasa.rate if ultima_tasa else Decimal('1.00')

        balance_bs = round(order.balance_due_calculated * tasa_dia, 2)
        max_reportable_bs = round(max_reportable * tasa_dia, 2)

        destinations = PaymentDestination.objects.filter(user=tenant, is_active=True).order_by('destination_type')

        # Actualizamos el contexto con los datos de la orden real
        context.update({
            'tasa_dia': tasa_dia,
            'balance_bs': balance_bs,
            'amount_pending': amount_pending,
            'max_reportable': max_reportable,
            'max_reportable_bs': max_reportable_bs,
            'destinations': destinations
        })
    
    return render(request, 'core/public_payment.html', context)

def customer_bulk_payment(request, public_id):
    customer = get_object_or_404(Customer, public_id=public_id)
    tenant = customer.user 
    
    pending_orders = Order.objects.filter(
        customer=customer, 
        status__in=['PENDING', 'PREPARING', 'DELIVERED']
    ).exclude(payment_status='PAID').order_by('created_at')

    orders_with_debt = [order for order in pending_orders if order.balance_due_calculated > 0]
    
    total_debt = sum(order.balance_due_calculated for order in orders_with_debt)
    total_pending = sum(order.amount_pending for order in orders_with_debt)
    
    max_reportable = total_debt - total_pending
    if max_reportable < 0:
        max_reportable = Decimal('0.00')

    ultima_tasa = ExchangeRate.objects.filter(user=tenant).order_by('-created_at').first()
    tasa_dia = ultima_tasa.rate if ultima_tasa else Decimal('1.00')

    max_reportable_bs = round(max_reportable * tasa_dia, 2)
    destinations = PaymentDestination.objects.filter(user=tenant, is_active=True).order_by('destination_type')

    if request.method == 'POST' and max_reportable > 0:
        payment_method = request.POST.get('payment_method')
        reference_number = request.POST.get('reference_number')
        receipt_file = request.FILES.get('receipt')
        
        destination_id = request.POST.get('destination_id')
        destination_obj = None
        if destination_id:
            destination_obj = PaymentDestination.objects.filter(id=destination_id, user=tenant).first()

        first_payment_record = None
        remaining_to_distribute = max_reportable
        group_token = uuid.uuid4()

        for order in orders_with_debt:
            if remaining_to_distribute <= 0:
                break
            
            order_unverified = order.amount_pending
            order_actual_debt = order.balance_due_calculated - order_unverified
            
            if order_actual_debt <= 0:
                continue
                
            amount_to_apply = min(remaining_to_distribute, order_actual_debt)

            payment = Payment(
                order=order,
                payment_method=payment_method,
                destination=destination_obj,
                amount=amount_to_apply, 
                reference_number=f"{reference_number}",
                is_verified=False,
                transaction_group=group_token,
            )

            if receipt_file and first_payment_record is None:
                payment.receipt = receipt_file
                payment.save()
                first_payment_record = payment
            elif first_payment_record and first_payment_record.receipt:
                payment.receipt = first_payment_record.receipt
                payment.save()
            else:
                payment.save()
                
            remaining_to_distribute -= amount_to_apply
            
            if first_payment_record:
                total_applied = max_reportable - remaining_to_distribute
                send_telegram_receipt_async(first_payment_record, total_applied, is_bulk=True)

        messages.success(request, '¡Liquidación de cuenta reportada exitosamente! Nuestro equipo la verificará a la brevedad.')
        return redirect('customer_bulk_payment', public_id=customer.public_id)

    context = {
        'customer': customer,
        'orders': orders_with_debt,
        'total_debt': total_debt,
        'max_reportable': max_reportable,
        'max_reportable_bs': max_reportable_bs,
        'tasa_dia': tasa_dia,
        'destinations': destinations
    }
    return render(request, 'core/customer_bulk_payment.html', context)

@require_POST
@login_required
def resend_telegram_receipt(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id, order__user=request.user)
    
    is_bulk = bool(payment.transaction_group)
    
    try:
        send_telegram_receipt_async(payment, payment.amount, is_bulk=is_bulk)
        messages.success(request, 'El comprobante se ha puesto en cola para reenviarse a Telegram.')
    except Exception as e:
        messages.error(request, f'Hubo un error al intentar reenviar: {str(e)}')
        
    return redirect(request.META.get('HTTP_REFERER', '/'))


@login_required
def quick_cash_payment(request, public_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, public_id=public_id, user=request.user)
        balance = order.balance_due_calculated
        
        if balance > 0:
            try:
                Payment.objects.create(
                    order=order,
                    payment_method='CASH',
                    amount=balance,
                    reference_number='Liquidación Rápida (Efectivo)',
                    is_verified=True
                )
                
                order.payment_status = 'PAID'
                order.save()
                
                messages.success(request, f'Liquidación rápida en efectivo por ${balance} completada.')
                
            except ValidationError as e:
                error_msg = e.messages[0] if hasattr(e, 'messages') else str(e)
                messages.error(request, f'Acción denegada: {error_msg}')
                
        return redirect('order_detail', public_id=order.public_id)
        
    return redirect('order_list')


@login_required
def verify_order_payments(request, public_id):
    order = get_object_or_404(Order, public_id=public_id, user=request.user)
    
    if request.method == 'POST':
        payment_id = request.POST.get('payment_id')
        action = request.POST.get('action')
        payment = get_object_or_404(Payment, id=payment_id, order=order)
        
        success, result_message = process_payment_action(payment, action)
        
        if success:
            if action == 'approve':
                messages.success(request, result_message)
            else:
                messages.warning(request, result_message)
        else:
            messages.error(request, result_message)

        return redirect('verify_order_payments', public_id=order.public_id)

    payments = order.payments.all().order_by('is_verified', '-reported_at')
    for payment in payments:
        if payment.transaction_group:
            payment.siblings = Payment.objects.filter(
                transaction_group=payment.transaction_group
            ).exclude(id=payment.id).select_related('order')
    
    context = {
        'order': order,
        'payments': payments
    }
    return render(request, 'core/verify_payments.html', context)


@login_required
def payment_config_list(request):
    destinations = PaymentDestination.objects.filter(user=request.user).order_by('-is_active', 'name')

    if request.method == 'POST':
        PaymentDestination.objects.create(
            user=request.user,
            name=request.POST.get('name'),
            destination_type=request.POST.get('destination_type'),
            bank=request.POST.get('bank'),
            phone=request.POST.get('phone'),
            document_type=request.POST.get('document_type'),
            document_number=request.POST.get('document_number'),
            account_number=request.POST.get('account_number'),
            email=request.POST.get('email'),
            owner_name=request.POST.get('owner_name'),
            is_active=True
        )
        messages.success(request, '¡Método de pago registrado exitosamente!')
        return redirect('payment_config_list')

    context = {
        'destinations': destinations
    }
    return render(request, 'core/payment_config.html', context)


@login_required
def toggle_payment_destination(request, pk):
    destination = get_object_or_404(PaymentDestination, pk=pk, user=request.user)
    destination.is_active = not destination.is_active
    destination.save()
    
    status = "activado" if destination.is_active else "desactivado"
    messages.info(request, f'El método "{destination.name}" ha sido {status}.')
    return redirect('payment_config_list')


@login_required
def edit_recipe(request, pk):
    product = get_object_or_404(Product, pk=pk, user=request.user)
    categories = Category.objects.filter(user=request.user)
    ingredients = Ingredient.objects.filter(user=request.user).order_by('name')

    if request.method == 'POST':
        product.name = request.POST.get('name')
        
        cat_id = request.POST.get('category')
        if cat_id:
            category_obj = get_object_or_404(Category, id=cat_id, user=request.user)
            product.category = category_obj
            
        product.description = request.POST.get('description', '')
        product.sale_price = request.POST.get('sale_price')
        product.recipe_yield = request.POST.get('recipe_yield')
        product.is_available = request.POST.get('is_available') == 'on'
        product.track_stock = request.POST.get('track_stock') == 'on'
        product.stock_quantity = int(request.POST.get('stock_quantity', '0')) if product.track_stock else 0
        product.save()

        product.recipe_items.all().delete() 
        
        ingredient_ids = request.POST.getlist('ingredient_id[]')
        quantities = request.POST.getlist('quantity_required[]')
        
        for i in range(len(ingredient_ids)):
            if ingredient_ids[i] and quantities[i]:
                ing_obj = get_object_or_404(Ingredient, id=ingredient_ids[i], user=request.user)
                RecipeItem.objects.create(
                    product=product,
                    ingredient=ing_obj,
                    quantity_required=quantities[i]
                )
                
        messages.success(request, f'La receta de "{product.name}" ha sido actualizada.')
        return redirect('product_list')

    context = {
        'product': product,
        'categories': categories,
        'ingredients': ingredients,
    }
    return render(request, 'core/product_edit.html', context)


@login_required
def delete_product(request, pk):
    product = get_object_or_404(Product, pk=pk, user=request.user)
    
    if request.method == 'POST':
        try:
            product_name = product.name
            product.delete()
            messages.warning(request, f'El producto "{product_name}" ha sido eliminado del catálogo.')
            return redirect('product_list')
        except ProtectedError:
            messages.error(request, f'No se puede eliminar "{product.name}" porque ya existen ventas asociadas a este producto. Sugerencia: Edita el producto y márcalo como "Oculto/Inactivo".')
            return redirect('product_list')
            
    return render(request, 'core/product_confirm_delete.html', {'product': product})

Este error (UnboundLocalError) es un clásico de Python y ocurre porque el código está intentando usar la variable chat_id antes de que se le haya asignado un valor, o fuera del bloque donde se definió.

En el código anterior, si Telegram enviaba un tipo de notificación que no era ni un callback_query ni un message estándar (o si fallaba justo en la línea donde se extraía el ID), la variable quedaba "en el aire".

Aquí tienes el código corregido. He movido la extracción del chat_id al principio de forma segura para que siempre esté disponible, especialmente para tu lógica de ID de Grupo.

Código Completo y Corregido
Python
import json
import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
# Asegúrate de que estos imports existan en tu proyecto
# from .models import Payment, StoreSettings
# from .utils import process_payment_action, process_telegram_command

@csrf_exempt
def telegram_webhook(request, token=None):
    if request.method == 'POST':
        # Inicializamos chat_id como None para evitar el error de "local variable"
        chat_id = None 
        
        try:
            update = json.loads(request.body.decode('utf-8'))
            TOKEN = settings.TELEGRAM_BOT_TOKEN

            # ==========================================
            # 1. EXTRACCIÓN SEGURA DEL CHAT_ID
            # ==========================================
            if 'callback_query' in update:
                chat_id = update['callback_query']['message']['chat']['id']
            elif 'message' in update:
                chat_id = update['message']['chat']['id']
            elif 'edited_message' in update:
                chat_id = update['edited_message']['chat']['id']

            # Si no logramos detectar un chat_id, ignoramos este update silenciosamente
            if chat_id is None:
                return JsonResponse({"status": "ignored_no_chat_id"})

            # Convertimos a string para comparar con la base de datos
            str_chat_id = str(chat_id).strip()

            # ==========================================
            # 2. PROCESAR BOTONES (Callback Queries)
            # ==========================================
            if 'callback_query' in update:
                callback = update['callback_query']
                message_id = callback['message']['message_id']
                data = callback['data'] 
                
                # Extraemos la acción y el pago
                try:
                    action_short, payment_id = data.split('_')
                    payment = Payment.objects.get(id=payment_id)
                    tienda_owner = payment.order.user
                    config = tienda_owner.store_settings
                    
                    # 🔒 SEGURIDAD POR GRUPO: Comparamos el chat_id del grupo
                    if not config.telegram_chat_id or str_chat_id != str(config.telegram_chat_id).strip():
                        print(f"⚠️ Chat no autorizado: {str_chat_id} intentó gestionar pago de {tienda_owner.username}")
                        requests.get(f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery", 
                                     params={
                                         'callback_query_id': callback['id'], 
                                         'text': '❌ Este grupo no está autorizado para este pago.', 
                                         'show_alert': True
                                     })
                        return JsonResponse({"status": "unauthorized"})

                    # Ejecutamos la acción
                    action_full = 'approve' if action_short == 'app' else 'reject'
                    success, result_message = process_payment_action(payment, action_full)
                    
                    # Formatear respuesta visual
                    estado_emoji = "✅" if success and action_full == 'approve' else "🗑️" if success else "❌"
                    is_photo = 'caption' in callback['message']
                    original_text = callback['message'].get('caption', callback['message'].get('text', ''))
                    nuevo_texto = f"{original_text}\n\n{estado_emoji} *{result_message}*"

                    # Notificar a Telegram y editar mensaje
                    requests.get(f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery?callback_query_id={callback['id']}")
                    
                    payload = {
                        'chat_id': chat_id, 
                        'message_id': message_id, 
                        'parse_mode': 'Markdown',
                        'reply_markup': json.dumps({'inline_keyboard': []}) 
                    }
                    
                    endpoint = "editMessageCaption" if is_photo else "editMessageText"
                    if is_photo: payload['caption'] = nuevo_texto
                    else: payload['text'] = nuevo_texto
                    
                    requests.post(f"https://api.telegram.org/bot{TOKEN}/{endpoint}", json=payload)

                except Exception as inner_e:
                    print(f"Error interno procesando callback: {inner_e}")
                    requests.get(f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery", 
                                 params={'callback_query_id': callback['id'], 'text': '❌ Error al procesar el pago.', 'show_alert': True})

            # ==========================================
            # 3. PROCESAR COMANDOS DE TEXTO
            # ==========================================
            elif 'message' in update and 'text' in update['message']:
                texto_recibido = update['message']['text']
                
                if texto_recibido.startswith('/start'):
                    reply_text = (
                        f"👋 ¡Hola! Bienvenido a CrumbCore.\n\n"
                        f"El ID de este chat es: `{chat_id}`\n\n"
                        f"📌 Configúralo en tu panel para recibir notificaciones aquí."
                    )
                    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                                  json={'chat_id': chat_id, 'text': reply_text, 'parse_mode': 'Markdown'})
                
                elif texto_recibido.startswith('/'):
                    respuesta_texto = process_telegram_command(texto_recibido, chat_id)
                    if respuesta_texto:
                        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                                      json={'chat_id': chat_id, 'text': respuesta_texto, 'parse_mode': 'Markdown'})

        except Exception as e:
            # Aquí chat_id ya existe (como None o con valor), evitando el UnboundLocalError
            print(f"❌ Error crítico en el Webhook: {e}")
    
        return JsonResponse({"status": "ok"})
    
    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
def pending_payments_list(request):
    pagos_pendientes = Payment.objects.filter(
        is_verified=False, 
        order__user=request.user
    ).select_related('order', 'order__customer')
    
    pagos_agrupados = {}
    pagos_individuales = []

    for pago in pagos_pendientes:
        cliente_nombre = pago.order.customer.full_name if pago.order.customer else "Venta de Mostrador"
        
        if pago.transaction_group:
            tg = str(pago.transaction_group)
            if tg not in pagos_agrupados:
                pagos_agrupados[tg] = {
                    'es_bulk': True,
                    'fecha': pago.reported_at,
                    'cliente': cliente_nombre,
                    'referencia': pago.reference_number,
                    'monto': 0,
                    'ordenes': [],
                    'orden_principal_public_id': pago.order.public_id,
                    'pago_id': pago.id,
                }
            pagos_agrupados[tg]['monto'] += pago.amount
            pagos_agrupados[tg]['ordenes'].append(str(pago.order.id))
        else:
            pagos_individuales.append({
                'es_bulk': False,
                'fecha': pago.reported_at,
                'cliente': cliente_nombre,
                'referencia': pago.reference_number,
                'monto': pago.amount,
                'ordenes_str': str(pago.order.id),
                'orden_principal_public_id': pago.order.public_id, 
                'pago_id': pago.id,
            })

    lista_final_pagos = pagos_individuales
    for tg, data in pagos_agrupados.items():
        data['ordenes_str'] = ", #".join(data['ordenes'])
        lista_final_pagos.append(data)

    lista_final_pagos.sort(key=lambda x: x['fecha'])

    context = {
        'pagos_lista': lista_final_pagos,
    }
    return render(request, 'core/pending_payments.html', context)


@login_required
def send_payment_link_whatsapp(request, public_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, public_id=public_id, user=request.user)
        
        if not order.customer or not order.customer.phone:
            return JsonResponse({'success': False, 'error': 'El cliente no tiene teléfono registrado.'})

        store_uuid = None
        if hasattr(request.user, 'store_settings'):
            store_uuid = request.user.store_settings.whatsapp_uuid
            
        if not store_uuid:
            return JsonResponse({
                'success': False, 
                'error': 'No tienes tu bot de WhatsApp configurado. Ve a Configuraciones.'
            })

        payment_link = request.build_absolute_uri(reverse('public_payment_link', args=[order.public_id]))
        name = order.customer.full_name or "Cliente"
        message = f"¡Hola {name}! 👋\n\nAquí tienes el enlace para reportar el pago de tu orden #{order.id}:\n{payment_link}\n\nGracias por preferirnos. 🍪"

        # 🎯 NUEVO: Pasamos el UUID secreto a la función de fondo
        enviar_whatsapp_background(order.customer.phone, message, store_uuid)
        
        return JsonResponse({'success': True, 'message': 'Mensaje enviado a la cola en segundo plano.'})
            
    return JsonResponse({'success': False, 'error': 'Método inválido.'})


@login_required
def send_customer_bulk_whatsapp(request, public_id):
    if request.method == 'POST':
        customer = get_object_or_404(Customer, public_id=public_id, user=request.user)
        
        if not customer.phone:
            return JsonResponse({'success': False, 'error': 'El cliente no tiene teléfono registrado.'})

        # 🎯 NUEVO: Validar y obtener el UUID de la tienda
        store_uuid = None
        if hasattr(request.user, 'store_settings'):
            store_uuid = request.user.store_settings.whatsapp_uuid
            
        if not store_uuid:
            return JsonResponse({
                'success': False, 
                'error': 'No tienes tu bot de WhatsApp configurado. Ve a Configuraciones.'
            })

        bulk_payment_link = request.build_absolute_uri(reverse('customer_bulk_payment', args=[customer.public_id]))
        name = customer.full_name or "Cliente"
        
        message = (
            f"¡Hola {name}! 👋\n\n"
            f"Aquí tienes el enlace para ver tu estado de cuenta y pagar todas tus órdenes pendientes en un solo paso:\n"
            f"{bulk_payment_link}\n\n"
            f"¡Gracias por preferirnos! 🍪"
        )

        # 🎯 NUEVO: Enviamos usando el UUID de la sesión de esta tienda
        enviar_whatsapp_background(customer.phone, message, store_uuid)
        
        return JsonResponse({'success': True, 'message': 'Mensaje de estado de cuenta encolado.'})
            
    return JsonResponse({'success': False, 'error': 'Método inválido.'})

@login_required
def store_settings_view(request):
    settings, created = StoreSettings.objects.get_or_create(user=request.user)
    
    if request.method == 'POST':
        settings.store_name = request.POST.get('store_name')
        settings.telegram_chat_id = request.POST.get('telegram_chat_id')
        settings.save()
        
        messages.success(request, "Configuraciones actualizadas correctamente.")
        return redirect('store_settings')
        
    return render(request, 'core/store_settings.html', {'settings': settings})


@login_required
def whatsapp_status_api(request):
    try:
        settings_obj = request.user.store_settings
        uuid_secreto = str(settings_obj.whatsapp_uuid)
        
        url = f"{settings.WHATSAPP_API_URL}/session/{uuid_secreto}"
        
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            return JsonResponse(response.json())
        return JsonResponse({'status': 'ERROR', 'detail': 'El microservicio no responde'}, status=500)
    except Exception as e:
        return JsonResponse({'status': 'ERROR', 'detail': str(e)}, status=500)

@login_required
@require_POST
def whatsapp_disconnect_api(request):
    try:
        settings_obj = request.user.store_settings
        uuid_secreto = str(settings_obj.whatsapp_uuid)
        
        url = f"{settings.WHATSAPP_API_URL}/session/{uuid_secreto}"
        response = requests.delete(url, timeout=10)
        
        if response.status_code == 200:
            return JsonResponse({'success': True})
        return JsonResponse({'success': False}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'detail': str(e)}, status=500)

@csrf_exempt  # Telegram no envía token CSRF, así que debemos eximir esta vista
def telegram_webhook_start(request):
    if request.method == 'POST':
        try:
            # Leemos la data que manda Telegram
            payload = json.loads(request.body)
            
            # Verificamos que sea un mensaje de texto normal
            if 'message' in payload:
                chat_id = payload['message']['chat']['id']
                text = payload['message'].get('text', '')
                
                # Obtenemos el nombre del usuario si lo tiene
                first_name = payload['message']['chat'].get('first_name', 'Repostero')

                # Si el mensaje es /start
                if text.startswith('/start'):
                    bot_token = settings.TELEGRAM_BOT_TOKEN
                    
                    reply_text = (
                        f"👋 ¡Hola, {first_name}! Bienvenido a CrumbCore.\n\n"
                        f"Tu ID de conexión es: <code>{chat_id}</code>\n\n"
                        f"Copia ese número (puedes tocarlo para copiar) y pégalo en la sección de "
                        f"Configuraciones de tu panel administrativo para empezar a recibir alertas de pagos."
                    )
                    
                    # Le respondemos a Telegram
                    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                    data = {
                        'chat_id': chat_id,
                        'text': reply_text,
                        'parse_mode': 'HTML'
                    }
                    requests.post(url, json=data)
                    
            # SIEMPRE debemos responder 200 OK, sino Telegram intentará reenviar el mensaje
            return HttpResponse(status=200)
            
        except Exception as e:
            print(f"Error procesando webhook de Telegram: {e}")
            return HttpResponse(status=200)
            
    # Si alguien intenta entrar por el navegador (GET), le damos error
    return HttpResponse(status=403)