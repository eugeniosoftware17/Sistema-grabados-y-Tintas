import base64

from django.contrib import admin, messages
from django_otp.plugins.otp_totp.models import TOTPDevice

# otp_totp ya trae su propio ModelAdmin registrado (con QR); lo reemplazamos
# por uno que muestra la clave en texto, ya que acá se configura a mano.
admin.site.unregister(TOTPDevice)


@admin.register(TOTPDevice)
class TOTPDeviceAdmin(admin.ModelAdmin):
    """
    Alta manual de 2FA por usuario. No usa QR: al guardar un dispositivo
    nuevo, la clave secreta (Base32) se muestra una única vez como mensaje
    para copiarla a mano en 1Password ("clave de configuración manual").
    """
    list_display = ('user', 'name', 'confirmed')
    list_filter = ('confirmed',)
    search_fields = ('user__username', 'name')
    fields = ('user', 'name', 'confirmed')

    def save_model(self, request, obj, form, change):
        es_nuevo = obj.pk is None
        if not obj.name:
            obj.name = f"{obj.user.username}-totp"

        super().save_model(request, obj, form, change)

        if es_nuevo:
            clave = base64.b32encode(obj.bin_key).decode('utf-8').rstrip('=')
            messages.info(
                request,
                f"Clave secreta para '{obj.user.username}' (Base32, para pegar en 1Password "
                f"como clave de configuración manual): {clave} — no se vuelve a mostrar."
            )
