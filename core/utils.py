import json
import threading
import requests
from django.conf import settings

def send_telegram_receipt_async(payment_record, total_amount, is_bulk=False):
    """
    Ejecuta la petición HTTP en un hilo separado para que el cliente 
    no tenga que esperar a que Telegram responda para ver su pantalla de éxito.
    """
    def send_message():
        TOKEN = settings.TELEGRAM_BOT_TOKEN
        CHAT_ID = settings.TELEGRAM_CHAT_ID
        
        customer_name = payment_record.order.customer.full_name if payment_record.order.customer else "Venta de Mostrador"
        metodo = payment_record.get_payment_method_display()
        ref = payment_record.reference_number
        
        # Armamos el mensaje con formato Markdown
        caption = f"🚨 *NUEVO PAGO REPORTADO* 🚨\n\n"
        caption += f"👤 *Cliente:* {customer_name}\n"
        caption += f"💰 *Monto Total:* ${total_amount}\n"
        caption += f"💳 *Método:* {metodo}\n"
        caption += f"🧾 *Ref:* {ref}\n\n"
        
        if is_bulk:
            caption += f"📦 *Tipo:* Pago\n"
            caption += f"🔗 *Token de Grupo:* `{str(payment_record.transaction_group)[:8]}`\n"
        else:
            caption += f"📦 *Orden:* #{payment_record.order.id}\n"
            
        # ⚠️ CAMBIA ESTO por tu dominio real de Render
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
                # OPTIMIZACIÓN S3: Mandamos la URL en lugar del archivo físico
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

    # Lanzamos un ÚNICO hilo
    threading.Thread(target=send_message).start()