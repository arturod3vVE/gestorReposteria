import uuid

from django.shortcuts import render, redirect
from decimal import Decimal
from .models import Ingredient, PaymentDestination, Product, Category, RecipeItem, Order, OrderItem, Customer, Payment, ExchangeRate
from django.db.models import ProtectedError, Sum, Count
from django.utils import timezone
import requests
import json
from datetime import timedelta
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from .services import process_payment_action, process_telegram_command
from .utils import send_telegram_receipt_async
from django.conf import settings

@user_passes_test(lambda u: u.is_staff)
def dashboard(request):
    hoy = timezone.now().date()
    mes_actual = hoy.month
    anio_actual = hoy.year
    
    # 1. MÉTRICAS FINANCIERAS PRINCIPALES
    ventas_hoy = Order.objects.filter(created_at__date=hoy).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    ventas_mes = Order.objects.filter(created_at__month=mes_actual, created_at__year=anio_actual).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    
    total_facturado = Order.objects.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    total_pagado = Payment.objects.filter(is_verified=True).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    deuda_total = total_facturado - total_pagado
    
    # 2. MÉTRICAS OPERATIVAS
    entregas_hoy_count = Order.objects.filter(expected_delivery_date__date=hoy, status__in=['PENDING', 'PREPARING']).count()
    pagos_por_verificar = Payment.objects.filter(is_verified=False).count()
    
    # Tasa del día
    ultima_tasa = ExchangeRate.objects.order_by('-created_at').first()
    tasa_dia = ultima_tasa.rate if ultima_tasa else Decimal('1.00')

    # 3. CLIENTES DEUDORES (Top 5 con mayor deuda)
    ordenes_pendientes = Order.objects.exclude(payment_status='PAID').select_related('customer')
    deudores = []
    for order in ordenes_pendientes:
        if order.balance_due_calculated > 0:
            deudores.append({
                'cliente': order.customer.full_name if order.customer else "Venta de Mostrador",
                'telefono': order.customer.phone if order.customer else '',
                'monto': order.balance_due_calculated,
                'fecha_entrega': order.expected_delivery_date,
                'id': order.id,
                'customer_id': order.customer.id if order.customer else None,
            })
    deudores = sorted(deudores, key=lambda x: x['monto'], reverse=True)[:5]

    # 4. PRÓXIMAS ENTREGAS (Próximas 48 horas)
    proximas_entregas = Order.objects.filter(
        expected_delivery_date__date__gte=hoy,
        status__in=['PENDING', 'PREPARING']
    ).select_related('customer').order_by('expected_delivery_date')[:5]

    # 5. ALERTAS DE STOCK (Ingredientes < 5 y Productos Terminados < 5)
    alertas_stock_ing = Ingredient.objects.filter(track_stock=True, stock_quantity__lt=5).order_by('stock_quantity')
    alertas_stock_prod = Product.objects.filter(track_stock=True, stock_quantity__lt=5).order_by('stock_quantity')

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

@user_passes_test(lambda u: u.is_staff)
def ingredient_list(request):
    # Obtenemos todos los ingredientes ordenados alfabéticamente
    ingredients = Ingredient.objects.all().order_by('name')
    
    # Preparamos el contexto para enviarlo a la plantilla
    context = {
        'ingredients': ingredients
    }
    
    # Renderizamos la plantilla con el contexto
    return render(request, 'core/ingredient_list.html', context)

@user_passes_test(lambda u: u.is_staff)
def create_ingredient(request):
    if request.method == 'POST':
        # 1. Capturar los datos básicos
        name = request.POST.get('name')
        measurement_unit = request.POST.get('measurement_unit')
        
        # Convertimos directamente a Decimal (el frontend ya valida que vengan números)
        cost_per_unit = Decimal(request.POST.get('cost_per_unit', '0'))
        
        # 2. Verificar el slider de stock
        track_stock = request.POST.get('track_stock') == 'on'
        
        if track_stock:
            stock_val = request.POST.get('stock_quantity')
            stock_quantity = Decimal(stock_val) if stock_val else Decimal('0.00')
        else:
            stock_quantity = Decimal('0.00')

        # 3. Crear el Ingrediente en la base de datos
        Ingredient.objects.create(
            name=name,
            measurement_unit=measurement_unit,
            track_stock=track_stock,
            cost_per_unit=cost_per_unit,
            stock_quantity=stock_quantity
        )

        # 4. Redirigir a la lista de ingredientes
        return redirect('ingredient_list')

    # GET: Pasamos las opciones de unidad de medida al select
    context = {
        'unidades': Ingredient.MEASUREMENT_UNITS
    }

    return render(request, 'core/ingredient_form.html', context)

