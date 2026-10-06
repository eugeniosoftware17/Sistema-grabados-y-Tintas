/* ============================================================
   Crear Grabado — ver views_grabados.py y services/grabados.py
   Orden de la pantalla: 1) tipo de grabado, 2) OF y proceso, 3) datos
   técnicos. El servidor decide el caso (alta nueva / refabricación por K1
   rechazado / refabricación por REPETIR); acá solo se muestra.
   - Alta nueva: manda el tipo elegido. K1 queda Pendiente de K1 y pide
     máquina; Producción directa queda Aprobado, sin K1 ni máquina.
   - Si la OF ya tiene grabado, el selector de tipo se bloquea mostrando
     el tipo que ya tiene (en la refabricación no se puede cambiar).
   ============================================================ */

(function () {
    'use strict';

    let busqueda = null;               // última respuesta de api_alta_buscar
    let procesoInicial = null;         // ?proceso= de la URL (desde el PLANI), solo en la primera búsqueda
    let compensacionRecomendada = 0;
    let tipoUsuario = '';              // lo que eligió el usuario (se restaura al desbloquear el selector)

    const $ = (id) => document.getElementById(id);
    const radiosTipo = [...document.querySelectorAll('input[name="alta-tipo"]')];

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

    // Errores (de validación o del servidor) y confirmación del guardado, junto al botón Guardar.
    function avisoGuardado(clase, texto) {
        mostrarAviso('alta-aviso-guardado', clase, texto);
        $('alta-aviso-guardado').scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function evaluacionActual() {
        const proceso = $('alta-proceso').value;
        return busqueda && proceso ? busqueda.procesos[proceso] : null;
    }

    // ------------------------------------------------------ tipo de grabado
    function tipoElegido() {
        const marcado = radiosTipo.find(r => r.checked);
        return marcado ? marcado.value : '';
    }

    function marcarTipo(valor) {
        radiosTipo.forEach(r => { r.checked = r.value === valor; });
    }

    // Con grabado existente: selector bloqueado con su tipo y una nota. Sin
    // grabado: selector libre con lo que había elegido el usuario.
    function bloquearTipo(grabado) {
        const nota = $('alta-tipo-nota');
        if (grabado) {
            marcarTipo(grabado.tipo);   // LEGADO no tiene opción: quedan las dos sin marcar
            radiosTipo.forEach(r => { r.disabled = true; });
            nota.textContent = `Esta OF ya tiene un grabado de tipo «${grabado.tipo_display}»: ` +
                'se mantiene ese tipo y no se puede cambiar.';
            nota.style.display = 'block';
        } else {
            radiosTipo.forEach(r => { r.disabled = false; });
            marcarTipo(tipoUsuario);
            nota.style.display = 'none';
        }
    }

    // Máquina del K1: en la refabricación por K1 rechazado y en el alta nueva de tipo K1.
    function necesitaMaquina(evaluacion) {
        return evaluacion.accion === 'RECHAZO_K1' || (evaluacion.accion === 'ALTA' && tipoElegido() === 'K1');
    }

    // ------------------------------------------------------------ limpieza
    function limpiarTecnicos() {
        ['alta-responsables', 'alta-tiempo', 'alta-peso-i', 'alta-peso-f', 'alta-temp', 'alta-rpm',
         'alta-compensacion', 'alta-compensacion-motivo', 'alta-maquina']
            .forEach(id => { $(id).value = ''; });
        compensacionRecomendada = 0;
        $('alta-perdida-msg').innerText = '0 g';
        $('alta-bano-msg').innerText = '0.00 ml';
        ocultar('alta-compensacion-motivo-caja');
    }

    function ocultarAccion() {
        ['alta-aviso-accion', 'alta-btn-cargar-origen', 'alta-form-tecnico', 'alta-btn-guardar',
         'alta-aviso-guardado'].forEach(ocultar);
    }

    window.limpiarAlta = function () {
        busqueda = null;
        tipoUsuario = '';
        bloquearTipo(null);
        $('alta-of').value = '';
        $('alta-proceso').value = '';
        $('alta-proceso').disabled = true;
        ['alta-cliente', 'alta-referencia', 'alta-sobre'].forEach(id => { $(id).value = ''; });
        ocultar('alta-aviso-externo');
        ocultar('alta-datos-grabado');
        ocultarAccion();
        limpiarTecnicos();
        radiosTipo[0].focus();
    };

    // ---------------------------------------------------------- búsqueda
    function buscarOF() {
        const of = $('alta-of').value.trim();
        if (!of) {
            mostrarAviso('alta-aviso-externo', 'aviso--error', 'Ingresa un número de OF.');
            return;
        }

        busqueda = null;
        bloquearTipo(null);
        ocultar('alta-datos-grabado');
        ocultarAccion();
        limpiarTecnicos();
        mostrarAviso('alta-aviso-externo', 'aviso--info', 'Buscando datos de la OF...');

        pedirJson(`/grabados/api/alta/buscar/?of=${encodeURIComponent(of)}`)
            .then(res => {
                if (res.status !== 'ok') {
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
            });
    }

    // "Se encontraron datos" solo si la API trae cliente y referencia: la OF
    // puede existir en CigarRings2012 con esos campos vacíos.
    function mostrarAvisoExterno(ext) {
        const faltantes = [['cliente', 'cliente'], ['referencia', 'referencia'], ['sobre', 'sobre']]
            .filter(([campo]) => !ext[campo]).map(([, etiqueta]) => etiqueta);

        if (!ext.encontrado) {
            mostrarAviso('alta-aviso-externo', 'aviso--advertencia',
                'No se encontró esta OF en el sistema externo. Si es un grabado nuevo, escribe el cliente y la referencia a mano.');
        } else if (!ext.completo) {
            mostrarAviso('alta-aviso-externo', 'aviso--advertencia',
                `La OF existe en el sistema externo, pero no tiene registrado: ${faltantes.join(', ')}. ` +
                'Si es un grabado nuevo, escribe a mano los datos que faltan.');
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
        bloquearTipo(null);
        const evaluacion = evaluacionActual();
        if (!evaluacion) return;

        mostrarDatosGrabado(evaluacion);
        bloquearTipo(evaluacion.grabado);

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

        actualizarSegunTipo();
        actualizarBanoAcumulado();
    }

    // Aviso de lo que va a pasar, formulario, campo de máquina y botón Guardar
    // según el caso y (en el alta nueva) el tipo elegido. Se vuelve a llamar
    // cada vez que cambia el tipo.
    function actualizarSegunTipo() {
        const evaluacion = evaluacionActual();
        if (!evaluacion || evaluacion.accion === 'BLOQUEADA') return;
        const g = evaluacion.grabado;
        const proceso = $('alta-proceso').value;
        const tipo = tipoElegido();
        const conMaquina = necesitaMaquina(evaluacion);

        // Alta nueva sin tipo: no se muestran los datos técnicos hasta elegirlo.
        if (evaluacion.accion === 'ALTA' && !tipo) {
            mostrarAviso('alta-aviso-accion', 'aviso--advertencia',
                `Grabado nuevo ${busqueda.of} ${proceso}: elige arriba el tipo de grabado (K1 o Producción) para continuar.`);
            ocultar('alta-form-tecnico');
            ocultar('alta-btn-guardar');
            return;
        }

        $('alta-form-tecnico').style.display = 'block';
        $('alta-maquina-caja').style.display = conMaquina ? 'block' : 'none';
        if (!conMaquina) $('alta-maquina').value = '';
        $('alta-btn-guardar').style.display = 'inline-block';

        if (conMaquina && !busqueda.hay_maquinas) {
            mostrarAviso('alta-aviso-accion', 'aviso--error',
                '⚠️ No hay máquinas activas registradas: hay que cargarlas en el administrador antes de registrar el K1.');
            ocultar('alta-btn-guardar');
            return;
        }

        if (evaluacion.accion === 'ALTA' && tipo === 'K1') {
            mostrarAviso('alta-aviso-accion', 'aviso--ok',
                `Grabado nuevo K1: se crea el grabado ${busqueda.of} ${proceso}, su fabricación inicial y el K1 (intento 1). Quedará Pendiente de K1 hasta que un supervisor lo apruebe.`);
        } else if (evaluacion.accion === 'ALTA') {
            mostrarAviso('alta-aviso-accion', 'aviso--ok',
                `Grabado nuevo de producción: se crea el grabado ${busqueda.of} ${proceso} con su fabricación inicial y queda Aprobado al guardar, sin K1 ni máquina.`);
        } else if (evaluacion.accion === 'RECHAZO_K1') {
            const siguiente = g.ultimo_intento_k1 + 1;
            const motivo = g.ultimo_motivo_rechazo ? ` Motivo del rechazo: "${g.ultimo_motivo_rechazo}".` : '';
            mostrarAviso('alta-aviso-accion', 'aviso--advertencia',
                `K1 rechazado (intento ${g.ultimo_intento_k1}).${motivo} Se registra una nueva fabricación y se abre el K1 intento ${siguiente}.`);
        } else if (evaluacion.accion === 'REPETICION') {
            mostrarAviso('alta-aviso-accion', 'aviso--info',
                'Grabado marcado para REPETIR: se registra la refabricación y vuelve directamente a Aprobado, sin K1.');
        }
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
        // Con dos decimales: con pérdidas chicas (50 g -> 0.33 ml) el entero da 0.
        const mlBano = (perdida / 1000) * 6.6;
        $('alta-bano-msg').innerText = mlBano.toFixed(2) + ' ml';

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

    // ------------------------------------------------------- red / errores
    // Devuelve siempre {status, message, ...}: si el servidor responde algo que
    // no es JSON (500, CSRF 403, etc.) o no hay conexión, arma el mensaje.
    function pedirJson(url, opciones) {
        return fetch(url, opciones)
            .then(r => r.text().then(texto => {
                try {
                    return JSON.parse(texto);
                } catch (e) {
                    return { status: 'error', message: `Error del servidor (HTTP ${r.status}). Avísale al administrador.` };
                }
            }))
            .catch(() => ({ status: 'error', message: 'Error de conexión con el servidor. Revisa la red e inténtalo de nuevo.' }));
    }

    // ------------------------------------------------------------ guardado
    window.guardarAlta = function () {
        const evaluacion = evaluacionActual();
        if (!evaluacion || evaluacion.accion === 'BLOQUEADA') return;
        ocultar('alta-aviso-guardado');

        // El tipo solo se manda en el alta nueva; en las refabricaciones manda el del grabado.
        const tipo = evaluacion.accion === 'ALTA' ? tipoElegido() : null;
        if (evaluacion.accion === 'ALTA' && !tipo) {
            avisoGuardado('aviso--error', 'Selecciona el tipo de grabado: K1 (de prueba) o Producción (directo).');
            return;
        }
        const conMaquina = necesitaMaquina(evaluacion);

        const obligatorios = [
            ['alta-responsables', 'Responsables'], ['alta-peso-i', 'Peso inicial'],
            ['alta-peso-f', 'Peso final'], ['alta-temp', 'Temperatura'], ['alta-rpm', 'RPM'],
        ];
        if (conMaquina) obligatorios.unshift(['alta-maquina', 'Máquina del K1']);
        if (!$('alta-referencia').readOnly) obligatorios.unshift(['alta-referencia', 'Referencia']);
        if (!$('alta-cliente').readOnly) obligatorios.unshift(['alta-cliente', 'Cliente']);
        const faltantes = obligatorios.filter(([id]) => !$(id).value.trim()).map(([, etiqueta]) => etiqueta);
        if (faltantes.length) {
            avisoGuardado('aviso--error', 'Faltan campos obligatorios: ' + faltantes.join(', ') + '.');
            return;
        }

        if (compensacionCambiada() && !$('alta-compensacion-motivo').value.trim()) {
            avisoGuardado('aviso--error', 'Cambiaste la compensación del valor recomendado: explica el motivo antes de guardar.');
            return;
        }

        const payload = {
            of: busqueda.of,
            proceso: $('alta-proceso').value,
            tipo: tipo,
            maquina_id: conMaquina ? $('alta-maquina').value : null,
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
        pedirJson('/grabados/api/alta/registrar/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
            body: JSON.stringify(payload),
        })
            .then(res => {
                if (res.status !== 'ok') {
                    avisoGuardado('aviso--error', 'No se guardó: ' + (res.message || 'error desconocido del servidor.'));
                    return;
                }
                let mensaje = res.message;
                if (res.bano) {
                    mensaje += res.bano.alerta
                        ? ` ⚠️ Baño: ${res.bano.ml_acumulados} / ${res.bano.limite} ml. ¡Hay que preparar un baño nuevo! (confírmalo en la Tabla de Consultas)`
                        : ` Baño: ${res.bano.ml_acumulados} / ${res.bano.limite} ml de compensación acumulados.`;
                }
                window.limpiarAlta();
                avisoGuardado(res.bano && res.bano.alerta ? 'aviso--advertencia' : 'aviso--ok', '✅ ' + mensaje);
            })
            .finally(() => { btn.disabled = false; });
    };

    // ------------------------------------------------------------- eventos
    $('alta-of').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); buscarOF(); }
    });
    $('alta-proceso').addEventListener('change', () => { limpiarTecnicos(); mostrarAccion(); });
    radiosTipo.forEach(r => r.addEventListener('change', () => {
        if (!r.checked || r.disabled) return;
        tipoUsuario = r.value;
        ocultar('alta-aviso-guardado');
        actualizarSegunTipo();
    }));
    $('alta-peso-i').addEventListener('input', calcularPerdida);
    $('alta-peso-f').addEventListener('input', calcularPerdida);
    $('alta-compensacion').addEventListener('input', verificarCambioCompensacion);

    // Desde el PLANI ("Crear grabado" / "Refabricar"): /grabados/alta/?of=23304&proceso=STAMPING.
    // Llega sin tipo: si es un grabado nuevo, la pantalla pide elegirlo.
    const parametros = new URLSearchParams(window.location.search);
    const ofInicial = (parametros.get('of') || '').trim();
    if (ofInicial) {
        const proceso = (parametros.get('proceso') || '').toUpperCase();
        procesoInicial = ['STAMPING', 'EMBOSSING'].includes(proceso) ? proceso : null;
        $('alta-of').value = ofInicial;
        buscarOF();
    } else {
        radiosTipo[0].focus();
    }
})();
