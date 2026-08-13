/* ============================================================
   Alta manual de "Fabricación" — ver views_fabricacion.py
   Módulo TEMPORAL: solo para cargar grabados que ya están en stock
   y no pasaron por Plani. Sin datos técnicos de producción.
   ============================================================ */

(function () {
    'use strict';

    let registrosSesion = [];

    const ESTADOS = {
        PENDIENTE: 'Pendiente', EN_PROCESO: 'En Proceso', EN_MAQUINA: 'En Máquina',
        COMPLETADO: 'Completado', REVISION: 'En Revisión', REPETIR: 'Para Repetir', CANCELADO: 'Cancelado',
    };

    function getCookie(name) {
        const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    function limpiarFormulario() {
        ['fab-of', 'fab-maquina', 'fab-sobre', 'fab-referencia', 'fab-papel', 'fab-cliente', 'fab-ubicacion', 'fab-descripcion']
            .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
        document.getElementById('fab-proceso').value = 'STAMPING';
        document.getElementById('fab-estado').value = 'COMPLETADO';
        ocultarAvisos();
    }

    function ocultarAvisos() {
        document.getElementById('fab-aviso-ok').style.display = 'none';
        document.getElementById('fab-aviso-vacio').style.display = 'none';
    }

    window.abrirModalFabricacion = function () {
        limpiarFormulario();
        document.getElementById('modal-fabricacion').style.display = 'flex';
        document.getElementById('fab-of').focus();
    };

    window.cerrarModalFabricacion = function () {
        document.getElementById('modal-fabricacion').style.display = 'none';
    };

    window.buscarExterno = function () {
        const of = document.getElementById('fab-of').value.trim();
        const proceso = document.getElementById('fab-proceso').value;
        if (!of) { alert('Ingresá un número de OF primero.'); return; }

        ocultarAvisos();

        fetch(`/grabados/api/fabricacion/buscar-externo/?of=${encodeURIComponent(of)}&proceso=${encodeURIComponent(proceso)}`)
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'ok') { alert('Error: ' + res.message); return; }
                const d = res.data;
                if (d.encontrado_ext) {
                    document.getElementById('fab-maquina').value = d.maquina_ext !== '—' ? d.maquina_ext : '';
                    document.getElementById('fab-sobre').value = d.sobre_ext !== '—' ? d.sobre_ext : '';
                    document.getElementById('fab-referencia').value = d.ref_ext !== '—' ? d.ref_ext : '';
                    document.getElementById('fab-papel').value = d.papel_ext !== '—' ? d.papel_ext : '';
                    document.getElementById('fab-aviso-ok').style.display = 'block';
                } else {
                    document.getElementById('fab-aviso-vacio').style.display = 'block';
                }
            })
            .catch(() => alert('Error de conexión al buscar en el sistema externo.'));
    };

    function renderTabla() {
        const tbody = document.getElementById('tabla-cuerpo-fabricacion');
        if (!registrosSesion.length) {
            tbody.innerHTML = '<tr><td colspan="10" class="tabla-sin-resultados">Todavía no registraste nada en esta sesión.</td></tr>';
            return;
        }
        tbody.innerHTML = registrosSesion.map(r => `
            <tr>
                <td><strong>${r.of}</strong></td>
                <td>${r.proceso}</td>
                <td>${r.cliente || '—'}</td>
                <td>${r.descripcion || '—'}</td>
                <td>${r.maquina || '—'}</td>
                <td>${r.sobre || '—'}</td>
                <td>${r.referencia || '—'}</td>
                <td>${r.papel || '—'}</td>
                <td>${r.ubicacion || '—'}</td>
                <td>${ESTADOS[r.estado] || r.estado}</td>
            </tr>
        `).join('');
    }

    window.guardarFabricacion = function () {
        const of = document.getElementById('fab-of').value.trim();
        const proceso = document.getElementById('fab-proceso').value;
        if (!of) { alert('El número de OF es obligatorio.'); return; }

        const payload = {
            of: of,
            proceso: proceso,
            maquina: document.getElementById('fab-maquina').value.trim(),
            sobre: document.getElementById('fab-sobre').value.trim(),
            referencia: document.getElementById('fab-referencia').value.trim(),
            papel: document.getElementById('fab-papel').value.trim(),
            cliente: document.getElementById('fab-cliente').value.trim(),
            ubicacion: document.getElementById('fab-ubicacion').value.trim(),
            descripcion: document.getElementById('fab-descripcion').value.trim(),
            estado: document.getElementById('fab-estado').value,
        };

        const btn = document.getElementById('fab-btn-guardar');
        btn.disabled = true;

        fetch('/grabados/api/fabricacion/registrar/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify(payload),
        })
            .then(r => r.json())
            .then(res => {
                if (res.status === 'ok') {
                    registrosSesion.unshift(payload);
                    renderTabla();
                    cerrarModalFabricacion();
                } else {
                    alert('Error: ' + res.message);
                }
            })
            .catch(() => alert('Error de conexión al guardar.'))
            .finally(() => { btn.disabled = false; });
    };

    function cargarRegistrados() {
        fetch('/grabados/api/fabricacion/listar/')
            .then(r => r.json())
            .then(res => {
                if (res.status === 'ok') {
                    registrosSesion = res.data;
                    renderTabla();
                }
            })
            .catch(() => renderTabla());
    }

    cargarRegistrados();
})();
