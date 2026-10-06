/* ============================================================
   Alta de Grabado — ver views_grabados.py y services/grabados.py
   El servidor decide el caso (alta nueva / refabricación por K1
   rechazado / refabricación por REPETIR); acá solo se muestra.
   ============================================================ */

(function () {
    'use strict';

    let busqueda = null;               // última respuesta de api_alta_buscar
    let procesoInicial = null;         // ?proceso= de la URL (desde el PLANI), solo en la primera búsqueda
    let compensacionRecomendada = 0;

    const $ = (id) => document.getElementById(id);

    function getCookie(name) {
        const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    function mostrarAviso(id, clase, texto) {
        const el = $(id);
        el.className = 'aviso ' + clase;
        el.textContent = texto;
        el.style.display = 'block';
    }

    function ocultar(id) { $(id).style.display = 'none'; }

    function limpiarTecnicos() {
        ['alta-responsables', 'alta-tiempo', 'alta-peso-i', 'alta-peso-f', 'alta-temp', 'alta-rpm',
         'alta-compensacion', 'alta-compensacion-motivo', 'alta-maquina']
            .forEach(id => { $(id).value = ''; });
        compensacionRecomendada = 0;
        $('alta-perdida-msg').innerText = '0 g';
        $('alta-bano-msg').innerText = '0 ml';
        ocultar('alta-compensacion-motivo-caja');
    }

    function ocultarAccion() {
        ['alta-aviso-accion', 'alta-btn-cargar-origen', 'alta-form-tecnico', 'alta-btn-guardar']
            .forEach(ocultar);
    }

    window.limpiarAlta = function () {
        busqueda = null;
        $('alta-of').value = '';
        $('alta-proceso').value = '';
        $('alta-proceso').disabled = true;
        ['alta-cliente', 'alta-referencia', 'alta-sobre'].forEach(id => { $(id).value = ''; });
        ocultar('alta-aviso-externo');
        ocultar('alta-datos-grabado');
        ocultarAccion();
        limpiarTecnicos();
        $('alta-of').focus();
    };

    // ---------------------------------------------------------- búsqueda
    function buscarOF() {
        const of = $('alta-of').value.trim();
        if (!of) { alert('Ingresa un número de OF.'); return; }

        ocultar('alta-aviso-externo');
        ocultar('alta-datos-grabado');
        ocultarAccion();
        limpiarTecnicos();
        mostrarAviso('alta-aviso-externo', 'aviso--info', 'Buscando datos de la OF...');

        fetch(`/grabados/api/alta/buscar/?of=${encodeURIComponent(of)}`)
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'ok') {
                    busqueda = null;
                    mostrarAviso('alta-aviso-externo', 'aviso--error', res.message);
                    return;
                }
                busqueda = res;
                $('alta-of').value = res.of;

                mostrarAvisoExterno(res.externo);

                const selProceso = $('alta-proceso');
                selProceso.disabled = false;
                selProceso.value = procesoInicial || res.externo.proceso || '';
                procesoInicial = null;
                if (selProceso.value) {
                    mostrarAccion();
                } else {
                    selProceso.focus();
                }
            })
            .catch(() => {
                busqueda = null;
                mostrarAviso('alta-aviso-externo', 'aviso--error', 'Error de conexión al buscar la OF.');
            });
    }

    // "Se encontraron datos" solo si la API trae cliente y referencia: la OF
    // puede existir en CigarRings2012 con esos campos vacíos.
    function mostrarAvisoExterno(ext) {
        const faltantes = [['cliente', 'cliente'], ['referencia', 'referencia'], ['sobre', 'sobre']]
            .filter(([campo]) => !ext[campo]).map(([, etiqueta]) => etiqueta);

        if (!ext.encontrado) {
            mostrarAviso('alta-aviso-externo', 'aviso--advertencia',
                'No se encontró esta OF en el sistema externo. Si es un alta nueva, escribe el cliente y la referencia a mano.');
        } else if (!ext.completo) {
            mostrarAviso('alta-aviso-externo', 'aviso--advertencia',
                `La OF existe en el sistema externo, pero no tiene registrado: ${faltantes.join(', ')}. ` +
                'Si es un alta nueva, escribe a mano los datos que faltan.');
        } else if (faltantes.length) {
            mostrarAviso('alta-aviso-externo', 'aviso--ok',
                'Se encontraron el cliente y la referencia en el sistema externo (sin sobre registrado).');
        } else {
            mostrarAviso('alta-aviso-externo', 'aviso--ok', 'Se encontraron los datos de esta OF en el sistema externo.');
        }
    }

    // Llena cliente / referencia / sobre: del grabado si ya existe; si es un alta
    // nueva, de la API, y los que la API no trae quedan editables (cliente y
    // referencia obligatorios).
    function mostrarDatosGrabado(evaluacion) {
        const g = evaluacion.grabado;
        const ext = busqueda.externo;
        const esAltaNueva = !g && evaluacion.accion === 'ALTA';

        [['cliente', 'alta-cliente'], ['referencia', 'alta-referencia'], ['sobre', 'alta-sobre']]
            .forEach(([campo, id]) => {
                $(id).value = g ? g[campo] : ext[campo];
                $(id).readOnly = !(esAltaNueva && !ext[campo]);
            });
        $('alta-cliente-obl').style.display = $('alta-cliente').readOnly ? 'none' : 'inline';
        $('alta-referencia-obl').style.display = $('alta-referencia').readOnly ? 'none' : 'inline';
        $('alta-datos-grabado').style.display = '';  // vuelve al display: grid del CSS
    }

    function mostrarAccion() {
        ocultarAccion();
        const proceso = $('alta-proceso').value;
        if (!busqueda || !proceso) return;

        const evaluacion = busqueda.procesos[proceso];
        mostrarDatosGrabado(evaluacion);

        const g = evaluacion.grabado;
        const necesitaMaquina = evaluacion.accion === 'ALTA' || evaluacion.accion === 'RECHAZO_K1';

        if (evaluacion.accion === 'BLOQUEADA') {
            mostrarAviso('alta-aviso-accion', 'aviso--error', '⛔ ' + evaluacion.mensaje);
            if (evaluacion.bloqueo === 'NO_ES_ORIGEN' && evaluacion.of_origen_sugerida) {
                const btn = $('alta-btn-cargar-origen');
                btn.textContent = `Cargar la OF de origen ${evaluacion.of_origen_sugerida}`;
                btn.onclick = () => {
                    $('alta-of').value = evaluacion.of_origen_sugerida;
                    buscarOF();
                };
                btn.style.display = 'inline-block';
            }
            return;
        }

        if (necesitaMaquina && !busqueda.hay_maquinas) {
            mostrarAviso('alta-aviso-accion', 'aviso--error',
                '⚠️ No hay máquinas activas registradas: hay que cargarlas en el administrador antes de registrar el K1.');
            return;
        }

        if (evaluacion.accion === 'ALTA') {
            mostrarAviso('alta-aviso-accion', 'aviso--ok',
                `Alta nueva: se crea el grabado ${busqueda.of} ${proceso}, su fabricación inicial y el K1 (intento 1). Quedará Pendiente de K1.`);
        } else if (evaluacion.accion === 'RECHAZO_K1') {
            const siguiente = g.ultimo_intento_k1 + 1;
            const motivo = g.ultimo_motivo_rechazo ? ` Motivo del rechazo: "${g.ultimo_motivo_rechazo}".` : '';
            mostrarAviso('alta-aviso-accion', 'aviso--advertencia',
                `K1 rechazado (intento ${g.ultimo_intento_k1}).${motivo} Se registra una nueva fabricación y se abre el K1 intento ${siguiente}.`);
        } else if (evaluacion.accion === 'REPETICION') {
            mostrarAviso('alta-aviso-accion', 'aviso--info',
                'Grabado marcado para REPETIR: se registra la refabricación y vuelve directamente a Aprobado, sin K1.');
        }

        $('alta-maquina-caja').style.display = necesitaMaquina ? 'block' : 'none';
        $('alta-form-tecnico').style.display = 'block';
        $('alta-btn-guardar').style.display = 'inline-block';
        actualizarBanoAcumulado();
    }

    // ------------------------------------------- cálculos (igual que el PLANI)
    function actualizarBanoAcumulado() {
        const caja = $('alta-bano-acumulado-caja');
        const msg = $('alta-bano-acumulado-msg');
        fetch('/grabados/api/bano/')
            .then(r => r.json())
            .then(eb => {
                msg.innerText = `${eb.ml_acumulados.toFixed(0)} / ${eb.limite} ml`;
                caja.className = 'aviso ' + (eb.alerta ? 'aviso--error' : 'aviso--info');
            })
            .catch(() => { msg.innerText = '— / 2000 ml'; });
    }

    function calcularPerdida() {
        const pi = parseFloat($('alta-peso-i').value) || 0; // g
        const pf = parseFloat($('alta-peso-f').value) || 0; // g
        const perdida = Math.max(0, pi - pf);               // g
        $('alta-perdida-msg').innerText = perdida.toFixed(0) + ' g';

        // Fórmula histórica: Pérdida(g) / 1000 × 6.6 ml (misma que el servidor).
        const mlBano = (perdida / 1000) * 6.6;
        $('alta-bano-msg').innerText = mlBano.toFixed(0) + ' ml';

        compensacionRecomendada = Math.round(mlBano);
        $('alta-compensacion').value = mlBano > 0 ? compensacionRecomendada : '';
        verificarCambioCompensacion();
    }

    function compensacionCambiada() {
        const valorActual = parseInt($('alta-compensacion').value, 10) || 0;
        return valorActual !== compensacionRecomendada;
    }

    function verificarCambioCompensacion() {
        const cambiado = compensacionCambiada();
        $('alta-compensacion-motivo-caja').style.display = cambiado ? 'block' : 'none';
        if (!cambiado) $('alta-compensacion-motivo').value = '';
    }

    // ------------------------------------------------------------ guardado
    window.guardarAlta = function () {
        if (!busqueda) return;
        const proceso = $('alta-proceso').value;
        const evaluacion = busqueda.procesos[proceso];
        if (!evaluacion || evaluacion.accion === 'BLOQUEADA') return;
        const necesitaMaquina = evaluacion.accion !== 'REPETICION';

        const obligatorios = [
            ['alta-responsables', 'Responsables'], ['alta-peso-i', 'Peso inicial'],
            ['alta-peso-f', 'Peso final'], ['alta-temp', 'Temperatura'], ['alta-rpm', 'RPM'],
        ];
        if (necesitaMaquina) obligatorios.unshift(['alta-maquina', 'Máquina del K1']);
        if (!$('alta-referencia').readOnly) obligatorios.unshift(['alta-referencia', 'Referencia']);
        if (!$('alta-cliente').readOnly) obligatorios.unshift(['alta-cliente', 'Cliente']);
        const faltantes = obligatorios.filter(([id]) => !$(id).value.trim()).map(([, etiqueta]) => etiqueta);
        if (faltantes.length) { alert('Faltan campos obligatorios: ' + faltantes.join(', ') + '.'); return; }

        if (compensacionCambiada() && !$('alta-compensacion-motivo').value.trim()) {
            alert('Cambiaste la compensación del valor recomendado: explica el motivo antes de guardar.');
            return;
        }

        const payload = {
            of: busqueda.of,
            proceso: proceso,
            maquina_id: necesitaMaquina ? $('alta-maquina').value : null,
            cliente: $('alta-cliente').value.trim(),
            referencia: $('alta-referencia').value.trim(),
            sobre: $('alta-sobre').value.trim(),
            tecnicos: {
                responsables: $('alta-responsables').value.trim(),
                tiempo: $('alta-tiempo').value,
                peso_inicial: $('alta-peso-i').value,
                peso_final: $('alta-peso-f').value,
                temp: $('alta-temp').value,
                rpm: $('alta-rpm').value,
                compensacion: $('alta-compensacion').value,
                compensacion_motivo: $('alta-compensacion-motivo').value.trim(),
            },
        };

        const btn = $('alta-btn-guardar');
        btn.disabled = true;
        fetch('/grabados/api/alta/registrar/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
            body: JSON.stringify(payload),
        })
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'ok') { alert('Error: ' + res.message); return; }
                let mensaje = res.message;
                if (res.bano) {
                    mensaje += res.bano.alerta
                        ? `\n\n⚠️ Baño: ${res.bano.ml_acumulados} / ${res.bano.limite} ml. ¡Hay que preparar un baño nuevo! (confírmalo en la Tabla de Consultas)`
                        : `\n\nBaño: ${res.bano.ml_acumulados} / ${res.bano.limite} ml de compensación acumulados.`;
                }
                alert(mensaje);
                window.limpiarAlta();
            })
            .catch(() => alert('Error de conexión al guardar.'))
            .finally(() => { btn.disabled = false; });
    };

    // ------------------------------------------------------------- eventos
    $('alta-of').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); buscarOF(); }
    });
    $('alta-proceso').addEventListener('change', () => { limpiarTecnicos(); mostrarAccion(); });
    $('alta-peso-i').addEventListener('input', calcularPerdida);
    $('alta-peso-f').addEventListener('input', calcularPerdida);
    $('alta-compensacion').addEventListener('input', verificarCambioCompensacion);

    // Desde el PLANI ("Dar de alta" / "Refabricar"): /grabados/alta/?of=23304&proceso=STAMPING
    const parametros = new URLSearchParams(window.location.search);
    const ofInicial = (parametros.get('of') || '').trim();
    if (ofInicial) {
        const proceso = (parametros.get('proceso') || '').toUpperCase();
        procesoInicial = ['STAMPING', 'EMBOSSING'].includes(proceso) ? proceso : null;
        $('alta-of').value = ofInicial;
        buscarOF();
    }
})();