@user_passes_test(lambda u: u.is_staff)
def edit_ingredient(request, pk):
    ingredient = get_object_or_404(Ingredient, pk=pk)

    if request.method == 'POST':
        ingredient.name = request.POST.get('name')
        ingredient.measurement_unit = request.POST.get('measurement_unit')
        
        # Parseo seguro de decimales
        cost_str = request.POST.get('cost_per_unit', '0').replace(',', '.')
        ingredient.cost_per_unit = Decimal(cost_str)
        
        # Manejo del stock
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

@user_passes_test(lambda u: u.is_staff)
def delete_ingredient(request, pk):
    ingredient = get_object_or_404(Ingredient, pk=pk)
    
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

@user_passes_test(lambda u: u.is_staff)
def product_list(request):
    products = Product.objects.select_related('category').prefetch_related(
        'recipe_items__ingredient'
    ).order_by('name')
    
    context = {
        'products': products
    }
    
    return render(request, 'core/product_list.html', context)

@user_passes_test(lambda u: u.is_staff)
def create_product(request):
    categories = Category.objects.all().order_by('name')
    # Necesitamos enviarle los ingredientes a la vista para el selector
    ingredients = Ingredient.objects.all().order_by('name')

    if request.method == 'POST':
        # 1. Crear el Producto Base
        name = request.POST.get('name')
        category_id = request.POST.get('category')
        description = request.POST.get('description', '')
        sale_price = Decimal(request.POST.get('sale_price', '0'))
        is_available = request.POST.get('is_available') == 'on'
        yield_val = request.POST.get('recipe_yield')
        recipe_yield = int(yield_val) if yield_val else 1
        track_stock = request.POST.get('track_stock') == 'on'
        stock_quantity = int(request.POST.get('stock_quantity', '0')) if track_stock else 0

        category_obj = get_object_or_404(Category, id=category_id)

        product = Product.objects.create(
            name=name,
            category=category_obj,
            description=description,
            sale_price=sale_price,
            recipe_yield=recipe_yield,
            is_available=is_available,
            track_stock=track_stock,
            stock_quantity=stock_quantity,
        )

        # 2. Capturar las listas de ingredientes del frontend
        ingredient_ids = request.POST.getlist('ingredient_id[]')
        quantities = request.POST.getlist('quantity_required[]')

        # 3. Procesar y guardar cada ítem de la receta
        for i in range(len(ingredient_ids)):
            # Validamos que no vengan campos vacíos
            if ingredient_ids[i] and quantities[i]:
                ing_obj = get_object_or_404(Ingredient, id=ingredient_ids[i])
                
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

@user_passes_test(lambda u: u.is_staff)
def create_category(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')

        # Creamos la categoría
        Category.objects.create(
            name=name,
            description=description
        )

        return redirect('create_product')

    return render(request, 'core/category_form.html')

@user_passes_test(lambda u: u.is_staff)
def order_list(request):
    # 1. Captura de filtros desde el navegador
    status_filter = request.GET.get('status')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    # Query inicial optimizada
    orders_list = Order.objects.select_related('customer').prefetch_related(
        'items__product', 
        'payments'
    ).order_by('-created_at')

    # 2. Aplicación de lógica de filtrado
    if status_filter:
        orders_list = orders_list.filter(status=status_filter)
    
    if start_date:
        orders_list = orders_list.filter(created_at__date__gte=start_date)
    
    if end_date:
        orders_list = orders_list.filter(created_at__date__lte=end_date)

    # 3. Configuración del Paginador (10 órdenes por página)
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
        'status_choices': Order.ORDER_STATUS, # Para llenar el select de filtros
        # Devolvemos los filtros para que el HTML mantenga los valores en los inputs
        'current_status': status_filter,
        'current_start': start_date,
        'current_end': end_date,
    }
    
    return render(request, 'core/order_list.html', context)

