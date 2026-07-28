document.addEventListener('DOMContentLoaded', function() {

    // Referencias DOM
    const loginForm = document.getElementById('loginForm');
    const passwordToggle = document.getElementById('passwordToggle');
    const passwordInput = document.getElementById('id_password'); // Changed from #password
    const usernameInput = document.getElementById('id_username'); // Changed from #username
    const messageContainer = document.getElementById('messageContainer');

    // Toggle mostrar/ocultar contraseña
    if (passwordToggle) {
        passwordToggle.addEventListener('click', function() {
            const type = passwordInput.type === 'password' ? 'text' : 'password';
            passwordInput.type = type;
            this.textContent = type === 'password' ? '👁️' : '🙈';
        });
    }

    // Funciones para los enlaces del footer
    window.forgotPassword = function() {
        alert('Por motivos de seguridad, solo el Administrador del Sistema puede restablecer las contraseñas.\n\nComunícate con el departamento de TI para solicitar el restablecimiento de tu acceso.');
    }

    window.showHelp = function() {
        alert('¿Necesitas ayuda?\n\n Si tienes alguna duda o problema al utilizar el sistema, comunícate con el administrador o el departamento de TI para recibir asistencia. ');
    }

    window.showPrivacy = function() {
        alert('Política de Privacidad \n\nTu información personal es tratada de forma segura y utilizada únicamente para el funcionamiento del sistema, de acuerdo con nuestras políticas de privacidad.');
    }

    window.showTerms = function() {
        alert('Términos y Condiciones\n\nAl utilizar este sistema, aceptas cumplir con las normas de uso establecidas por Cigar Rings. El uso indebido puede resultar en la suspensión del acceso.');
    }

    // Auto-focus en el campo de usuario al cargar la página
    if (usernameInput) {
        usernameInput.focus();
    }

    // Manejo de Enter en los inputs
    document.querySelectorAll('input').forEach(input => {
        input.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                if (this.id === 'id_username') {
                    if(passwordInput) passwordInput.focus();
                } else if (this.id === 'id_password') {
                    if(loginForm) loginForm.submit();
                }
            }
        });
    });

    // Limpiar mensajes de error de Django al hacer focus en inputs
    document.querySelectorAll('input').forEach(input => {
        input.addEventListener('focus', function() {
            if (messageContainer) {
                messageContainer.innerHTML = '';
            }
        });
    });

    // Animación de entrada
    const loginContainer = document.querySelector('.login-container');
    if (loginContainer) {
        loginContainer.style.opacity = '0';
        loginContainer.style.transform = 'translateY(50px) scale(0.9)';
        
        setTimeout(() => {
            loginContainer.style.transition = 'all 0.8s cubic-bezier(0.4, 0, 0.2, 1)';
            loginContainer.style.opacity = '1';
            loginContainer.style.transform = 'translateY(0) scale(1)';
        }, 100);
    }
});
