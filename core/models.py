from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal
from django.core.exceptions import ValidationError
from io import BytesIO
from PIL import Image
import uuid
from django.core.files.uploadedfile import InMemoryUploadedFile
import sys

class ExchangeRate(models.Model):
    rate = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.0001'))])
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.rate} - {self.created_at.strftime('%d/%m/%Y %H:%M')}"

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Categories"

class Ingredient(models.Model):
    MEASUREMENT_UNITS = [
        ('g', 'Gramos'),
        ('kg', 'Kilogramos'),
        ('ml', 'Mililitros'),
        ('l', 'Litros'),
        ('unit', 'Unidad/Pieza'),
    ]

    name = models.CharField(max_length=150, unique=True)
    measurement_unit = models.CharField(max_length=10, choices=MEASUREMENT_UNITS, default='g')
    # Cost per single unit of measure (e.g., cost per 1 gram or 1 ml)
    cost_per_unit = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    track_stock = models.BooleanField(default=False)
    stock_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.name} ({self.measurement_unit})"

class Product(models.Model):
    category = models.ForeignKey('Category', on_delete=models.PROTECT, related_name='products')
    name = models.CharField(max_length=200)
    description = models.TextField()
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    recipe_yield = models.PositiveIntegerField(default=1, help_text="Cantidad de unidades que salen de esta receta")
    track_stock = models.BooleanField(default=False, help_text="Track physical inventory for this finished product")
    stock_quantity = models.IntegerField(default=0, help_text="Current available units for immediate sale")

    def __str__(self):
        return self.name

    # 1. Costo total de fabricar la receta completa (Reemplaza a tu antiguo recipe_cost)
    @property
    def batch_cost(self):
        total = sum(item.ingredient_cost() for item in self.recipe_items.all())
        return round(Decimal(total), 2)

    # 2. Costo individual de cada unidad producida
    @property
    def unit_cost(self):
        if self.recipe_yield > 0:
            return round(self.batch_cost / Decimal(self.recipe_yield), 2)
        return Decimal('0.00')

    # 3. Ganancia neta por cada unidad vendida
    @property
    def unit_profit(self):
        return round(self.sale_price - self.unit_cost, 2)

    # 4. Ganancia total si vendes todas las unidades de la receta
    @property
    def batch_profit(self):
        return round(self.unit_profit * Decimal(self.recipe_yield), 2)

class RecipeItem(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='recipe_items')
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT)
    quantity_required = models.DecimalField(max_digits=8, decimal_places=2, help_text="Quantity in the ingredient's measurement unit")

    def __str__(self):
        return f"{self.quantity_required} {self.ingredient.measurement_unit} of {self.ingredient.name} for {self.product.name}"

    def ingredient_cost(self):
        return self.quantity_required * self.ingredient.cost_per_unit

class Customer(models.Model):
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(max_length=255, blank=True, null=True)
    delivery_address = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.full_name

class Order(models.Model):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_status = self.status
        self._original_payment_status = self.payment_status

    ORDER_STATUS = [
        ('PENDING', 'Pending'),
        ('PREPARING', 'In Preparation'),
        ('DELIVERED', 'Delivered'),
        ('CANCELLED', 'Cancelled'),
    ]

    PAYMENT_STATUS = [
        ('PENDING', 'Pending'),
        ('PARTIAL', 'Partially Paid'),
        ('PAID', 'Fully Paid'),
        ('REFUNDED', 'Refunded'),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, related_name='orders')
    created_at = models.DateTimeField(auto_now_add=True)
    expected_delivery_date = models.DateTimeField()
    status = models.CharField(max_length=15, choices=ORDER_STATUS, default='PENDING')
    payment_status = models.CharField(max_length=15, choices=PAYMENT_STATUS, default='PENDING')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    special_notes = models.TextField(blank=True)

    def __str__(self):
        return f"Order #{self.id} - {self.customer}"

    @property
    def total_calculated(self):
        # We sum the subtotal property of each related OrderItem
        total = sum(item.subtotal for item in self.items.all())
        return Decimal(total).quantize(Decimal('0.01'))

    @property
    def amount_paid(self):
        return sum(payment.amount for payment in self.payments.all() if payment.is_verified)

    @property
    def amount_pending(self):
        return sum(payment.amount for payment in self.payments.all() if not payment.is_verified)

    @property
    def balance_due_calculated(self):
        return self.total_calculated - Decimal(self.amount_paid)

    def clean(self):
        super().clean()

        # Solo validamos transiciones si la orden ya existe en la base de datos
        if self.pk:
            # REGLA A: Intentar CANCELAR una orden
            if self.status == 'CANCELLED' and self._original_status != 'CANCELLED':
                if self._original_status == 'DELIVERED':
                    raise ValidationError("Violación de integridad: No se puede cancelar una orden que ya fue entregada.")
                if self.amount_paid > 0 or self._original_payment_status != 'PENDING':
                    raise ValidationError("Violación de integridad: No se puede cancelar una orden que ya tiene dinero abonado. Procesa un reembolso primero.")

            # REGLA B: Intentar modificar una orden YA CANCELADA (Órdenes muertas)
            if self._original_status == 'CANCELLED':
                if self.status != 'CANCELLED':
                    raise ValidationError("Violación de integridad: Las órdenes canceladas no pueden ser reactivadas o entregadas.")
                if self.payment_status != self._original_payment_status:
                    raise ValidationError("Violación de integridad: No se pueden registrar pagos ni alterar el estado financiero de una orden cancelada.")

            # REGLA C: Entregas
            if self.status == 'DELIVERED' and self._original_status == 'PENDING':
                # Opcional: Podrías forzar a que pasen por PREPARING, pero en mostrador es normal pasar directo a DELIVERED.
                pass

    # 3. Forzamos la validación antes de cada guardado en la base de datos
    def save(self, *args, **kwargs):
        self.clean() # Llama a nuestras reglas estrictas
        super().save(*args, **kwargs)

        # Actualizamos la memoria del estado original tras un guardado exitoso
        self._original_status = self.status
        self._original_payment_status = self.payment_status

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"

    @property
    def subtotal(self):
        # Logic is centralized here to avoid repetition
        return Decimal(self.quantity * self.unit_price).quantize(Decimal('0.01'))

    def save(self, *args, **kwargs):
        # 1. Verificamos si es un ítem NUEVO (aún no tiene ID en la base de datos)
        if self.pk is None:
            if self.product.track_stock:
                # Descontamos el stock de la vitrina
                self.product.stock_quantity -= self.quantity
                self.product.save()
        else:
            # 2. Si estamos EDITANDO un ítem (ej. el cliente cambió de 2 a 5 galletas)
            old_item = OrderItem.objects.get(pk=self.pk)
            diferencia = self.quantity - old_item.quantity

            if self.product.track_stock and diferencia != 0:
                self.product.stock_quantity -= diferencia
                self.product.save()

        # Finalmente, guardamos el OrderItem normalmente
        super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        # 3. Si eliminamos un producto de la orden, devolvemos el stock a la vitrina
        if self.product.track_stock:
            self.product.stock_quantity += self.quantity
            self.product.save()

        super().delete(*args, **kwargs)

