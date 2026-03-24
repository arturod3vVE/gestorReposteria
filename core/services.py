from asyncio.log import logger
from .models import Payment, Order
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
import requests
from django.conf import settings

def process_payment_action(payment, action):
    """
    Core business logic for approving or rejecting a payment.
    Handles bulk transactions (domino effect) and individual payments.
    Returns: (bool success, str result_message)
    """
    if action == 'approve':
        if payment.transaction_group:
            related_payments = Payment.objects.filter(transaction_group=payment.transaction_group, is_verified=False)
            approved_count = 0
            
            for related_payment in related_payments:
                if related_payment.amount <= related_payment.order.balance_due_calculated:
                    related_payment.is_verified = True
                    related_payment.save()
                    
                    related_order = related_payment.order
                    if related_order.amount_paid >= related_order.total_calculated:
                        related_order.payment_status = 'PAID'
                    elif related_order.amount_paid > 0:
                        related_order.payment_status = 'PARTIAL'
                    related_order.save()
                    
                    # Notificamos el pago aprobado
                    send_whatsapp_payment_notification(related_order, related_payment.amount, status='approved')
                    
                    approved_count += 1
            return True, f'¡Efecto dominó! Se verificaron {approved_count} pagos asociados a esta liquidación masiva.'
            
        else:
            if payment.amount > payment.order.balance_due_calculated:
                return False, f'¡Error! No puedes aprobar un pago de ${payment.amount} porque supera el saldo pendiente (${payment.order.balance_due_calculated}).'
            else:
                payment.is_verified = True
                payment.save()
                
                order = payment.order
                if order.amount_paid >= order.total_calculated:
                    order.payment_status = 'PAID'
                elif order.amount_paid > 0:
                    order.payment_status = 'PARTIAL'
                order.save()
                
                send_whatsapp_payment_notification(order, payment.amount, status='approved')
                
                return True, f'Pago de ${payment.amount} verificado correctamente.'
                
    elif action == 'reject':
        if payment.transaction_group:
            related_payments = Payment.objects.filter(transaction_group=payment.transaction_group)
            deleted_count = related_payments.count()
            
            # Notificamos a cada cliente antes de borrar los registros masivos
            for related_payment in related_payments:
                send_whatsapp_payment_notification(related_payment.order, related_payment.amount, status='rejected')
                
            related_payments.delete()
            return True, f'El reporte de pago masivo ha sido rechazado.'
        else:
            rejected_amount = payment.amount
            order = payment.order 
            
            payment.delete()
            
            send_whatsapp_payment_notification(order, rejected_amount, status='rejected')
            
            return True, f'El reporte de pago por ${rejected_amount} ha sido rechazado y eliminado.'
            
    return False, 'Acción no reconocida.'

def send_whatsapp_payment_notification(order, amount, status='approved'):
    """
    Envía una notificación al cliente sobre el estado de su pago (aprobado o rechazado).
    """
    if not order.customer or not order.customer.phone:
        return  # Si no hay cliente o teléfono, salimos en silencio

    phone = order.customer.phone
    name = order.customer.full_name or "Cliente"
    
    if status == 'approved':
        message = f"¡Hola {name}! 🍪\n\n"
        message += f"Tu pago de *${amount}* para la orden *#{order.id}* fue aprobado.\n\n"
        
        if order.payment_status == 'PAID':
            message += "✅ Tu pedido ya está 100% pagado. ¡Gracias por preferirnos!"
        else:
            message += f"📝 Saldo restante por pagar: *${order.balance_due_calculated}*."
            
    elif status == 'rejected':
        message = f"¡Hola {name}! 🍪\n\n"
        message += f"❌ Tuvimos un inconveniente al verificar tu pago de *${amount}* para la orden *#{order.id}* y ha sido rechazado.\n\n"
        message += "Por favor, revisa el comprobante y vuelve a registrarlo. Si tienes alguna duda, escríbenos por esta vía."

    try:
        api_url = f"{settings.WHATSAPP_API_URL}/send"
        payload = {
            "phone": phone,
            "message": message
        }
        
        requests.post(api_url, json=payload, timeout=3)
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Fallo al notificar por WhatsApp a {phone}: {e}")

