import json
import os
import threading
import requests
from django.conf import settings

def send_telegram_receipt_async(payment_record, total_amount, is_bulk=False):
    
    tienda_user = payment_record.order.user
    
    try:
        CHAT_ID = tienda_user.store_settings.telegram_chat_id
        store_name = tienda_user.store_settings.store_name
        if not store_name:
            store_name = "CrumbCore" 
    except Exception as e:
        print(f"Aviso: La tienda de {tienda_user.username} no tiene configuraciones de Telegram.")
        return 
    if not CHAT_ID:
        print(f"Aviso: La tienda de {tienda_user.username} no ha ingresado su CHAT_ID.")
        return

    def send_message():
        TOKEN = settings.TELEGRAM_BOT_TOKEN
        
        customer_name = payment_record.order.customer.full_name if payment_record.order.customer else "Venta de Mostrador"
        metodo = payment_record.get_payment_method_display()
        ref = payment_record.reference_number
        
        caption = f"🚨 *NUEVO PAGO - {store_name.upper()}* 🚨\n\n"
        caption += f"👤 *Cliente:* {customer_name}\n"
        caption += f"💰 *Monto Total:* ${total_amount}\n"
        caption += f"💳 *Método:* {metodo}\n"
        caption += f"🧾 *Ref:* {ref}\n\n"
        
        if is_bulk and payment_record.transaction_group:
            Payment = payment_record.__class__
            
            pagos_asociados = Payment.objects.filter(transaction_group=payment_record.transaction_group)
            
            lista_ordenes = [str(p.order.id) for p in pagos_asociados]
            ordenes_str = ", #".join(lista_ordenes)
            if len(lista_ordenes) > 1:
                caption += f"📦 *Tipo:* Pago de Ordenes\n"
                caption += f"🔗 *Órdenes pagadas:* #{ordenes_str}\n"
            else:
                caption += f"📦 *Orden:* #{ordenes_str}\n"
        else:
            caption += f"📦 *Orden:* #{payment_record.order.id}\n"
            
        render_url = "https://crumbcore-app.onrender.com" 
        caption += f"\n👉 [Entrar al Panel de Verificación]({render_url}/orders/)"

        botones = {
            "inline_keyboard": [
                [
                    {"text": "✅ Aprobar", "callback_data": f"app_{payment_record.id}"},
                    {"text": "❌ Rechazar", "callback_data": f"rej_{payment_record.id}"}
                ]
            ]
        }
        reply_markup = json.dumps(botones)

        try:
            if payment_record.receipt:
                url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
                data = {
                    'chat_id': CHAT_ID, 
                    'photo': payment_record.receipt.url, 
                    'caption': caption, 
                    'parse_mode': 'Markdown', 
                    'reply_markup': reply_markup
                }
                response = requests.post(url, data=data, timeout=10)
            else:
                url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                data = {
                    'chat_id': CHAT_ID, 
                    'text': caption, 
                    'parse_mode': 'Markdown', 
                    'reply_markup': reply_markup
                }
                response = requests.post(url, data=data, timeout=10)
                
            # Validamos si Telegram rechazó la petición
            if response.status_code != 200:
                print(f"TELEGRAM ERROR LOG: {response.status_code} - {response.text}")
                
        except Exception as e:
            print(f"Error fatal enviando Telegram: {e}")

    threading.Thread(target=send_message).start()

def enviar_whatsapp_background(telefono_cliente, mensaje, store_uuid):
    def send_task():
        base_url = getattr(settings, 'WHATSAPP_API_URL', None)
        if not base_url:
            print("❌ Error: No se ha configurado WHATSAPP_API_URL en settings.py")
            return

        endpoint = f"{base_url}/send"
        
        # Limpiamos el teléfono (dejar solo números)
        telefono_limpio = ''.join(filter(str.isdigit, str(telefono_cliente)))
        
        # 🎯 NUEVO: Agregamos el store_id al payload
        payload = {
            "phone": telefono_limpio,
            "message": mensaje,
            "store_id": str(store_uuid) 
        }
        
        try:
            # Hacemos la petición POST al microservicio de Node.js
            response = requests.post(endpoint, json=payload, timeout=30)
            
            if response.status_code == 200:
                print(f"✅ WhatsApp enviado con éxito a {telefono_limpio}")
            else:
                print(f"❌ Error del microservicio ({response.status_code}): {response.text}")
                
        except requests.exceptions.Timeout:
            print("⚠️ El microservicio tardó demasiado en responder (Railway posiblemente hibernando).")
        except Exception as e:
            print(f"❌ Error de conexión con el microservicio de WhatsApp: {e}")

    # Lanzamos el hilo para no congelar la pantalla de carga del cliente
    threading.Thread(target=send_task).start()