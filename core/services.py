from .models import Payment, Order
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone

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
                return True, f'Pago de ${payment.amount} verificado correctamente.'
                
    elif action == 'reject':
        if payment.transaction_group:
            related_payments = Payment.objects.filter(transaction_group=payment.transaction_group)
            deleted_count = related_payments.count()
            related_payments.delete()
            return True, f'El reporte de liquidación masiva ha sido rechazado. Se eliminaron {deleted_count} registros asociados.'
        else:
            rejected_amount = payment.amount
            payment.delete()
            return True, f'El reporte de pago por ${rejected_amount} ha sido rechazado y eliminado.'
            
    return False, 'Acción no reconocida.'

def process_telegram_command(command_text):
    """
    Procesador de comandos para la administración de CrumbCore.
    """
    partes = command_text.strip().split()
    if not partes:
        return None
        
    comando = partes[0].lower()
    hoy = timezone.now().date()
    
    # --- COMANDO: /METRICAS ---
    if comando.startswith('/metricas'):
        ventas_hoy = Order.objects.filter(created_at__date=hoy).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        pagos_hoy = Payment.objects.filter(is_verified=True, reported_at__date=hoy).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        return (
            f"🧁 *CRUMBCORE: REPORTE DIARIO* ({hoy.strftime('%d/%m/%Y')})\n"
            f"----------------------------------\n"
            f"📈 *Ventas Brutas:* ${ventas_hoy}\n"
            f"💵 *Cobranza Verificada:* ${pagos_hoy}\n"
            f"----------------------------------"
        )

    # --- COMANDO: /DEUDORES ---
    elif comando.startswith('/deudores'):
        ordenes_pendientes = Order.objects.exclude(payment_status='PAID').select_related('customer')
        deudores = []
        for order in ordenes_pendientes:
            saldo = order.balance_due_calculated
            if saldo > 0:
                cliente = order.customer.full_name if order.customer else f"Orden #{order.id}"
                deudores.append((cliente, order.id, saldo))
        
        if not deudores:
            return "✅ *CrumbCore:* Todas las cuentas están al día."
            
        deudores = sorted(deudores, key=lambda x: x[2], reverse=True)[:5]
        respuesta = "⚠️ *CLIENTES CON SALDO PENDIENTE*\n"
        for d in deudores:
            respuesta += f"👤 {d[0]} | 🆔 #{d[1]} ➔ *${d[2]}*\n"
        return respuesta

    # --- COMANDO: /BUSCAR_ORDEN ---
    elif comando.startswith('/buscar_orden'):
        if len(partes) < 2:
            return "❌ *Error:* Indica el ID de la orden. Ej: `/buscar_orden 15`"
        
        try:
            order_id = partes[1]
            order = Order.objects.prefetch_related('items__product').get(id=order_id)
            
            status_map = {'PENDING': '⏳ Pendiente', 'PREPARING': '👨‍🍳 En Cocina', 'READY': '📦 Listo', 'DELIVERED': '✅ Entregado', 'CANCELLED': '🚫 Cancelado'}
            
            items_resumen = ""
            for item in order.items.all():
                items_resumen += f"• {item.quantity}x {item.product.name}\n"

            return (
                f"📑 *DETALLE DE ORDEN #{order.id}*\n"
                f"----------------------------------\n"
                f"👤 *Cliente:* {order.customer.full_name if order.customer else 'N/A'}\n"
                f"📍 *Status:* {status_map.get(order.status, order.status)}\n"
                f"💰 *Total:* ${order.total_amount}\n"
                f"🔴 *Por pagar:* ${order.balance_due_calculated}\n\n"
                f"📦 *Productos:*\n{items_resumen}"
            )
        except:
            return f"❓ No existe la orden #{partes[1]}."

    return None