def process_telegram_command(command_text, chat_id):
    partes = command_text.strip().split()
    if not partes:
        return None
        
    comando = partes[0].lower()
    hoy = timezone.now().date()
    
    # --- 1. IDENTIFICAR LA TIENDA (MULTI-TENANT) ---
    try:
        # Buscamos a qué tienda le pertenece este chat de Telegram
        config = StoreSettings.objects.get(telegram_chat_id=str(chat_id))
        tienda_user = config.user
        store_name = config.store_name or "CrumbCore"
    except StoreSettings.DoesNotExist:
        # Si alguien que no está registrado le escribe un comando al bot
        return "❌ *Acceso Denegado:*\nNo tienes ninguna tienda vinculada a este chat. Ingresa a tu panel de CrumbCore y configura tu Chat ID."

    # --- COMANDO: /METRICAS ---
    if comando.startswith('/metricas'):
        # 🎯 FILTRAMOS POR tienda_user
        ventas_hoy = Order.objects.filter(user=tienda_user, created_at__date=hoy).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        pagos_hoy = Payment.objects.filter(user=tienda_user, is_verified=True, reported_at__date=hoy).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        return (
            f"🧁 *REPORTE DIARIO - {store_name.upper()}*\n"
            f"📅 Fecha: {hoy.strftime('%d/%m/%Y')}\n"
            f"----------------------------------\n"
            f"📈 *Ventas Brutas:* ${ventas_hoy}\n"
            f"💵 *Cobranza Verificada:* ${pagos_hoy}\n"
            f"----------------------------------"
        )

    # --- COMANDO: /DEUDORES ---
    elif comando.startswith('/deudores'):
        # 🎯 FILTRAMOS POR tienda_user
        ordenes_pendientes = Order.objects.filter(user=tienda_user).exclude(payment_status='PAID').select_related('customer')
        deudores = []
        for order in ordenes_pendientes:
            saldo = order.balance_due_calculated
            if saldo > 0:
                cliente = order.customer.full_name if order.customer else f"Orden #{order.id}"
                deudores.append((cliente, order.id, saldo))
        
        if not deudores:
            return f"✅ *{store_name}:* Todas las cuentas están al día."
            
        deudores = sorted(deudores, key=lambda x: x[2], reverse=True)[:5]
        respuesta = f"⚠️ *DEUDORES - {store_name.upper()}*\n"
        for d in deudores:
            respuesta += f"👤 {d[0]} | 🆔 #{d[1]} ➔ *${d[2]}*\n"
        return respuesta

    # --- COMANDO: /BUSCAR_ORDEN ---
    elif comando.startswith('/buscar_orden'):
        if len(partes) < 2:
            return "❌ *Error:* Indica el ID de la orden. Ej: `/buscar_orden 15`"
        
        try:
            order_id = partes[1]
            # 🎯 FILTRAMOS POR tienda_user PARA QUE NO ESPÍE ÓRDENES AJENAS
            order = Order.objects.prefetch_related('items__product').get(id=order_id, user=tienda_user)
            
            status_map = {'PENDING': '⏳ Pendiente', 'PREPARING': '👨‍🍳 En Cocina', 'DELIVERED': '✅ Entregado', 'CANCELLED': '🚫 Cancelado'}
            
            items_resumen = ""
            for item in order.items.all():
                items_resumen += f"• {item.quantity}x {item.product.name}\n"

            return (
                f"📑 *DETALLE DE ORDEN #{order.id}*\n"
                f"🏪 *Tienda:* {store_name}\n"
                f"----------------------------------\n"
                f"👤 *Cliente:* {order.customer.full_name if order.customer else 'N/A'}\n"
                f"📍 *Status:* {status_map.get(order.status, order.status)}\n"
                f"💰 *Total:* ${order.total_amount}\n"
                f"🔴 *Por pagar:* ${order.balance_due_calculated}\n\n"
                f"📦 *Productos:*\n{items_resumen}"
            )
        except Order.DoesNotExist:
            return f"❓ No tienes ninguna orden registrada con el ID #{partes[1]}."

    return None