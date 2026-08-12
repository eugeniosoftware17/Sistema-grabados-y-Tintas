from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django_otp import login as otp_login, match_token
from django_otp.plugins.otp_totp.models import TOTPDevice

@login_required
def dashboard(request):
    return render(request, 'dashboard.html')


@login_required
def verificar_otp(request):
    if request.user.is_verified():
        return redirect('core:dashboard')

    if not TOTPDevice.objects.filter(user=request.user, confirmed=True).exists():
        # El usuario no tiene 2FA configurado, no debería haber llegado acá.
        return redirect('core:dashboard')

    if request.method == 'POST':
        token = request.POST.get('token', '').strip()
        device = match_token(request.user, token)
        if device:
            otp_login(request, device)
            return redirect('core:dashboard')
        messages.error(request, 'Código incorrecto. Probá de nuevo.')

    return render(request, 'registration/verificar_otp.html')