@user_passes_test(lambda u: u.is_staff)
def create_order(request):
    customers = Customer.objects.all().order_by('full_name')
    products = Product.objects.filter(is_available=True).order_by('name')

    if request.method == 'POST':
        product_ids = request.POST.getlist('product_id[]') or request.POST.getlist('product_id')
        quantities = request.POST.getlist('quantity[]') or request.POST.getlist('quantity')

        # --- 1. FASE DE VALIDACIÓN ESTRICTA (PRE-FLIGHT CHECK) ---
        # Agrupamos las cantidades solicitadas (por si agregan el mismo producto en dos filas distintas)
        requested_qtys = {}
        for i in range(len(product_ids)):
            if product_ids[i] and quantities[i]:
                pid = int(product_ids[i])
                qty = int(quantities[i])
                requested_qtys[pid] = requested_qtys.get(pid, 0) + qty

        has_errors = False
        for pid, total_qty in requested_qtys.items():
            prod_obj = get_object_or_404(Product, id=pid)
            # Verificamos si lleva stock y si se está pidiendo de más
            if prod_obj.track_stock and total_qty > prod_obj.stock_quantity:
                messages.error(request, f'¡Stock insuficiente! Solicitaste {total_qty} unidades de "{prod_obj.name}", pero solo quedan {prod_obj.stock_quantity} disponibles.')
                has_errors = True

        if has_errors:
            # Abortamos la creación de la orden y recargamos el formulario
            return redirect('create_order')
        # ---------------------------------------------------------

        # 2. Si pasó la validación, procedemos a crear la Orden
        customer_id = request.POST.get('customer')
        expected_delivery_date = request.POST.get('expected_delivery_date')
        special_notes = request.POST.get('special_notes', '')
        status = request.POST.get('status', 'PENDING')

        customer_obj = get_object_or_404(Customer, id=customer_id) if customer_id else None

        order = Order.objects.create(
            customer=customer_obj,
            expected_delivery_date=expected_delivery_date,
            special_notes=special_notes,
            status=status
        )

        total_amount = Decimal('0.00')

        # 3. Guardamos cada item (El modelo OrderItem restará el stock automáticamente)
        for i in range(len(product_ids)):
            if product_ids[i] and quantities[i]:
                prod_obj = get_object_or_404(Product, id=product_ids[i])
                qty = int(quantities[i])
                
                item = OrderItem(
                    order=order,
                    product=prod_obj,
                    quantity=qty,
                    unit_price=prod_obj.sale_price # Congelamos el precio
                )
                item.save()

                total_amount += (item.unit_price * Decimal(qty))

        order.total_amount = total_amount
        order.save()

        messages.success(request, f'Orden #{order.id} creada exitosamente.')
        return redirect('order_list')

    context = {
        'customers': customers,
        'products': products
    }
    return render(request, 'core/order_form.html', context)

@user_passes_test(lambda u: u.is_staff)
def update_order_status(request, pk, new_status):
    if request.method == 'POST':
        order = get_object_or_404(Order, pk=pk)
        
        # --- NUEVO CANDADO DE SEGURIDAD PARA CANCELACIONES ---
        if new_status == 'CANCELLED':
            # Solo permitimos cancelar si está en su estado inicial puro
            if order.status != 'PENDING' or order.payment_status != 'PENDING':
                messages.error(request, f'No puedes cancelar la Orden #{order.id} porque ya tiene pagos registrados o ya ha sido entregada.')
                return redirect(request.META.get('HTTP_REFERER', 'order_list'))
        # -----------------------------------------------------

        # LÓGICA DE INVENTARIO: Si se cancela, devolvemos el stock a la vitrina
        if new_status == 'CANCELLED' and order.status != 'CANCELLED':
            for item in order.items.all():
                if item.product.track_stock:
                    item.product.stock_quantity += item.quantity
                    item.product.save()
                    
        # Si se "descancela", volvemos a restar el stock
        elif order.status == 'CANCELLED' and new_status != 'CANCELLED':
            for item in order.items.all():
                if item.product.track_stock:
                    item.product.stock_quantity -= item.quantity
                    item.product.save()

        order.status = new_status
        order.save()
        messages.info(request, f'La Orden #{order.id} ahora está marcada como {order.get_status_display()}.')
        
    return redirect(request.META.get('HTTP_REFERER', 'order_list'))

