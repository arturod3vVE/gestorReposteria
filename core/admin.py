from django.contrib import admin
from .models import (
    Category, Product, Ingredient, RecipeItem, 
    Customer, Order, OrderItem, Payment, 
    ExchangeRate, PaymentDestination
)

# --- CONFIGURACIÓN DE INVENTARIO Y RECETAS ---

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

class RecipeItemInline(admin.TabularInline):
    model = RecipeItem
    extra = 0

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'sale_price', 'recipe_yield', 'is_available', 'track_stock', 'stock_quantity')
    list_filter = ('category', 'is_available', 'track_stock')
    search_fields = ('name',)
    inlines = [RecipeItemInline]

@admin.register(Ingredient)
class IngredientAdmin(admin.ModelAdmin):
    list_display = ('name', 'measurement_unit', 'cost_per_unit', 'track_stock', 'stock_quantity')
    list_filter = ('measurement_unit', 'track_stock')
    search_fields = ('name',)


# --- CONFIGURACIÓN DE VENTAS Y ÓRDENES ---

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'phone', 'email')
    search_fields = ('full_name', 'phone', 'email')

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer', 'status', 'payment_status', 'total_amount', 'created_at')
    list_filter = ('status', 'payment_status', 'created_at')
    search_fields = ('id', 'customer__full_name', 'customer__phone')
    readonly_fields = ('created_at',)
    inlines = [OrderItemInline]

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'payment_method', 'amount', 'is_verified', 'reported_at')
    list_filter = ('is_verified', 'payment_method', 'reported_at')
    search_fields = ('order__id', 'reference_number')


# --- CONFIGURACIÓN FINANCIERA ---

@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ('rate', 'created_at')
    readonly_fields = ('created_at',)

@admin.register(PaymentDestination)
class PaymentDestinationAdmin(admin.ModelAdmin):
    list_display = ('name', 'destination_type', 'is_active')
    list_filter = ('destination_type', 'is_active')
    search_fields = ('name', 'bank', 'phone', 'email')