from django.shortcuts import redirect
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice


class RequireOTPMiddleware:
    """
    Si el usuario tiene un dispositivo TOTP confirmado (creado a mano por un
    admin desde /crdadmin/) y todavía no verificó el código en esta sesión,
    lo manda a la pantalla de verificación antes de dejarlo pasar a cualquier
    otra vista, incluido el propio panel de administración.

    Los usuarios sin dispositivo TOTP (el caso por defecto) no ven ningún
    paso extra: siguen entrando solo con usuario y contraseña.
    """

    EXEMPT_PATHS = {'/static/', '/media/'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)

        if (
            user
            and user.is_authenticated
            and not user.is_verified()
            and not any(request.path.startswith(p) for p in self.EXEMPT_PATHS)
            and request.path not in (reverse('core:verificar_otp'), reverse('core:logout'))
            and TOTPDevice.objects.filter(user=user, confirmed=True).exists()
        ):
            return redirect('core:verificar_otp')

        return self.get_response(request)
