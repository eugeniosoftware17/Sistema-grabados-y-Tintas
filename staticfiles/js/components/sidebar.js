let sidebarOpen = false;

function toggleSidebar() {
    const sidebar   = document.querySelector('.sb-nav');
    const overlay   = document.querySelector('.sb-overlay');
    const hamburger = document.querySelector('.sb-hamburger');

    sidebarOpen = !sidebarOpen;

    sidebar.classList.toggle('open', sidebarOpen);
    overlay.classList.toggle('active', sidebarOpen);
    hamburger.classList.toggle('active', sidebarOpen);
}

function toggleSubmenu(element) {
    const submenu = element.nextElementSibling;
    const wasOpen = submenu.style.display === 'block';

    // Cerrar todos los submenús
    document.querySelectorAll('.sb-submenu').forEach(sub => {
        if (sub !== submenu) {
            sub.style.display = 'none';
            sub.previousElementSibling.classList.remove('submenu-open');
        }
    });

    // Abrir o cerrar el clickeado
    submenu.style.display = wasOpen ? 'none' : 'block';
    element.classList.toggle('submenu-open', !wasOpen);
}

function refreshPage() {
    const refreshBtn = document.querySelector('.sb-btn--refresh');
    if (refreshBtn) refreshBtn.classList.add('spinning');

    const url = new URL(window.location);
    url.searchParams.set('reloaded', 'true');
    window.location.href = url.href;
}

// Cerrar sidebar al hacer click fuera
document.addEventListener('click', function(event) {
    const sidebar   = document.querySelector('.sb-nav');
    const hamburger = document.querySelector('.sb-hamburger');

    if (sidebarOpen && !sidebar.contains(event.target) && !hamburger.contains(event.target)) {
        toggleSidebar();
    }
});

document.addEventListener('DOMContentLoaded', function() {
    // Abrir submenú si hay un item activo al cargar
    const activeSubitem = document.querySelector('.sb-subitem--active');
    if (activeSubitem) {
        const submenu = activeSubitem.closest('.sb-submenu');
        if (submenu) {
            submenu.style.display = 'block';
            submenu.previousElementSibling.classList.add('submenu-open');
        }
    }

    // Notificación de refresh
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.has('reloaded')) {
        const notification = document.getElementById('refresh-notification');
        if (notification) {
            notification.textContent = 'Página actualizada correctamente.';
            notification.classList.add('show');
            setTimeout(() => notification.classList.remove('show'), 3000);

            const url = new URL(window.location);
            url.searchParams.delete('reloaded');
            history.replaceState(null, '', url.href);
        }
    }
});