class PaymentDestination(models.Model):
    DESTINATION_TYPES = [
        ('MOBILE', 'Pago Móvil'),
        ('TRANSFER', 'Transferencia Bancaria'),
        ('ZELLE', 'Zelle / Dólares Digitales'),
        ('CASH', 'Efectivo (Punto de Entrega)'),
    ]

    DOCUMENT_TYPES = [
        ('V', 'Venezolano (V)'),
        ('E', 'Extranjero (E)'),
        ('J', 'Jurídico (J)'),
        ('P', 'Pasaporte (P)'),
        ('G', 'Gubernamental (G)'),
    ]

    name = models.CharField(max_length=100, help_text="e.g., Pago Móvil Banesco Arturo")
    destination_type = models.CharField(max_length=15, choices=DESTINATION_TYPES)

    # Campos Específicos (Nulables para que se adapten al tipo de pago)
    bank = models.CharField(max_length=100, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    document_type = models.CharField(max_length=1, choices=DOCUMENT_TYPES, default='V', blank=True, null=True)
    document_number = models.CharField(max_length=20, blank=True, null=True)
    account_number = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    owner_name = models.CharField(max_length=100, blank=True, null=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.get_destination_type_display()})"

    @property
    def formatted_details(self):
        """Genera un resumen limpio dependiendo del tipo de pago"""
        if self.destination_type == 'MOBILE':
            return f"Banco: {self.bank}\nTel: {self.phone}\nDoc: {self.document_type}-{self.document_number}"
        elif self.destination_type == 'TRANSFER':
            return f"Banco: {self.bank}\nCuenta: {self.account_number}\nDoc: {self.document_type}-{self.document_number}"
        elif self.destination_type == 'ZELLE':
            return f"Zelle: {self.email}\nTitular: {self.owner_name}"
        return "Pago presencial en divisas o moneda local."

class Payment(models.Model):
    PAYMENT_METHODS = [
        ('CASH', 'Cash'),
        ('TRANSFER', 'Bank Transfer'),
        ('CARD', 'Card / POS'),
        ('MOBILE', 'Mobile Payment'),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    payment_method = models.CharField(max_length=15, choices=PAYMENT_METHODS)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    reference_number = models.CharField(max_length=100, blank=True, null=True, help_text="Bank transfer or receipt reference number")
    receipt = models.ImageField(upload_to='receipts/', blank=True, null=True)
    is_verified = models.BooleanField(default=False, help_text="Check when the payment is confirmed in the bank/register")
    reported_at = models.DateTimeField(auto_now_add=True)
    transaction_group = models.UUIDField(null=True, blank=True, editable=False)
    destination = models.ForeignKey(PaymentDestination, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')

    def clean(self):
        super().clean()
        if self.order and self.order.status == 'CANCELLED':
            raise ValidationError("No se pueden registrar ni verificar pagos en una orden que ha sido cancelada.")

    def save(self, *args, **kwargs):
        self.clean()

        # ¡TRUCO PRO! Solo comprimimos si hay recibo Y si NO ha sido comprimido antes.
        # Esto evita descargas innecesarias desde Supabase cada vez que editas el pago.
        if self.receipt and '_compressed' not in self.receipt.name:
            img = Image.open(self.receipt)

            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            img.thumbnail((800, 800), Image.Resampling.LANCZOS)

            output = BytesIO()
            img.save(output, format='JPEG', quality=60, optimize=True)
            output.seek(0)
            file_name = self.receipt.name.split('.')[0] + '_compressed.jpg'
            self.receipt = InMemoryUploadedFile(
                output,
                'ImageField',
                file_name,
                'image/jpeg',
                sys.getsizeof(output),
                None
            )
        super().save(*args, **kwargs)