@user_passes_test(lambda u: u.is_staff)
def create_customer(request):
    if request.method == 'POST':
        # Captura manual de los campos del modelo
        full_name = request.POST.get('full_name')
        phone = request.POST.get('phone')
        email = request.POST.get('email')
        delivery_address = request.POST.get('delivery_address')

        # Creación del registro en la base de datos
        Customer.objects.create(
            full_name=full_name,
            phone=phone,
            email=email,
            delivery_address=delivery_address
        )

        return redirect('order_list')

    return render(request, 'core/customer_form.html')

@user_passes_test(lambda u: u.is_staff)
def customer_list(request):
    search_query = request.GET.get('search', '')
    
    # Traemos los clientes y contamos cuántas órdenes tiene cada uno
    customers_qs = Customer.objects.annotate(
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

@user_passes_test(lambda u: u.is_staff)
def edit_customer(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    
    if request.method == 'POST':
        customer.full_name = request.POST.get('full_name')
        customer.phone = request.POST.get('phone')
        customer.email = request.POST.get('email')
        customer.delivery_address = request.POST.get('delivery_address')
        customer.save()
        
        messages.success(request, f'Datos de {customer.full_name} actualizados exitosamente.')
        return redirect('customer_list')
        
    return render(request, 'core/customer_edit.html', {'customer': customer})

@user_passes_test(lambda u: u.is_staff)
def delete_customer(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        name = customer.full_name
        customer.delete()
        messages.warning(request, f'El cliente "{name}" ha sido eliminado del directorio. Sus órdenes anteriores se mantendrán en el registro.')
    return redirect('customer_list')

@user_passes_test(lambda u: u.is_staff)
def manage_exchange_rate(request):
    if request.method == 'POST':
        nueva_tasa = request.POST.get('rate')
        if nueva_tasa:
            ExchangeRate.objects.create(rate=Decimal(nueva_tasa))
        return redirect('dashboard')

    # Obtenemos las últimas 10 tasas para el histórico
    history = ExchangeRate.objects.all().order_by('-created_at')[:10]
    
    return render(request, 'core/exchange_rate_form.html', {'history': history})

@user_passes_test(lambda u: u.is_staff)
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related('customer').prefetch_related(
            'items__product', 
            'payments'
        ), 
        pk=pk
    )
    
    # Obtenemos la última tasa registrada (Si no hay, usamos 1.00 por defecto)
    ultima_tasa = ExchangeRate.objects.order_by('-created_at').first()
    tasa_dia = ultima_tasa.rate if ultima_tasa else Decimal('1.00')

    # Calculamos los equivalentes en Bolívares para la vista
    total_bs = round(order.total_calculated * tasa_dia, 2)
    balance_bs = round(order.balance_due_calculated * tasa_dia, 2)
    
    context = {
        'order': order,
        'tasa_dia': tasa_dia,
        'total_bs': total_bs,
        'balance_bs': balance_bs
    }
    
    return render(request, 'core/order_detail.html', context)

def order_invoice(request, pk):
    order = get_object_or_404(
        Order.objects.select_related('customer').prefetch_related(
            'items__product', 
            'payments'
        ), 
        pk=pk
    )
    
    # Obtenemos la tasa para los cálculos en Bs.
    ultima_tasa = ExchangeRate.objects.order_by('-created_at').first()
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

def public_payment_link(request, pk):
    order = get_object_or_404(Order, pk=pk)
    
    # 1. Variables estrictamente necesarias para AMBOS flujos (GET y POST)
    amount_pending = order.amount_pending
    max_reportable = order.balance_due_calculated - Decimal(amount_pending)
    if max_reportable < 0:
        max_reportable = Decimal('0.00')

    # 2. FLUJO POST (Procesamiento de datos)
    if request.method == 'POST':
        if order.status == 'CANCELLED':
            messages.error(request, 'Acción denegada: Esta orden ha sido cancelada y no admite nuevos pagos.')
            return redirect('public_payment_link', pk=order.pk)

        if max_reportable > 0:
            amount_str = request.POST.get('amount')
            payment_method = request.POST.get('payment_method')
            reference_number = request.POST.get('reference_number')
            receipt_file = request.FILES.get('receipt')
            
            # Rescatamos el ID de la cuenta destino seleccionada
            destination_id = request.POST.get('destination_id')
            destination_obj = None
            if destination_id:
                destination_obj = PaymentDestination.objects.filter(id=destination_id).first()

            client_amount = Decimal(amount_str)
            if client_amount > max_reportable:
                client_amount = max_reportable

            # 🛡️ EL CANDADO ANTI-DUPLICADOS (Idempotencia)
            # Calculamos la hora exacta de hace 2 minutos
            tiempo_limite = timezone.now() - timedelta(minutes=2)
            
            # Buscamos si ya existe un registro idéntico súper reciente
            es_duplicado = Payment.objects.filter(
                order=order,
                amount=client_amount,
                reference_number=reference_number,
                reported_at__gte=tiempo_limite
            ).exists()

            if es_duplicado:
                # Si es un doble envío fantasma, lo ignoramos por completo.
                # Redirigimos al usuario para que crea que todo salió bien en su "primer" clic.
                return redirect('public_payment_link', pk=order.pk)

            # Si pasa el filtro, creamos el pago normalmente
            new_payment = Payment.objects.create(
                order=order,
                payment_method=payment_method,
                destination=destination_obj, 
                amount=client_amount,
                reference_number=reference_number,
                receipt=receipt_file,
                is_verified=False 
            )
            
            # Disparamos el evento asíncrono a Telegram
            send_telegram_receipt_async(new_payment, new_payment.amount, is_bulk=False)
            
            messages.success(request, '¡Tu pago ha sido reportado exitosamente! Lo verificaremos en breve.')
            return redirect('public_payment_link', pk=order.pk)
            
    # 3. FLUJO GET (Solo se ejecuta si no fue un POST. Aquí usamos tus variables)
    ultima_tasa = ExchangeRate.objects.order_by('-created_at').first()
    tasa_dia = ultima_tasa.rate if ultima_tasa else Decimal('1.00')

    balance_bs = round(order.balance_due_calculated * tasa_dia, 2)
    max_reportable_bs = round(max_reportable * tasa_dia, 2)

    destinations = PaymentDestination.objects.filter(is_active=True).order_by('destination_type')

    context = {
        'order': order,
        'tasa_dia': tasa_dia,
        'balance_bs': balance_bs,
        'amount_pending': amount_pending,
        'max_reportable': max_reportable,
        'max_reportable_bs': max_reportable_bs,
        'destinations': destinations
    }
    return render(request, 'core/public_payment.html', context)

def customer_bulk_payment(request, customer_id):
    customer = get_object_or_404(Customer, id=customer_id)
    
    # 1. Obtenemos órdenes con deuda pendiente
    pending_orders = Order.objects.filter(
        customer=customer, 
        status__in=['PENDING', 'PREPARING', 'DELIVERED']
    ).exclude(payment_status='PAID').order_by('created_at')

    orders_with_debt = [order for order in pending_orders if order.balance_due_calculated > 0]
    
    # 2. Cálculos Financieros Consolidados
    total_debt = sum(order.balance_due_calculated for order in orders_with_debt)
    total_pending = sum(order.amount_pending for order in orders_with_debt) # Dinero en revisión
    
    # Lo que realmente se le permite reportar al cliente
    max_reportable = total_debt - total_pending
    if max_reportable < 0:
        max_reportable = Decimal('0.00')

    ultima_tasa = ExchangeRate.objects.order_by('-created_at').first()
    tasa_dia = ultima_tasa.rate if ultima_tasa else Decimal('1.00')

    max_reportable_bs = round(max_reportable * tasa_dia, 2)
    destinations = PaymentDestination.objects.filter(is_active=True).order_by('destination_type')

    # 3. Solo procesamos si realmente hay dinero faltando por reportar
    if request.method == 'POST' and max_reportable > 0:
        payment_method = request.POST.get('payment_method')
        reference_number = request.POST.get('reference_number')
        receipt_file = request.FILES.get('receipt')

        first_payment_record = None
        remaining_to_distribute = max_reportable
        group_token = uuid.uuid4()

        for order in orders_with_debt:
            if remaining_to_distribute <= 0:
                break
            
            # Calculamos la deuda real de esta orden (quitando lo que ya se reportó antes para ella)
            order_unverified = order.amount_pending
            order_actual_debt = order.balance_due_calculated - order_unverified
            
            if order_actual_debt <= 0:
                continue # Esta orden específica ya tiene su pago en revisión, pasamos a la siguiente
                
            amount_to_apply = min(remaining_to_distribute, order_actual_debt)

            payment = Payment(
                order=order,
                payment_method=payment_method,
                amount=amount_to_apply, 
                reference_number=f"{reference_number} (Liquidación Múltiple)",
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

        messages.success(request, '¡Liquidación de cuenta reportada exitosamente! Nuestro equipo la verificará a la brevedad.')
        return redirect('customer_bulk_payment', customer_id=customer.id)

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

@user_passes_test(lambda u: u.is_staff)
def quick_cash_payment(request, pk):
    # We use POST for security to prevent duplicate payments on reload
    if request.method == 'POST':
        order = get_object_or_404(Order, pk=pk)
        balance = order.balance_due_calculated
        
        if balance > 0:
            try:
                # 1. Create the verified payment record
                # Esto detonará el ValidationError si la orden está cancelada
                Payment.objects.create(
                    order=order,
                    payment_method='CASH',
                    amount=balance,
                    reference_number='Liquidación Rápida (Efectivo)',
                    is_verified=True # Automatically approved
                )
                
                # 2. Update the Order's payment status and save to DB
                # Esto también está protegido por el Fat Model
                order.payment_status = 'PAID'
                order.save()
                
                messages.success(request, f'Liquidación rápida en efectivo por ${balance} completada.')
                
            except ValidationError as e:
                # ¡ATRAPAMOS EL ERROR! Y lo enviamos a la interfaz de usuario
                error_msg = e.messages[0] if hasattr(e, 'messages') else str(e)
                messages.error(request, f'Acción denegada: {error_msg}')
                
        return redirect('order_detail', pk=order.pk)
        
    return redirect('order_list')

@user_passes_test(lambda u: u.is_staff)
def verify_order_payments(request, pk):
    order = get_object_or_404(Order, pk=pk)
    
    if request.method == 'POST':
        payment_id = request.POST.get('payment_id')
        action = request.POST.get('action')
        payment = get_object_or_404(Payment, id=payment_id, order=order)
        
        # MAGIC: Llamamos al servicio reutilizable
        success, result_message = process_payment_action(payment, action)
        
        if success:
            if action == 'approve':
                messages.success(request, result_message)
            else:
                messages.warning(request, result_message)
        else:
            messages.error(request, result_message)

        return redirect('verify_order_payments', pk=order.pk)

    # Fetch payments, unverified first
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

@user_passes_test(lambda u: u.is_staff)
def payment_config_list(request):
    destinations = PaymentDestination.objects.all().order_by('-is_active', 'name')

    if request.method == 'POST':
        PaymentDestination.objects.create(
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

@user_passes_test(lambda u: u.is_staff)
def toggle_payment_destination(request, pk):
    # Cambia el estado (Activo <-> Inactivo) rápidamente
    destination = get_object_or_404(PaymentDestination, pk=pk)
    destination.is_active = not destination.is_active
    destination.save()
    
    status = "activado" if destination.is_active else "desactivado"
    messages.info(request, f'El método "{destination.name}" ha sido {status}.')
    return redirect('payment_config_list')

@user_passes_test(lambda u: u.is_staff)
def edit_recipe(request, pk):
    product = get_object_or_404(Product, pk=pk)
    categories = Category.objects.all()
    ingredients = Ingredient.objects.all().order_by('name')

    if request.method == 'POST':
        # 1. Actualizar los datos base del Producto
        product.name = request.POST.get('name')
        product.category_id = request.POST.get('category')
        product.description = request.POST.get('description', '')
        product.sale_price = request.POST.get('sale_price')
        product.recipe_yield = request.POST.get('recipe_yield')
        product.is_available = request.POST.get('is_available') == 'on'
        product.track_stock = request.POST.get('track_stock') == 'on'
        product.stock_quantity = int(request.POST.get('stock_quantity', '0')) if product.track_stock else 0
        product.save()

        # 2. Estrategia "Wipe and Replace" para los ingredientes de la receta
        product.recipe_items.all().delete() # Limpiamos la receta anterior
        
        ingredient_ids = request.POST.getlist('ingredient_id[]')
        quantities = request.POST.getlist('quantity_required[]')
        
        # Insertamos la nueva receta
        for i in range(len(ingredient_ids)):
            if ingredient_ids[i] and quantities[i]:
                RecipeItem.objects.create(
                    product=product,
                    ingredient_id=ingredient_ids[i],
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

@user_passes_test(lambda u: u.is_staff)
def delete_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    
    if request.method == 'POST':
        try:
            product_name = product.name
            product.delete()
            messages.warning(request, f'El producto "{product_name}" ha sido eliminado del catálogo.')
            return redirect('product_list')
        except ProtectedError:
            # Si el producto ya tiene ventas, Django protegerá la base de datos
            messages.error(request, f'No se puede eliminar "{product.name}" porque ya existen ventas asociadas a este producto. Sugerencia: Edita el producto y márcalo como "Oculto/Inactivo".')
            return redirect('product_list')
            
    return render(request, 'core/product_confirm_delete.html', {'product': product})

@csrf_exempt
def telegram_webhook(request):
    if request.method == 'POST':
        try:
            update = json.loads(request.body.decode('utf-8'))
            TOKEN = settings.TELEGRAM_BOT_TOKEN
            
            # --- CASO A: EL USUARIO HIZO CLIC EN UN BOTÓN ---
            if 'callback_query' in update:
                callback = update['callback_query']
                chat_id = callback['message']['chat']['id']
                message_id = callback['message']['message_id']
                data = callback['data'] 
                original_text = callback['message'].get('caption', callback['message'].get('text', ''))
                
                action_short, payment_id = data.split('_')
                payment = Payment.objects.get(id=payment_id)
                action_full = 'approve' if action_short == 'app' else 'reject'
                
                success, result_message = process_payment_action(payment, action_full)
                
                if success and action_full == 'approve':
                    estado_emoji = "✅" 
                elif success and action_full == 'reject':
                    estado_emoji = "🗑️" 
                else:
                    estado_emoji = "❌" 
                    
                nuevo_estado = f"{estado_emoji} *{result_message}*"

                requests.get(f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery?callback_query_id={callback['id']}")
                nuevo_texto = f"{original_text}\n\n{nuevo_estado}"
                edit_url = f"https://api.telegram.org/bot{TOKEN}/editMessageCaption"
                requests.post(edit_url, json={'chat_id': chat_id, 'message_id': message_id, 'caption': nuevo_texto, 'parse_mode': 'Markdown'})
            
            # --- CASO B: EL USUARIO ESCRIBIÓ UN COMANDO DE TEXTO ---
            elif 'message' in update and 'text' in update['message']:
                chat_id = update['message']['chat']['id']
                texto_recibido = update['message']['text']
                
                # Candado de Seguridad (Opcional pero recomendado):
                # if str(chat_id) != '-100_TU_ID_DE_GRUPO': return JsonResponse({"status": "ok"})
                
                # Si el mensaje empieza con "/", llamamos a nuestro procesador
                if texto_recibido.startswith('/'):
                    respuesta_texto = process_telegram_command(texto_recibido)
                    
                    if respuesta_texto:
                        url_enviar = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                        requests.post(url_enviar, json={'chat_id': chat_id, 'text': respuesta_texto, 'parse_mode': 'Markdown'})

        except Exception as e:
            print(f"Webhook error: {e}")
            
        return JsonResponse({"status": "ok"})